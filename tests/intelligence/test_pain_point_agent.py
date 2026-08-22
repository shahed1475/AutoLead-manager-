import pytest

from backend.intelligence import pain_point_agent as ppa_module
from backend.intelligence.pain_point_agent import PainPointAgent

pytestmark = pytest.mark.asyncio


def _fake_website_data(**overrides):
    base = {
        "url": "https://acmedental.co", "has_ssl": True, "error": None,
        "page_title": "Acme Dental — Home",
        "body_text": "Acme Dental offers general and cosmetic dentistry services.",
        "cta_buttons": ["Book Now"],
        "social_media_links": ["facebook", "instagram"],
        "has_contact_form": True, "has_phone_on_page": True, "has_email_on_page": True,
    }
    base.update(overrides)
    return base


def _profile(**overrides):
    base = {"id": 1, "lead_id": 1, "industry": "Dental Care", "status": "DONE"}
    base.update(overrides)
    return base


async def _no_llm_findings(*args, **kwargs):
    return []


async def test_no_website_returns_empty_result_no_llm_call():
    agent = PainPointAgent()
    result = await agent.run({"id": 1, "business_name": "No Site Co", "website": None}, _profile(), [])
    assert result.status == "ok"
    assert result.data["pain_points"] == []
    assert result.data["business_opportunities"] == []
    assert result.confidence == 0.0


async def test_website_fetch_error_returns_empty_result(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data(error="timeout")
    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_infer_llm_pain_points", _no_llm_findings)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co"}
    result = await agent.run(lead, _profile(), [])

    assert result.status == "ok"
    assert result.data["pain_points"] == []


async def test_heuristic_no_contact_method_observed(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data(has_contact_form=False, has_phone_on_page=False, has_email_on_page=False)
    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_infer_llm_pain_points", _no_llm_findings)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co", "niche": "dentist"}
    result = await agent.run(lead, _profile(), [])

    titles = [pp["title"] for pp in result.data["pain_points"]]
    assert "No easy way for customers to reach the business online" in titles
    contact_pp = next(pp for pp in result.data["pain_points"] if "reach the business" in pp["title"])
    assert contact_pp["classification"] == "observed"
    assert contact_pp["evidence_snippet"]
    assert contact_pp["source_url"] == "https://acmedental.co"
    assert 0.0 <= contact_pp["confidence"] <= 1.0


async def test_heuristic_no_booking_system_for_booking_niche(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data(cta_buttons=[], body_text="Welcome to our clinic.")
    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_infer_llm_pain_points", _no_llm_findings)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co", "niche": "dentist"}
    result = await agent.run(lead, _profile(industry="Dental Care"), [])

    titles = [pp["title"] for pp in result.data["pain_points"]]
    assert "No visible online appointment/booking system" in titles


async def test_no_booking_pain_point_when_cta_present(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data(cta_buttons=["Book an appointment"])
    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_infer_llm_pain_points", _no_llm_findings)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co", "niche": "dentist"}
    result = await agent.run(lead, _profile(industry="Dental Care"), [])

    titles = [pp["title"] for pp in result.data["pain_points"]]
    assert "No visible online appointment/booking system" not in titles


async def test_no_booking_check_for_non_booking_niche(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data(cta_buttons=[])
    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_infer_llm_pain_points", _no_llm_findings)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Software", "website": "https://acmesoftware.co", "niche": "software"}
    result = await agent.run(lead, _profile(industry="Software"), [])

    titles = [pp["title"] for pp in result.data["pain_points"]]
    assert "No visible online appointment/booking system" not in titles


async def test_heuristic_no_ssl(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data(has_ssl=False)
    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_infer_llm_pain_points", _no_llm_findings)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co"}
    result = await agent.run(lead, _profile(), [])

    titles = [pp["title"] for pp in result.data["pain_points"]]
    assert "Website is not served over HTTPS" in titles


async def test_llm_inferred_pain_point_without_evidence_is_dropped(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data()

    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=500):
        # No "evidence" field at all — must be dropped, never shown as a pain point.
        return '{"pain_points": [{"title": "Unfounded claim", "severity": "high"}]}'

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(ppa_module, "_ollama_cfg", fake_ollama_cfg)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co"}
    result = await agent.run(lead, _profile(), [])

    titles = [pp["title"] for pp in result.data["pain_points"]]
    assert "Unfounded claim" not in titles


async def test_llm_inferred_pain_point_with_evidence_is_kept_and_classified_inferred(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data()

    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=500):
        return '''{"pain_points": [{"title": "Outdated blog", "evidence": "Last post 2019",
                   "severity": "low", "operational_impact": "Content team may be under-resourced.",
                   "customer_impact": "Visitors may assume the business is inactive."}]}'''

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(ppa_module, "_ollama_cfg", fake_ollama_cfg)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co"}
    result = await agent.run(lead, _profile(), [])

    matches = [pp for pp in result.data["pain_points"] if pp["title"] == "Outdated blog"]
    assert len(matches) == 1
    assert matches[0]["classification"] == "inferred"
    assert matches[0]["evidence_snippet"] == "Last post 2019"
    assert matches[0]["severity"] == "low"


async def test_llm_invalid_severity_is_clamped_to_medium(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data()

    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=500):
        return '{"pain_points": [{"title": "X", "evidence": "Y", "severity": "catastrophic"}]}'

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(ppa_module, "_ollama_cfg", fake_ollama_cfg)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co"}
    result = await agent.run(lead, _profile(), [])

    match = next(pp for pp in result.data["pain_points"] if pp["title"] == "X")
    assert match["severity"] == "medium"


async def test_llm_failure_degrades_gracefully_to_heuristics_only(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data(has_contact_form=False, has_phone_on_page=False, has_email_on_page=False)

    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=500):
        raise RuntimeError("model unavailable")

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(ppa_module, "_ollama_cfg", fake_ollama_cfg)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co"}
    result = await agent.run(lead, _profile(), [])

    assert result.status == "ok"
    assert len(result.data["pain_points"]) >= 1  # heuristic findings still present


async def test_business_opportunities_derived_from_pain_points_never_exceed_source_confidence(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data(has_contact_form=False, has_phone_on_page=False, has_email_on_page=False)
    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_infer_llm_pain_points", _no_llm_findings)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co", "niche": "dentist"}
    result = await agent.run(lead, _profile(industry="Dental Care"), [])

    assert len(result.data["business_opportunities"]) == len(result.data["pain_points"])
    for opp, pp in zip(result.data["business_opportunities"], result.data["pain_points"]):
        assert opp["confidence"] <= pp["confidence"]
        assert opp["area"] == "Appointment Scheduling"


async def test_no_pain_points_means_no_opportunities(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data()  # everything present, nothing wrong
    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_infer_llm_pain_points", _no_llm_findings)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co", "niche": "dentist"}
    result = await agent.run(lead, _profile(industry="Dental Care"), [])

    assert result.data["pain_points"] == []
    assert result.data["business_opportunities"] == []


async def test_confidence_is_max_of_pain_point_confidences(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data(has_contact_form=False, has_phone_on_page=False, has_email_on_page=False, has_ssl=False)
    monkeypatch.setattr(ppa_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(ppa_module, "_infer_llm_pain_points", _no_llm_findings)

    agent = PainPointAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co"}
    result = await agent.run(lead, _profile(), [])

    expected_max = max(pp["confidence"] for pp in result.data["pain_points"])
    assert result.confidence == expected_max
