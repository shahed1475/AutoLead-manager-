import pytest

from backend.intelligence import followup_agent as fa_module
from backend.intelligence.followup_agent import FollowUpAgent

pytestmark = pytest.mark.asyncio


def _pain_point(**overrides):
    base = {
        "id": 1, "title": "No visible online appointment/booking system",
        "evidence_snippet": "cta_buttons=[]", "source_url": "https://acmedental.co",
        "confidence": 0.85, "operational_impact": "Staff handle scheduling manually.",
        "customer_impact": "Customers must call to book.",
    }
    base.update(overrides)
    return base


def _opportunity(**overrides):
    base = {"id": 10, "pain_point_id": 1, "confidence": 0.85}
    base.update(overrides)
    return base


def _solution(**overrides):
    base = {"id": 20, "business_opportunity_id": 10, "service_name": "WhatsApp Automation", "confidence": 0.8}
    base.update(overrides)
    return base


async def _no_llm(*args, **kwargs):
    return None


def _patch_no_llm(monkeypatch):
    monkeypatch.setattr(fa_module, "_generate_fragments_llm", _no_llm)


async def test_no_pain_points_returns_not_generated():
    agent = FollowUpAgent()
    result = await agent.run({"id": 1, "business_name": "No Data Co"}, [], [], [], step=2)
    assert result.data == {"generated": False}


async def test_step_2_and_step_3_use_different_openings(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}

    r2 = await agent.run(lead, [_pain_point()], [_opportunity()], [_solution()], step=2)
    r3 = await agent.run(lead, [_pain_point()], [_opportunity()], [_solution()], step=3)

    assert r2.data["generated"] and r3.data["generated"]
    opening2 = r2.data["email_body"].split("\n")[0]
    opening3 = r3.data["email_body"].split("\n")[0]
    assert opening2 != opening3


async def test_never_repeats_a_previous_body_heuristic_path(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}

    first = await agent.run(lead, [_pain_point()], [_opportunity()], [_solution()], step=2)
    second = await agent.run(
        lead, [_pain_point()], [_opportunity()], [_solution()], step=3,
        previous_bodies=[first.data["email_body"]],
    )
    assert second.data["email_body"] != first.data["email_body"]


async def test_llm_output_colliding_with_previous_body_falls_back_to_heuristic(monkeypatch):
    async def fake_llm(*args, **kwargs):
        return {"opening": "Repeat opening.", "solution_benefit": "Same benefit.", "cta": "Same CTA?"}
    monkeypatch.setattr(fa_module, "_generate_fragments_llm", fake_llm)

    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    previous_body = "Repeat opening.\n\nSame benefit.\n\nSame CTA?"

    result = await agent.run(
        lead, [_pain_point()], [_opportunity()], [_solution()], step=2,
        previous_bodies=[previous_body],
    )
    assert result.data["email_body"] != previous_body


async def test_maybe_later_intent_uses_gentler_angle(monkeypatch):
    captured = {}

    async def fake_llm(pain_point, evidence_snippet, business_impact, solution_desc, benefit_text, business_name, angle_instruction, service_name):
        captured["angle"] = angle_instruction
        return None

    monkeypatch.setattr(fa_module, "_generate_fragments_llm", fake_llm)

    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    await agent.run(
        lead, [_pain_point()], [_opportunity()], [_solution()], step=2,
        latest_reply_intent="MAYBE_LATER",
    )
    assert "later" in captured["angle"].lower()


async def test_whatsapp_body_present_and_shorter_than_email(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}

    result = await agent.run(lead, [_pain_point()], [_opportunity()], [_solution()], step=2)
    assert result.data["whatsapp_body"]
    assert len(result.data["whatsapp_body"]) < len(result.data["email_body"])


async def test_no_solution_match_never_names_a_service(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Vague Co"}

    result = await agent.run(lead, [_pain_point()], [], [], step=2)
    assert result.data["service_name"] is None
