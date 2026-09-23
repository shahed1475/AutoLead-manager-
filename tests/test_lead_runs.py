"""Find leads runs: collect -> research -> draft, chained over one set of
leads. A run must never send a message."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend import database as db
from backend import email_sender, whatsapp_sender
from backend.lead_runs import runner
from backend.routers import lead_runs as lead_runs_router
from backend.routers import campaigns as campaigns_router

pytestmark = pytest.mark.asyncio


class _NeverCalled:
    def __getattr__(self, name):
        async def _fail(*args, **kwargs):
            raise AssertionError(f"sender.{name} must never be called by a Find leads run")
        return _fail


@pytest.fixture
def no_sending(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("a Find leads run must never send")
    monkeypatch.setattr(email_sender, "send_email", boom)
    monkeypatch.setattr(whatsapp_sender, "send_whatsapp", boom)
    monkeypatch.setattr(campaigns_router, "email_sender", _NeverCalled())
    monkeypatch.setattr(campaigns_router, "whatsapp_sender", _NeverCalled())


@pytest.fixture
def fake_drafting(monkeypatch):
    """Deterministic enrich/score + message generation (no network, no LLM)."""
    async def enrich_and_score(lead):
        label = "COLD" if "cold" in (lead.get("business_name") or "").lower() else "HOT"
        await db.update_lead(lead["id"], {"score": 80 if label == "HOT" else 10, "score_label": label})
        return dict(await db.get_lead_by_id(lead["id"]))

    async def generate(lead):
        return {"email_subject": f"Hi {lead['business_name']}", "email_body": "Body",
                "first_message": "WA", "follow_up_1": "F1", "follow_up_2": "F2", "follow_up_3": "F3"}
    monkeypatch.setattr(runner, "_enrich_and_score", enrich_and_score)
    monkeypatch.setattr(runner.ai_brain, "generate_all_messages", generate)


_phone = iter(range(5551234000, 5551239999))


async def _lead(name, **over):
    data = {"business_name": name, "email": f"{name.replace(' ', '').lower()}@x.test", "phone": str(next(_phone)),
            "website": f"https://{name.replace(' ', '').lower()}.test", "niche": "dentist", "city": "Dubai"}
    data.update(over)
    return await db.create_lead(data)


async def _run(steps, **over):
    data = {"niche": "dentist", "location": "Dubai", "target_count": 5, "steps": steps, "channel": "EMAIL"}
    data.update(over)
    return await db.create_lead_run(data)


def _stub_stages(monkeypatch, lead_ids, calls, session_id=7, researched=2):
    async def collect(run):
        calls.append("collect")
        return lead_ids

    async def start_research(ids, titles):
        calls.append(("research", tuple(ids), tuple(titles or ())))
        return session_id

    async def wait(run_id, sid):
        calls.append(("wait", sid))
        return researched
    monkeypatch.setattr(runner, "collect_leads", collect)
    monkeypatch.setattr(runner, "start_research", start_research)
    monkeypatch.setattr(runner, "wait_for_research", wait)


# ── Chaining ────────────────────────────────────────────────────────────

async def test_all_three_steps_chain_over_the_same_leads(clean_db, monkeypatch, no_sending, fake_drafting):
    ids = [await _lead("Alpha Dental"), await _lead("Beta Dental")]
    calls = []
    _stub_stages(monkeypatch, ids, calls)
    run_id = await _run(["collect", "research", "outreach"], channel="BOTH", target_titles=["Owner"])

    await runner.run_lead_run({"run_id": run_id})

    run = await db.get_lead_run(run_id)
    assert run["status"] == "COMPLETED" and run["stage"] == "DONE"
    assert calls == ["collect", ("research", tuple(ids), ("Owner",)), ("wait", 7)]
    assert run["lead_ids"] == ids and run["leads_found"] == 2 and run["leads_researched"] == 2
    assert run["research_session_id"] == 7
    assert run["drafts_written"] == 2 and run["drafts_skipped"] == 0
    for lid in ids:
        lead = await db.get_lead_by_id(lid)
        assert lead["ai_email_subject"].startswith("Hi ") and lead["channel"] == "BOTH"
        assert lead["status"] == "MESSAGES_READY"   # drafted and waiting for review — not sent


async def test_collect_only_skips_research_and_drafting(clean_db, monkeypatch, no_sending):
    ids = [await _lead("Alpha Dental")]
    calls = []
    _stub_stages(monkeypatch, ids, calls)
    run_id = await _run(["collect"])
    await runner.run_lead_run({"run_id": run_id})
    run = await db.get_lead_run(run_id)
    assert calls == ["collect"] and run["status"] == "COMPLETED"
    assert (await db.get_lead_by_id(ids[0]))["ai_email_subject"] in (None, "")


async def test_outreach_without_research(clean_db, monkeypatch, no_sending, fake_drafting):
    ids = [await _lead("Alpha Dental")]
    calls = []
    _stub_stages(monkeypatch, ids, calls)
    run_id = await _run(["collect", "outreach"])
    await runner.run_lead_run({"run_id": run_id})
    assert calls == ["collect"]
    assert (await db.get_lead_run(run_id))["drafts_written"] == 1


# ── Outreach safety ─────────────────────────────────────────────────────

async def test_drafting_skips_opted_out_cold_and_uncontactable_leads(clean_db, monkeypatch, no_sending, fake_drafting):
    ok = await _lead("Good Dental")
    dnc = await _lead("Opted Out Dental")
    await db.update_lead(dnc, {"status": "DO_NOT_CONTACT"})
    cold = await _lead("Cold Dental")
    no_email = await _lead("No Email Dental", email=None)
    sent = await _lead("Already Sent Dental")
    await db.update_lead(sent, {"status": "SENT", "ai_email_subject": "Old", "ai_email_body": "Old"})
    _stub_stages(monkeypatch, [ok, dnc, cold, no_email, sent], [])
    run_id = await _run(["collect", "outreach"], channel="EMAIL", hot_warm_only=True)

    await runner.run_lead_run({"run_id": run_id})

    run = await db.get_lead_run(run_id)
    assert run["drafts_written"] == 1 and run["drafts_skipped"] == 4
    assert (await db.get_lead_by_id(sent))["status"] == "SENT"   # already contacted: untouched
    assert (await db.get_lead_by_id(ok))["ai_email_subject"]
    for lid in (dnc, cold, no_email):
        assert not (await db.get_lead_by_id(lid))["ai_email_subject"]
    assert (await db.get_lead_by_id(dnc))["status"] == "DO_NOT_CONTACT"


async def test_existing_draft_is_kept_not_regenerated(clean_db, monkeypatch, no_sending, fake_drafting):
    lid = await _lead("Alpha Dental")
    await db.update_lead(lid, {"ai_email_subject": "Edited by me", "ai_email_body": "Mine"})
    _stub_stages(monkeypatch, [lid], [])
    run_id = await _run(["collect", "outreach"])
    await runner.run_lead_run({"run_id": run_id})
    assert (await db.get_lead_by_id(lid))["ai_email_subject"] == "Edited by me"


async def test_ai_lab_list_filter_accepts_several_statuses(clean_db):
    a = await _lead("Alpha Dental")
    b = await _lead("Beta Dental")
    c = await _lead("Gamma Dental")
    await db.update_lead(b, {"status": "MESSAGES_READY"})
    await db.update_lead(c, {"status": "SENT"})
    rows = await db.get_leads(status="PENDING,MESSAGES_READY", page_size=50)
    rows = rows["items"] if isinstance(rows, dict) else rows
    assert {r["id"] for r in rows} == {a, b}


# ── Cancel / failure / resume ───────────────────────────────────────────

async def test_cancel_after_collect_stops_before_research(clean_db, monkeypatch, no_sending):
    ids = [await _lead("Alpha Dental")]
    calls = []
    run_id = await _run(["collect", "research", "outreach"])

    async def collect(run):
        calls.append("collect")
        await db.update_lead_run(run_id, {"status": "CANCEL_REQUESTED"})
        return ids
    monkeypatch.setattr(runner, "collect_leads", collect)
    monkeypatch.setattr(runner, "start_research", lambda *a: calls.append("research"))

    await runner.run_lead_run({"run_id": run_id})
    run = await db.get_lead_run(run_id)
    assert run["status"] == "CANCELLED" and calls == ["collect"]
    assert run["lead_ids"] == ids   # what was collected is kept


async def test_collect_failure_marks_run_failed(clean_db, monkeypatch):
    async def collect(run):
        raise RuntimeError("Every source was blocked")
    monkeypatch.setattr(runner, "collect_leads", collect)
    run_id = await _run(["collect"])
    await runner.run_lead_run({"run_id": run_id})
    run = await db.get_lead_run(run_id)
    assert run["status"] == "FAILED" and "blocked" in run["error_message"]


async def test_resumed_run_continues_from_saved_progress(clean_db, monkeypatch, no_sending, fake_drafting):
    ids = [await _lead("Alpha Dental")]
    calls = []
    _stub_stages(monkeypatch, ids, calls, session_id=11)
    run_id = await _run(["collect", "research", "outreach"])
    await db.update_lead_run(run_id, {"status": "RUNNING", "stage": "RESEARCHING",
                                      "lead_ids": ids, "research_session_id": 11})

    class Q:
        def enqueue_nowait(self, kind, payload, handler):
            calls.append(("enqueued", kind, payload["run_id"]))
            return True
    assert await runner.reconcile_interrupted_runs(Q()) == 1
    await runner.run_lead_run({"run_id": run_id})

    assert calls == [("enqueued", "LEAD_RUN", run_id), ("wait", 11)]   # no re-collect, no new session
    run = await db.get_lead_run(run_id)
    assert run["status"] == "COMPLETED" and run["resume_count"] == 1 and run["drafts_written"] == 1


async def test_wait_for_research_returns_when_session_finishes(clean_db, monkeypatch):
    session_id = await db.create_research_session({"niche": "d", "location": "x", "target_count": 1})
    await db.update_research_session(session_id, {"status": "COMPLETED", "leads_completed": 1})
    run_id = await _run(["collect", "research"])
    assert await runner.wait_for_research(run_id, session_id) == 1


# ── API ─────────────────────────────────────────────────────────────────

class _FakeQueue:
    def __init__(self):
        self.jobs = []

    def enqueue_nowait(self, kind, payload, handler):
        self.jobs.append((kind, payload))
        return True


async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_start_endpoint_normalises_steps_and_enqueues(clean_db, monkeypatch):
    q = _FakeQueue()
    monkeypatch.setattr(lead_runs_router, "get_queue", lambda: q)
    async with await _client() as c:
        resp = await c.post("/api/lead-runs", json={
            "niche": "dentist", "location": "Dubai", "target_count": 999,
            "steps": ["outreach", "bogus", "research", "outreach"], "channel": "whatsapp",
            "target_titles": ["Owner", "owner"],
        })
        bad = await c.post("/api/lead-runs", json={"niche": " ", "location": "Dubai"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["steps"] == ["collect", "outreach", "research"]
    assert body["target_count"] == 100 and body["channel"] == "WHATSAPP"
    assert body["target_titles"] == ["Owner"] and body["status"] == "QUEUED"
    assert q.jobs == [("LEAD_RUN", {"run_id": body["id"]})]
    assert bad.status_code == 422


async def test_results_endpoint_returns_leads_drafts_and_decision_makers(clean_db, monkeypatch):
    lid = await _lead("Alpha Dental")
    await db.update_lead(lid, {"ai_email_subject": "Hi", "ai_email_body": "B"})
    session_id = await db.create_research_session({"niche": "d", "location": "x", "target_count": 1})
    await db.save_research_result(session_id, {"business_name": "Alpha Dental", "research_status": "COMPLETE"}, [],
                                  lead_id=lid, decision_makers=[{"name": "Amy Lin", "title": "Owner", "is_primary": True}])
    run_id = await _run(["collect", "research", "outreach"])
    await db.update_lead_run(run_id, {"lead_ids": [lid], "research_session_id": session_id, "status": "COMPLETED"})

    async with await _client() as c:
        data = (await c.get(f"/api/lead-runs/{run_id}/results")).json()
        cancel = (await c.post(f"/api/lead-runs/{run_id}/cancel")).json()
    lead = data["leads"][0]
    assert lead["business_name"] == "Alpha Dental" and lead["has_draft"] is True
    assert [d["name"] for d in lead["decision_makers"]] == ["Amy Lin"]
    assert cancel["status"] == "COMPLETED"   # finished runs can't be cancelled
