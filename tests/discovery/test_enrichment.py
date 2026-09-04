"""Phase 3 — post-discovery enrichment + scoring (discovery/enrichment.py)."""
import pytest

from backend import database as db
from backend.discovery import enrichment as enr

pytestmark = pytest.mark.asyncio


async def _lead(clean_db, **over):
    data = {"business_name": "Ent Co", "phone": "5550009999"}
    data.update(over)
    return await db.create_lead(data)


async def test_skipped_when_no_lead_ids(clean_db):
    out = await enr.enrich_and_score([])
    assert out["skipped"] is True


async def test_skipped_when_setting_off(clean_db):
    await db.upsert_setting("discovery_enrichment_enabled", "false")
    lid = await _lead(clean_db, website="https://x.example")
    out = await enr.enrich_and_score([lid])
    assert out["skipped"] is True


async def test_finds_email_for_website_only_lead(clean_db, monkeypatch):
    lid = await _lead(clean_db, website="https://acmedental.com", email=None)

    async def fake_finder(url, log_callback=None):
        return {"primary_email": "hi@acmedental.com", "all_emails": ["hi@acmedental.com"],
                "phone_numbers": [], "owner_name": None, "contact_page_url": None}

    monkeypatch.setattr(enr, "find_emails_from_website", fake_finder)
    monkeypatch.setattr(enr, "_run_website_ai", _noop)          # skip the LLM leg
    monkeypatch.setattr(enr, "score_lead", _noop_score)

    out = await enr.enrich_and_score([lid])
    lead = await db.get_lead_by_id(lid)
    assert lead["email"] == "hi@acmedental.com"
    assert lead["email_status"] == "FOUND"
    assert out["emails_found"] == 1


async def test_never_overwrites_verified_email(clean_db, monkeypatch):
    lid = await _lead(clean_db, website="https://acmedental.com",
                      email="real@acmedental.com", verified_email=1)

    async def fake_finder(url, log_callback=None):
        return {"primary_email": "spam@acmedental.com", "all_emails": [], "phone_numbers": [],
                "owner_name": None, "contact_page_url": None}

    monkeypatch.setattr(enr, "find_emails_from_website", fake_finder)
    monkeypatch.setattr(enr, "_run_website_ai", _noop)
    monkeypatch.setattr(enr, "score_lead", _noop_score)

    await enr.enrich_and_score([lid])
    assert (await db.get_lead_by_id(lid))["email"] == "real@acmedental.com"


async def test_scores_each_lead(clean_db, monkeypatch):
    lid = await _lead(clean_db)          # no website — email finder is skipped
    calls = []

    async def spy_score(lead, enriched=None, website_scores=None):
        calls.append(lead["id"])
        return {"final_score": 50, "category": "WARM"}

    monkeypatch.setattr(enr, "_run_website_ai", _noop)
    monkeypatch.setattr(enr, "score_lead", spy_score)

    out = await enr.enrich_and_score([lid])
    assert calls == [lid]
    assert out["scored"] == 1


async def test_never_raises_on_failure(clean_db, monkeypatch):
    lid = await _lead(clean_db, website="https://acmedental.com")

    async def boom(*a, **k):
        raise RuntimeError("enrichment blew up")

    monkeypatch.setattr(enr, "find_emails_from_website", boom)
    monkeypatch.setattr(enr, "_run_website_ai", boom)
    monkeypatch.setattr(enr, "score_lead", boom)

    out = await enr.enrich_and_score([lid])   # must not raise
    assert "scored" in out


async def _noop(*a, **k):
    return None


async def _noop_score(*a, **k):
    return {"final_score": 0, "category": "COLD"}
