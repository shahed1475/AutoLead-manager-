import pytest

from backend.intelligence import company_research_agent as cra_module
from backend.intelligence.company_research_agent import CompanyResearchAgent

pytestmark = pytest.mark.asyncio


def _fake_website_data(**overrides):
    base = {
        "url": "https://acmedental.co", "has_ssl": True, "error": None,
        "page_title": "Acme Dental — Home", "meta_description": "Best dental care in town",
        "all_headings": ["Welcome to Acme Dental", "Our Services"],
        "body_text": "Acme Dental offers general and cosmetic dentistry services.",
        "word_count": 50, "image_count": 3,
        "has_contact_form": True, "has_phone_on_page": True, "has_email_on_page": True,
        "cta_buttons": ["Book Now"], "social_media_links": ["facebook", "instagram"],
        "has_social_links": True, "page_text": "Acme Dental offers general and cosmetic dentistry.",
        "raw_html": '<html><div class="wp-content">x</div></html>',
    }
    base.update(overrides)
    return base


async def test_research_with_no_website_returns_low_confidence_ok():
    agent = CompanyResearchAgent()
    result = await agent.run({"id": 1, "business_name": "No Site Co", "website": None}, None)
    assert result.status == "ok"
    assert result.confidence < 0.5
    assert result.evidence == []


async def test_research_success_path(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data()

    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '''{"industry": "Dental Care", "services": ["general dentistry", "cosmetic dentistry"],
                   "products": [], "company_description": "A dental clinic offering general and cosmetic care.",
                   "company_size_estimate": "small", "maturity_estimate": "established"}'''

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    async def fake_enrich_lead_with_ai(lead, website_data, company_dna):
        return {}

    monkeypatch.setattr(cra_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(cra_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(cra_module, "_ollama_cfg", fake_ollama_cfg)
    monkeypatch.setattr(cra_module, "enrich_lead_with_ai", fake_enrich_lead_with_ai)
    monkeypatch.setattr(cra_module, "_load_company_dna", lambda: "We build CRMs.")

    agent = CompanyResearchAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co", "niche": "dentist"}
    result = await agent.run(lead, None)

    assert result.status == "ok"
    assert result.confidence == 1.0
    assert result.data["industry"] == "Dental Care"
    assert "WordPress" in result.data["tech_stack"]

    field_names = {e.field_name for e in result.evidence}
    assert "industry" in field_names
    assert "tech_stack" in field_names
    industry_evidence = [e for e in result.evidence if e.field_name == "industry"][0]
    assert industry_evidence.source_type == "ai_inference"
    tech_evidence = [e for e in result.evidence if e.field_name == "tech_stack"][0]
    assert tech_evidence.source_type == "heuristic"


async def test_research_degrades_gracefully_on_llm_failure(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data()

    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        raise RuntimeError("model unavailable")

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    async def fake_enrich_lead_with_ai(lead, website_data, company_dna):
        return {}

    monkeypatch.setattr(cra_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(cra_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(cra_module, "_ollama_cfg", fake_ollama_cfg)
    monkeypatch.setattr(cra_module, "enrich_lead_with_ai", fake_enrich_lead_with_ai)
    monkeypatch.setattr(cra_module, "_load_company_dna", lambda: "We build CRMs.")

    agent = CompanyResearchAgent()
    lead = {"id": 2, "business_name": "Acme Dental", "website": "https://acmedental.co"}
    result = await agent.run(lead, None)

    assert result.status == "ok"
    assert result.confidence == 0.6  # heuristic-only, no AI fields
    assert "industry" not in result.data


async def test_research_handles_website_fetch_error(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data(error="timeout", body_text="", raw_html="")

    monkeypatch.setattr(cra_module, "analyze_website", fake_analyze_website)

    agent = CompanyResearchAgent()
    lead = {"id": 3, "business_name": "Acme Dental", "website": "https://acmedental.co"}
    result = await agent.run(lead, None)

    assert result.status == "ok"
    assert result.confidence == 0.2
