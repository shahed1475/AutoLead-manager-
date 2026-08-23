import pytest

from backend.intelligence import opportunity_agent as oa_module
from backend.intelligence.opportunity_agent import OpportunityAgent

pytestmark = pytest.mark.asyncio


def _pain_point(**overrides):
    base = {
        "id": 1, "title": "No visible online appointment/booking system",
        "description": "No booking CTA found.",
        "confidence": 0.85, "severity": "high", "classification": "observed",
        "operational_impact": "Staff may need to handle routine scheduling requests manually.",
        "customer_impact": "Additional friction - customers must call to book.",
        "source_url": "https://acmedental.co",
    }
    base.update(overrides)
    return base


async def _identity_polish(sentence):
    return sentence


def _patch_no_llm(monkeypatch):
    monkeypatch.setattr(oa_module, "_polish_wording", _identity_polish)


async def test_no_pain_points_returns_empty_result():
    agent = OpportunityAgent()
    result = await agent.run({"id": 1, "niche": "dentist"}, {"industry": "Dental Care"}, [], [])
    assert result.status == "ok"
    assert result.data["opportunities"] == []
    assert result.confidence == 0.0


async def test_opportunity_derived_from_pain_points_for_dental_niche(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = OpportunityAgent()
    lead = {"id": 1, "niche": "dentist"}
    profile = {"industry": "Dental Care"}
    result = await agent.run(lead, profile, [_pain_point()], [])

    assert len(result.data["opportunities"]) == 1
    opp = result.data["opportunities"][0]
    assert opp["opportunity"] == "Appointment automation"
    assert opp["business_ease"] == "Make appointment scheduling easier"
    assert "scheduling requests manually" in opp["why_it_matters"]
    assert "customers must call to book" in opp["why_it_matters"]


async def test_priority_high_for_high_severity_observed_pain_point(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = OpportunityAgent()
    result = await agent.run(
        {"id": 1, "niche": "dentist"}, {"industry": "Dental Care"},
        [_pain_point(severity="high", classification="observed")], [],
    )
    assert result.data["opportunities"][0]["priority"] == "HIGH"


async def test_priority_medium_for_high_severity_inferred_pain_point(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = OpportunityAgent()
    result = await agent.run(
        {"id": 1, "niche": "dentist"}, {"industry": "Dental Care"},
        [_pain_point(severity="high", classification="inferred")], [],
    )
    assert result.data["opportunities"][0]["priority"] == "MEDIUM"


async def test_priority_low_for_low_severity_pain_point(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = OpportunityAgent()
    result = await agent.run(
        {"id": 1, "niche": "dentist"}, {"industry": "Dental Care"},
        [_pain_point(severity="low", classification="inferred")], [],
    )
    assert result.data["opportunities"][0]["priority"] == "LOW"


async def test_confidence_never_exceeds_source_pain_point_confidence(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = OpportunityAgent()
    result = await agent.run(
        {"id": 1, "niche": "dentist"}, {"industry": "Dental Care"},
        [_pain_point(confidence=0.42)], [],
    )
    assert result.data["opportunities"][0]["confidence"] == 0.42


async def test_multiple_pain_points_same_area_consolidate_into_one_opportunity(monkeypatch):
    """Avoids the Phase-1 artifact of one duplicate opportunity per pain point."""
    _patch_no_llm(monkeypatch)
    agent = OpportunityAgent()
    pain_points = [
        _pain_point(id=1, title="No SSL", confidence=0.5, severity="medium"),
        _pain_point(id=2, title="No visible online appointment/booking system", confidence=0.9, severity="high"),
    ]
    result = await agent.run({"id": 1, "niche": "dentist"}, {"industry": "Dental Care"}, pain_points, [])

    assert len(result.data["opportunities"]) == 1
    # Strongest (highest confidence) pain point drives the opportunity content.
    assert result.data["opportunities"][0]["pain_point_id"] == 2
    assert result.data["opportunities"][0]["confidence"] == 0.9


async def test_llm_polish_used_when_it_returns_a_reasonable_sentence(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=120):
        return "Customers currently must call in to book, adding friction to the process."

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    monkeypatch.setattr(oa_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(oa_module, "_ollama_cfg", fake_ollama_cfg)

    agent = OpportunityAgent()
    result = await agent.run({"id": 1, "niche": "dentist"}, {"industry": "Dental Care"}, [_pain_point()], [])
    assert result.data["opportunities"][0]["why_it_matters"] == (
        "Customers currently must call in to book, adding friction to the process."
    )


async def test_llm_failure_falls_back_to_heuristic_sentence(monkeypatch):
    async def fake_ollama_cfg():
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr(oa_module, "_ollama_cfg", fake_ollama_cfg)

    agent = OpportunityAgent()
    result = await agent.run({"id": 1, "niche": "dentist"}, {"industry": "Dental Care"}, [_pain_point()], [])
    opp = result.data["opportunities"][0]
    assert "scheduling requests manually" in opp["why_it_matters"]


async def test_llm_empty_response_falls_back_to_heuristic_sentence(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=120):
        return "   "

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    monkeypatch.setattr(oa_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(oa_module, "_ollama_cfg", fake_ollama_cfg)

    agent = OpportunityAgent()
    result = await agent.run({"id": 1, "niche": "dentist"}, {"industry": "Dental Care"}, [_pain_point()], [])
    opp = result.data["opportunities"][0]
    assert "scheduling requests manually" in opp["why_it_matters"]


async def test_llm_overlong_response_discarded_in_favor_of_heuristic(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=120):
        return "x" * 500

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    monkeypatch.setattr(oa_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(oa_module, "_ollama_cfg", fake_ollama_cfg)

    agent = OpportunityAgent()
    result = await agent.run({"id": 1, "niche": "dentist"}, {"industry": "Dental Care"}, [_pain_point()], [])
    opp = result.data["opportunities"][0]
    assert "scheduling requests manually" in opp["why_it_matters"]


async def test_why_it_matters_falls_back_to_description_when_no_impact_fields(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = OpportunityAgent()
    pp = _pain_point(operational_impact="", customer_impact="", description="Fallback description text.")
    result = await agent.run({"id": 1, "niche": "dentist"}, {"industry": "Dental Care"}, [pp], [])
    assert result.data["opportunities"][0]["why_it_matters"] == "Fallback description text."
