"""Phase 5 — persistent automatic research handoff after discovery enrichment."""
import pytest

from backend import database as db
from backend.discovery import enrichment as enr

pytestmark = pytest.mark.asyncio


async def _lead(score=0, **over):
    d = {"business_name": "AR Co", "phone": "5550050000", "niche": "dental clinic", "city": "Reno"}
    d.update(over)
    lid = await db.create_lead(d)
    if score:
        await db.update_lead(lid, {"score": score, "score_label": "HOT" if score >= 70 else "WARM"})
    return lid


async def test_no_handoff_when_mode_manual(clean_db, monkeypatch):
    calls = []
    monkeypatch.setattr(enr, "_handoff_leads", lambda q, ids, **k: calls.append(ids))
    a = await _lead(score=90)
    await enr.maybe_auto_handoff([a])
    assert calls == []


async def test_handoff_queues_high_score_leads(clean_db, monkeypatch):
    await db.upsert_setting("research_handoff_mode", "automatic")
    captured = {}

    async def fake_handoff(queue, lead_ids, **kw):
        captured["ids"] = list(lead_ids)
        return {"session_id": 1, "queued": len(lead_ids), "skipped": []}

    monkeypatch.setattr(enr, "_handoff_leads", fake_handoff)
    monkeypatch.setattr(enr, "get_queue", lambda: object())

    hi = await _lead(score=85)
    lo = await _lead(score=30, phone="5550050001")
    out = await enr.maybe_auto_handoff([hi, lo])
    assert captured["ids"] == [hi]
    assert out["auto_queued"] == 1


async def test_handoff_skips_excluded_and_already_researched(clean_db, monkeypatch):
    await db.upsert_setting("research_handoff_mode", "automatic")
    captured = {}

    async def fake_handoff(queue, lead_ids, **kw):
        captured["ids"] = list(lead_ids)
        return {"session_id": 1, "queued": len(lead_ids), "skipped": []}

    monkeypatch.setattr(enr, "_handoff_leads", fake_handoff)
    monkeypatch.setattr(enr, "get_queue", lambda: object())

    ok = await _lead(score=80)
    excl = await _lead(score=80, phone="5550050002", excluded_from_research=1)
    done = await _lead(score=80, phone="5550050003")
    await db.update_lead(done, {"research_status": "COMPLETED"})

    await enr.maybe_auto_handoff([ok, excl, done])
    assert captured["ids"] == [ok]


async def test_auto_handoff_writes_one_activity_log_line(clean_db, monkeypatch):
    dc = clean_db
    await dc.upsert_setting("research_handoff_mode", "automatic")

    async def fake_handoff(queue, lead_ids, **kw):
        return {"session_id": 9, "queued": len(lead_ids), "skipped": []}

    monkeypatch.setattr(enr, "_handoff_leads", fake_handoff)
    monkeypatch.setattr(enr, "get_queue", lambda: object())

    a = await _lead(score=90)
    b = await _lead(score=88, phone="5550050777")
    lines_before = len(await dc.get_automation_log(limit=100))
    await enr.maybe_auto_handoff([a, b], log_fn=dc.append_automation_log)
    lines = await dc.get_automation_log(limit=100)
    assert len(lines) == lines_before + 1
    assert "Research: queued 2" in lines[0]["message"]


async def test_handoff_respects_per_batch_cap(clean_db, monkeypatch):
    await db.upsert_setting("research_handoff_mode", "automatic")
    await db.upsert_setting("research_handoff_max_per_batch", "2")
    captured = {}

    async def fake_handoff(queue, lead_ids, **kw):
        captured["ids"] = list(lead_ids)
        return {"session_id": 1, "queued": len(lead_ids), "skipped": []}

    monkeypatch.setattr(enr, "_handoff_leads", fake_handoff)
    monkeypatch.setattr(enr, "get_queue", lambda: object())

    ids = [await _lead(score=90, phone=f"555005{i:04d}") for i in range(5)]
    await enr.maybe_auto_handoff(ids)
    assert len(captured["ids"]) == 2
