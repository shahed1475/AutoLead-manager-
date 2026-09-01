import pytest

from backend.research_agent import llm as llm_mod

pytestmark = pytest.mark.asyncio


async def _mock_cfg():
    return {"provider": "ollama", "model": "test", "base_url": "http://x", "timeout": 5}


async def test_decide_next_action_valid_json(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"action": "google_search", "query": "\\"Acme Dental\\" Abbeville", "reason": "find business", "confidence": 0.9}'

    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(llm_mod, "_ollama_cfg", _mock_cfg)

    action = await llm_mod.decide_next_action("state summary here")
    assert action.action == "google_search"
    assert action.params["query"] == '"Acme Dental" Abbeville'
    assert action.confidence == 0.9
    assert action.reason == "find business"


async def test_decide_next_action_nested_params_also_supported(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"action": "open_url", "params": {"url": "https://acme.test"}, "confidence": 0.7}'

    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(llm_mod, "_ollama_cfg", _mock_cfg)

    action = await llm_mod.decide_next_action("state")
    assert action.action == "open_url"
    assert action.params == {"url": "https://acme.test"}


async def test_decide_next_action_malformed_json_returns_llm_failed(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return "I think you should search for the business, here's my answer: not really json"

    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(llm_mod, "_ollama_cfg", _mock_cfg)

    action = await llm_mod.decide_next_action("state")
    assert action.action == "_llm_failed"


async def test_decide_next_action_missing_action_key(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"query": "something", "confidence": 0.5}'

    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(llm_mod, "_ollama_cfg", _mock_cfg)

    action = await llm_mod.decide_next_action("state")
    assert action.action == "_llm_failed"


async def test_decide_next_action_unknown_action_rejected(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"action": "delete_database", "confidence": 0.9}'

    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(llm_mod, "_ollama_cfg", _mock_cfg)

    action = await llm_mod.decide_next_action("state")
    assert action.action == "_llm_failed"
    assert "invalid action" in action.reason


async def test_decide_next_action_llm_exception_never_raises(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        raise ConnectionError("ollama unreachable")

    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(llm_mod, "_ollama_cfg", _mock_cfg)

    action = await llm_mod.decide_next_action("state")  # must not raise
    assert action.action == "_llm_failed"


async def test_decide_next_action_confidence_clamped(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"action": "finish_research", "confidence": 5.0}'

    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(llm_mod, "_ollama_cfg", _mock_cfg)

    action = await llm_mod.decide_next_action("state")
    assert action.confidence == 1.0


async def test_extract_fields_valid_json(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"management_contact_name": "Emma Papp", "management_title": "Office Manager"}'

    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(llm_mod, "_ollama_cfg", _mock_cfg)

    result = await llm_mod.extract_fields("Emma Papp — Office Manager", ["management_contact_name", "management_title"], business_name="Acme Dental")
    assert result == {"management_contact_name": "Emma Papp", "management_title": "Office Manager"}


async def test_extract_fields_filters_junk_values(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"management_contact_name": "unknown", "management_title": "N/A", "business_email": null}'

    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(llm_mod, "_ollama_cfg", _mock_cfg)

    result = await llm_mod.extract_fields("some text", ["management_contact_name"], business_name="Acme")
    assert result == {}


async def test_extract_fields_malformed_returns_empty(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return "not json"

    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(llm_mod, "_ollama_cfg", _mock_cfg)

    result = await llm_mod.extract_fields("some text", ["management_contact_name"], business_name="Acme")
    assert result == {}


async def test_extract_fields_short_circuits_with_no_text_or_no_missing_fields(monkeypatch):
    called = {"n": 0}

    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        called["n"] += 1
        return "{}"

    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(llm_mod, "_ollama_cfg", _mock_cfg)

    assert await llm_mod.extract_fields("", ["management_contact_name"]) == {}
    assert await llm_mod.extract_fields("some text", []) == {}
    assert called["n"] == 0  # never even called the LLM for empty inputs
