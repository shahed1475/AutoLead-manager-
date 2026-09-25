"""Proof-backed messages: every drafted message carries the source link, the
date the fact was checked, and whether it was observed or inferred."""
import pytest

from backend.intelligence import marketing_agent as ma

pytestmark = pytest.mark.asyncio


def _pain(**over):
    base = {
        "title": "No visible online booking", "evidence_snippet": "cta_buttons=[]",
        "source_url": "https://alpha.example", "created_at": "2026-09-20 10:00:00",
        "confidence": 0.8, "severity": "high", "classification": "observed",
    }
    base.update(over)
    return base


async def _run(pain, monkeypatch):
    async def no_llm(*a, **k):
        return None                      # force the deterministic fragments
    monkeypatch.setattr(ma, "_generate_fragments_llm", no_llm)
    result = await ma.MarketingAgent().run(
        lead={"business_name": "Alpha Dental"}, company_profile={}, pain_points=[pain],
        opportunities=[], solutions=[], evidence=[],
    )
    return result.data["messages"]


async def test_observed_proof_is_attached_to_every_message(monkeypatch):
    msgs = await _run(_pain(), monkeypatch)
    assert msgs
    for m in msgs:
        assert m["evidence_url"] == "https://alpha.example"
        assert m["evidence_checked_at"] == "2026-09-20 10:00:00"
        assert m["evidence_kind"] == "observed"


async def test_inferred_pain_point_is_flagged(monkeypatch):
    msgs = await _run(_pain(classification="inferred", source_url=None), monkeypatch)
    assert {m["evidence_kind"] for m in msgs} == {"inferred"}
    assert all(m["evidence_url"] is None for m in msgs)


async def test_proof_columns_round_trip(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Alpha Dental"})
    await db.replace_generated_messages(lead_id, [{
        "channel": "EMAIL", "message": "Hi", "evidence": "cta_buttons=[]",
        "evidence_url": "https://alpha.example", "evidence_checked_at": "2026-09-20 10:00:00",
        "evidence_kind": "observed",
    }])
    [m] = await db.get_generated_messages(lead_id)
    assert (m["evidence_url"], m["evidence_checked_at"], m["evidence_kind"]) == (
        "https://alpha.example", "2026-09-20 10:00:00", "observed")
