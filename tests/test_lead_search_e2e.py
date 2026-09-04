"""Phase 11 — end-to-end: search → dedup → enrich → score → auto research handoff.

Externals (scrapers, email_finder, website AI, LLM scorer, research browser) are
mocked; the wiring between every stage is real.
"""
import pytest

from backend import database as db
from backend.discovery import enrichment as enr
from backend.discovery import quick_search as qs
from backend.discovery.adapters import AdapterResult

pytestmark = pytest.mark.asyncio


class _Adapter:
    enabled = True


class _Registry:
    def get(self, name):
        return _Adapter() if name == "GOOGLE_MAPS" else None

    async def execute(self, name, niche, city, country, budget, cfg, log_fn=None):
        return AdapterResult(source=name, leads=[
            {"business_name": "Bright Smiles Dental", "phone": "5551110001",
             "website": "https://brightsmilesdental.com", "city": city, "source": "GOOGLE_MAPS"},
            {"business_name": "City Orthodontics", "phone": "5551110002",
             "website": "https://cityortho.com", "city": city, "source": "GOOGLE_MAPS"},
        ])


class _Plan:
    intent = "LOCAL_BUSINESS"
    confidence = 0.9
    recommended_sources = ["GOOGLE_MAPS"]
    query_variants = ["dental clinic"]


class _Planner:
    async def plan(self, **kw):
        return _Plan()


class _Queue:
    def __init__(self):
        self.jobs = []

    def enqueue_nowait(self, jt, payload, handler):
        self.jobs.append(jt)
        return True


async def _noop(*a, **k):
    return None


async def test_full_pipeline_search_to_research(clean_db, monkeypatch):
    # ── settings: enrichment on, automatic research handoff on ──
    await db.upsert_setting("discovery_enrichment_enabled", "true")
    await db.upsert_setting("research_handoff_mode", "automatic")
    await db.upsert_setting("research_handoff_min_score", "50")

    # ── stage 1-2: discovery ──
    monkeypatch.setattr(qs, "DiscoveryPlanner", lambda: _Planner())
    monkeypatch.setattr(qs, "get_registry", lambda: _Registry())
    monkeypatch.setattr(qs, "_scraper_cfg", lambda: _await({}))

    # ── stage 3: enrichment — email finder finds one, website AI noop ──
    async def fake_finder(url, log_callback=None):
        found = "owner@brightsmilesdental.com" if "bright" in url else None
        return {"primary_email": found, "all_emails": [], "phone_numbers": [],
                "owner_name": None, "contact_page_url": None}

    monkeypatch.setattr(enr, "find_emails_from_website", fake_finder)
    monkeypatch.setattr(enr, "_run_website_ai", _noop)

    async def fake_score(lead, enriched=None, website_scores=None):
        # Bright Smiles scores high, City Ortho low
        s = 80 if "Bright" in lead["business_name"] else 40
        await db.update_lead(lead["id"], {"score": s, "score_label": "HOT" if s >= 70 else "COLD"})
        return {"final_score": s, "category": "HOT" if s >= 70 else "COLD"}

    monkeypatch.setattr(enr, "score_lead", fake_score)

    # ── stage: research handoff — mock the browser session away ──
    handoff_calls = {}

    async def fake_handoff(queue, lead_ids, **kw):
        handoff_calls["ids"] = list(lead_ids)
        sid = await db.create_research_session({
            "niche": "dental clinic", "location": "Reno", "target_count": len(lead_ids),
            "mode": "handoff", "seed_lead_ids": list(lead_ids),
        })
        await db.set_leads_research_status(list(lead_ids), "QUEUED", session_id=sid)
        return {"session_id": sid, "queued": len(lead_ids), "skipped": []}

    monkeypatch.setattr(enr, "_handoff_leads", fake_handoff)
    monkeypatch.setattr(enr, "get_queue", lambda: _Queue())

    # ── run the real Quick Search job ──
    run_id = await db.create_discovery_run({
        "mode": "QUICK", "raw_query": "dental clinic", "niche": "dental clinic",
        "city": "Reno", "country": "", "target_count": 10,
    })
    await qs.run_quick_search({"run_id": run_id})

    # ── assert every stage landed ──
    run = await db.get_discovery_run(run_id)
    assert run["status"] == "COMPLETED"
    assert run["results_count"] == 2

    leads = await db.get_leads_for_discovery_run(run_id)
    by_name = {l["business_name"]: l for l in leads}
    assert by_name["Bright Smiles Dental"]["email"] == "owner@brightsmilesdental.com"   # enrichment
    assert by_name["Bright Smiles Dental"]["email_status"] == "FOUND"
    assert by_name["Bright Smiles Dental"]["score"] == 80                               # scoring
    assert by_name["City Orthodontics"]["score"] == 40

    # only the high-score lead was auto-handed to research
    assert handoff_calls["ids"] == [by_name["Bright Smiles Dental"]["id"]]
    assert by_name["Bright Smiles Dental"]["research_status"] == "QUEUED"
    assert by_name["City Orthodontics"]["research_status"] == "NOT_STARTED"


async def _await(v):
    return v
