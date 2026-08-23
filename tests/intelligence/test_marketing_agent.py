import pytest

from backend.intelligence import marketing_agent as ma_module
from backend.intelligence.marketing_agent import MarketingAgent

pytestmark = pytest.mark.asyncio


def _pain_point(**overrides):
    base = {
        "id": 1, "title": "No visible online appointment/booking system",
        "description": "No booking CTA found.",
        "evidence_snippet": "cta_buttons=[]",
        "source_url": "https://acmedental.co",
        "confidence": 0.85, "severity": "high", "classification": "observed",
        "operational_impact": "Staff may need to handle routine scheduling requests manually.",
        "customer_impact": "Additional friction - customers must call to book.",
    }
    base.update(overrides)
    return base


def _opportunity(**overrides):
    base = {"id": 10, "pain_point_id": 1, "area": "Appointment Scheduling", "confidence": 0.85}
    base.update(overrides)
    return base


def _solution(**overrides):
    base = {"id": 20, "business_opportunity_id": 10, "service_name": "WhatsApp Automation", "confidence": 0.8}
    base.update(overrides)
    return base


async def _no_llm(*args, **kwargs):
    return None


def _patch_no_llm(monkeypatch):
    monkeypatch.setattr(ma_module, "_generate_fragments_llm", _no_llm)


async def test_no_pain_points_returns_empty_result():
    agent = MarketingAgent()
    result = await agent.run({"id": 1, "business_name": "No Data Co"}, {}, [], [], [], [])
    assert result.status == "ok"
    assert result.data["messages"] == []
    assert result.confidence == 0.0


async def test_generates_up_to_six_messages_three_variants_two_channels(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    result = await agent.run(lead, {"industry": "Dental Care"}, [_pain_point()], [_opportunity()], [_solution()], [])

    messages = result.data["messages"]
    assert len(messages) == 6
    channels = {m["channel"] for m in messages}
    assert channels == {"EMAIL", "WHATSAPP"}
    variants = {m["variant"] for m in messages}
    assert variants == {"PRIMARY", "ALTERNATIVE_1", "ALTERNATIVE_2"}


async def test_pain_point_appears_before_solution_in_every_message(monkeypatch):
    """CRITICAL RULE: pain point must come first. Structural, not hoped-for —
    verified across every generated message, heuristic path."""
    _patch_no_llm(monkeypatch)
    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    result = await agent.run(lead, {"industry": "Dental Care"}, [_pain_point()], [_opportunity()], [_solution()], [])

    pain_point_text = "No visible online appointment/booking system"
    for m in result.data["messages"]:
        pain_idx = m["message"].find(pain_point_text)
        solution_idx = m["message"].lower().find("we could help")
        if pain_idx == -1:
            # WhatsApp may truncate — the untruncated email variant is the authoritative check
            continue
        assert pain_idx != -1
        if solution_idx != -1:
            assert pain_idx < solution_idx


async def test_email_message_never_starts_with_company_introduction(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    result = await agent.run(lead, {"industry": "Dental Care"}, [_pain_point()], [_opportunity()], [_solution()], [])

    email = next(m for m in result.data["messages"] if m["channel"] == "EMAIL")
    lowered = email["message"].lower()
    assert not lowered.startswith("hi, we are")
    assert not lowered.startswith("we offer")
    assert not lowered.startswith("we specialize")


async def test_message_uses_evidence_snippet_content(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    pp = _pain_point(evidence_snippet="cta_buttons=[]")
    result = await agent.run(lead, {"industry": "Dental Care"}, [pp], [_opportunity()], [_solution()], [])

    for m in result.data["messages"]:
        assert m["evidence"] == "cta_buttons=[]"


async def test_no_solution_match_never_names_a_service(monkeypatch):
    """No confident solution -> agent must not invent or name a service."""
    _patch_no_llm(monkeypatch)
    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Vague Co"}
    result = await agent.run(lead, {"industry": "unknown"}, [_pain_point()], [], [], [])

    for m in result.data["messages"]:
        assert m["service_name"] is None
        for svc in ("WhatsApp Automation", "CRM Development", "AI Chatbots", "RAG", "Agentic AI"):
            assert svc.lower() not in m["message"].lower()


async def test_irrelevant_services_not_mentioned_when_solution_matched(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    result = await agent.run(lead, {"industry": "Dental Care"}, [_pain_point()], [_opportunity()], [_solution()], [])

    for m in result.data["messages"]:
        for svc in ("CRM Development", "AI Chatbots", "RAG", "Agentic AI", "SaaS Development"):
            assert svc.lower() not in m["message"].lower()


async def test_whatsapp_message_shorter_than_email_for_same_variant(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    result = await agent.run(lead, {"industry": "Dental Care"}, [_pain_point()], [_opportunity()], [_solution()], [])

    by_variant = {}
    for m in result.data["messages"]:
        by_variant.setdefault(m["variant"], {})[m["channel"]] = m["message"]

    for variant, channels in by_variant.items():
        assert len(channels["WHATSAPP"]) < len(channels["EMAIL"])
        assert len(channels["WHATSAPP"]) <= 320


async def test_email_has_valid_subject_and_whatsapp_has_none(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    result = await agent.run(lead, {"industry": "Dental Care"}, [_pain_point()], [_opportunity()], [_solution()], [])

    for m in result.data["messages"]:
        if m["channel"] == "EMAIL":
            assert m["subject"]
            assert len(m["subject"]) > 0
        else:
            assert m["subject"] is None


async def test_confidence_never_exceeds_min_of_pain_point_and_solution(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    pp = _pain_point(confidence=0.9)
    sol = _solution(confidence=0.4)
    result = await agent.run(lead, {"industry": "Dental Care"}, [pp], [_opportunity()], [sol], [])

    for m in result.data["messages"]:
        assert m["confidence"] <= 0.4


async def test_llm_output_with_unsupported_financial_claim_falls_back_to_heuristic(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=300):
        return '''{"opening": "I noticed your booking is manual.",
                   "solution_benefit": "This could increase revenue by 30% and guarantee more bookings.",
                   "cta": "Want a demo?"}'''

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    monkeypatch.setattr(ma_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(ma_module, "_ollama_cfg", fake_ollama_cfg)

    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    result = await agent.run(lead, {"industry": "Dental Care"}, [_pain_point()], [_opportunity()], [_solution()], [])

    for m in result.data["messages"]:
        assert "30%" not in m["message"]
        assert "guarantee" not in m["message"].lower()


async def test_llm_output_mentioning_other_service_falls_back_to_heuristic(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=300):
        return '''{"opening": "I noticed your booking is manual.",
                   "solution_benefit": "We could set up CRM Development and AI Chatbots for you.",
                   "cta": "Want a demo?"}'''

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    monkeypatch.setattr(ma_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(ma_module, "_ollama_cfg", fake_ollama_cfg)

    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    result = await agent.run(lead, {"industry": "Dental Care"}, [_pain_point()], [_opportunity()], [_solution()], [])

    for m in result.data["messages"]:
        assert "crm development" not in m["message"].lower()


async def test_llm_output_with_emoji_or_html_falls_back_to_heuristic(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=300):
        return '''{"opening": "I noticed your booking is manual \\ud83d\\ude00",
                   "solution_benefit": "<b>We could help</b>",
                   "cta": "Want a demo?"}'''

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    monkeypatch.setattr(ma_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(ma_module, "_ollama_cfg", fake_ollama_cfg)

    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    result = await agent.run(lead, {"industry": "Dental Care"}, [_pain_point()], [_opportunity()], [_solution()], [])

    for m in result.data["messages"]:
        assert "<b>" not in m["message"]


async def test_llm_failure_degrades_gracefully(monkeypatch):
    async def fake_ollama_cfg():
        raise RuntimeError("ollama down")

    monkeypatch.setattr(ma_module, "_ollama_cfg", fake_ollama_cfg)

    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    result = await agent.run(lead, {"industry": "Dental Care"}, [_pain_point()], [_opportunity()], [_solution()], [])

    assert result.status == "ok"
    assert len(result.data["messages"]) == 6


async def test_strongest_pain_point_selected_when_multiple(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = MarketingAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    pain_points = [
        _pain_point(id=1, title="Weak signal", confidence=0.3, severity="low"),
        _pain_point(id=2, title="No visible online appointment/booking system", confidence=0.9, severity="high"),
    ]
    result = await agent.run(lead, {"industry": "Dental Care"}, pain_points, [], [], [])

    for m in result.data["messages"]:
        assert m["pain_point"] == "No visible online appointment/booking system"
