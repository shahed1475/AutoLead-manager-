import pytest

from backend.intelligence import reply_intelligence_agent as ria_module
from backend.intelligence.reply_intelligence_agent import ReplyIntelligenceAgent

pytestmark = pytest.mark.asyncio

_LEAD = {"id": 1, "business_name": "Acme Dental"}
_ORIGINAL_MESSAGE = {"pain_point": "No visible online booking", "solution": "website FAQ answering"}


async def _no_llm(*args, **kwargs):
    return None


def _patch_no_llm(monkeypatch):
    monkeypatch.setattr(ria_module, "_classify_and_draft_llm", _no_llm)


# ── Deterministic opt-out gate ──────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "STOP",
    "Please unsubscribe me",
    "Don't contact me again",
    "Remove me from your list",
    "I'm not interested in further messages",
    "not interested in future emails please",
    "please opt-out",
    "Stop emailing me",
])
async def test_opt_out_phrases_always_force_opt_out(monkeypatch, phrase):
    async def fake_llm_says_something_else(*args, **kwargs):
        return {"intent": "INTERESTED", "confidence": 0.9, "draft_response": "Great, let's talk!"}

    monkeypatch.setattr(ria_module, "_classify_and_draft_llm", fake_llm_says_something_else)

    agent = ReplyIntelligenceAgent()
    result = await agent.run(phrase, _LEAD)

    assert result.data["intent"] == "OPT_OUT"
    assert result.data["confidence"] == 1.0
    assert result.data["recommended_action"] == "SUPPRESS_OUTREACH"
    assert result.data["draft_response"] is None


async def test_clearly_non_opt_out_text_does_not_trigger_opt_out(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = ReplyIntelligenceAgent()
    result = await agent.run("Sounds interesting, tell me more", _LEAD)
    assert result.data["intent"] != "OPT_OUT"


async def test_empty_reply_text_returns_other(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = ReplyIntelligenceAgent()
    result = await agent.run("   ", _LEAD)
    assert result.data["intent"] == "OTHER"
    assert result.data["confidence"] == 0.0


# ── Heuristic fallback classification (LLM unavailable) ─────────────────────

@pytest.mark.parametrize("text,expected_intent", [
    ("How much does this cost?", "PRICING"),
    ("What's your pricing look like?", "PRICING"),
    ("Can we schedule a call this week?", "MEETING_REQUEST"),
    ("I'd love to see a demo", "DEMO_REQUEST"),
    ("Not interested, thanks", "NOT_INTERESTED"),
    ("Maybe later, check back in a few months", "MAYBE_LATER"),
    ("Can you send me more information?", "NEEDS_INFORMATION"),
    ("This is the wrong contact, I no longer work here", "WRONG_CONTACT"),
    ("Absolutely, very interested in this!", "VERY_INTERESTED"),
    ("Sounds interesting, tell me more", "INTERESTED"),
    ("What does this actually involve?", "QUESTION"),
    ("asdkfjasdlkfj random text", "OTHER"),
])
async def test_heuristic_classification_all_intents(monkeypatch, text, expected_intent):
    _patch_no_llm(monkeypatch)
    agent = ReplyIntelligenceAgent()
    result = await agent.run(text, _LEAD)
    assert result.data["intent"] == expected_intent
    assert 0.0 <= result.data["confidence"] <= 1.0


# ── Recommended action mapping ──────────────────────────────────────────────

async def test_pricing_recommended_action_is_request_requirements(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = ReplyIntelligenceAgent()
    result = await agent.run("How much does this cost?", _LEAD)
    assert result.data["recommended_action"] == "REQUEST_REQUIREMENTS"


async def test_interested_recommended_action_is_schedule_meeting(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = ReplyIntelligenceAgent()
    result = await agent.run("Sounds interesting, tell me more", _LEAD, _ORIGINAL_MESSAGE)
    assert result.data["recommended_action"] == "SCHEDULE_MEETING"
    assert result.data["draft_response"] is not None


async def test_not_interested_recommended_action_is_stop_campaign(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = ReplyIntelligenceAgent()
    result = await agent.run("Not interested, thanks", _LEAD)
    assert result.data["recommended_action"] == "STOP_CAMPAIGN"
    assert result.data["draft_response"] is None


async def test_maybe_later_recommended_action_is_schedule_followup(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = ReplyIntelligenceAgent()
    result = await agent.run("Maybe later, check back in a few months", _LEAD)
    assert result.data["recommended_action"] == "SCHEDULE_FOLLOWUP"


# ── Draft response never contains fabricated claims ─────────────────────────

async def test_pricing_draft_response_never_contains_dollar_figure(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = ReplyIntelligenceAgent()
    result = await agent.run("How much does this cost?", _LEAD, _ORIGINAL_MESSAGE)
    if result.data["draft_response"]:
        assert "$" not in result.data["draft_response"]
        assert "guarantee" not in result.data["draft_response"].lower()


async def test_llm_draft_with_unsupported_claim_discarded(monkeypatch):
    """Mocks at the _call_llm_raw layer (not _classify_and_draft_llm itself)
    so the real forbidden-claim filtering inside _classify_and_draft_llm
    actually runs and is what's under test."""
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=350):
        return '''{"intent": "PRICING", "confidence": 0.8,
                   "draft_response": "This will increase revenue by 30% and we guarantee results!"}'''

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    monkeypatch.setattr(ria_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(ria_module, "_ollama_cfg", fake_ollama_cfg)

    agent = ReplyIntelligenceAgent()
    result = await agent.run("How much?", _LEAD, _ORIGINAL_MESSAGE)
    # Forbidden-claim scan must have discarded the draft entirely — never surfaced.
    assert result.data["draft_response"] is None


# ── Draft only generated for draft-worthy intents ───────────────────────────

async def test_opt_out_never_has_draft(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = ReplyIntelligenceAgent()
    result = await agent.run("unsubscribe", _LEAD)
    assert result.data["draft_response"] is None


async def test_wrong_contact_never_has_draft(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = ReplyIntelligenceAgent()
    result = await agent.run("Wrong contact, I no longer work here", _LEAD)
    assert result.data["draft_response"] is None


async def test_question_intent_gets_a_draft(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = ReplyIntelligenceAgent()
    result = await agent.run("What does this actually involve?", _LEAD, _ORIGINAL_MESSAGE)
    assert result.data["draft_response"] is not None


# ── LLM success path ─────────────────────────────────────────────────────────

async def test_llm_classification_used_when_available(monkeypatch):
    async def fake_llm(*args, **kwargs):
        return {"intent": "DEMO_REQUEST", "confidence": 0.92, "draft_response": "Happy to show you a demo."}
    monkeypatch.setattr(ria_module, "_classify_and_draft_llm", fake_llm)

    agent = ReplyIntelligenceAgent()
    result = await agent.run("Can you show me how this works?", _LEAD, _ORIGINAL_MESSAGE)
    assert result.data["intent"] == "DEMO_REQUEST"
    assert result.data["confidence"] == 0.92
    assert result.data["recommended_action"] == "SCHEDULE_MEETING"
    assert result.data["draft_response"] == "Happy to show you a demo."


async def test_llm_failure_falls_back_to_heuristic(monkeypatch):
    async def fake_ollama_cfg():
        raise RuntimeError("ollama down")
    monkeypatch.setattr(ria_module, "_ollama_cfg", fake_ollama_cfg)

    agent = ReplyIntelligenceAgent()
    result = await agent.run("How much does this cost?", _LEAD)
    assert result.data["intent"] == "PRICING"
