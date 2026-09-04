import pytest

from backend.discovery import planner as planner_module
from backend.discovery.planner import DiscoveryPlanner

pytestmark = pytest.mark.asyncio


async def test_local_business_keyword_classification(clean_db):
    plan = await DiscoveryPlanner().plan(
        query="dental clinics", niche="dental clinics", city="California", country="", mode="QUICK",
    )
    assert plan.intent == "LOCAL_BUSINESS"
    assert plan.classification_method == "rule_based"
    assert "GOOGLE_MAPS" in plan.recommended_sources
    assert plan.confidence >= 0.5


async def test_startup_tech_keyword_classification(clean_db):
    plan = await DiscoveryPlanner().plan(
        query="AI startups", niche="AI startups", city="California", country="", mode="QUICK",
    )
    assert plan.intent == "STARTUP_TECH_COMPANY"
    assert plan.classification_method == "rule_based"
    assert "GOOGLE_SEARCH" in plan.recommended_sources


async def test_ai_keyword_does_not_false_match_inside_unrelated_words(clean_db, monkeypatch):
    """Regression: a naive substring check on 'ai' would misclassify local
    businesses whose name merely contains those letters (retail, tailor,
    detailing, portrait) as STARTUP_TECH_COMPANY."""
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"intent": "LOCAL_BUSINESS", "confidence": 0.5, "recommended_sources": ["GOOGLE_MAPS"], "query_variants": ["car detailing"]}'

    async def fake_ollama_cfg():
        return {"provider": "ollama", "model": "test", "base_url": "http://x", "timeout": 5}

    monkeypatch.setattr(planner_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_module, "_ollama_cfg", fake_ollama_cfg)

    plan = await DiscoveryPlanner().plan(
        query="car detailing", niche="car detailing", city="Texas", country="", mode="QUICK",
    )
    # Must NOT rule-based-match STARTUP_TECH via the "ai" substring in "detailing" —
    # falls through to the (mocked) LLM instead.
    assert plan.classification_method != "rule_based" or plan.intent != "STARTUP_TECH_COMPANY"


async def test_vet_keyword_does_not_false_match_inside_advertising(clean_db, monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"intent": "AMBIGUOUS", "confidence": 0.3, "recommended_sources": ["GOOGLE_SEARCH"], "query_variants": ["advertising agency"]}'

    async def fake_ollama_cfg():
        return {"provider": "ollama", "model": "test", "base_url": "http://x", "timeout": 5}

    monkeypatch.setattr(planner_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_module, "_ollama_cfg", fake_ollama_cfg)

    plan = await DiscoveryPlanner().plan(
        query="advertising agency", niche="advertising agency", city="Texas", country="", mode="QUICK",
    )
    # "vet" is a LOCAL_BUSINESS keyword — must not match inside "advertising".
    assert not (plan.classification_method == "rule_based" and plan.intent == "LOCAL_BUSINESS")


async def test_standalone_vet_keyword_still_matches(clean_db):
    plan = await DiscoveryPlanner().plan(
        query="vet clinic", niche="vet clinic", city="Texas", country="", mode="QUICK",
    )
    assert plan.intent == "LOCAL_BUSINESS"
    assert plan.classification_method == "rule_based"


async def test_ambiguous_query_triggers_llm_fallback(clean_db, monkeypatch):
    called = {}

    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        called["hit"] = True
        return '{"intent": "STARTUP_TECH_COMPANY", "confidence": 0.7, "recommended_sources": ["GOOGLE_SEARCH"], "query_variants": ["widget makers"]}'

    async def fake_ollama_cfg():
        return {"provider": "ollama", "model": "test", "base_url": "http://x", "timeout": 5}

    monkeypatch.setattr(planner_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_module, "_ollama_cfg", fake_ollama_cfg)

    plan = await DiscoveryPlanner().plan(
        query="widget makers", niche="widget makers", city="Ohio", country="", mode="QUICK",
    )
    assert called.get("hit") is True
    assert plan.intent == "STARTUP_TECH_COMPANY"
    assert plan.classification_method == "llm"
    assert plan.confidence == 0.7
    assert plan.query_variants == ["widget makers"]


async def test_malformed_llm_response_falls_back_safely(clean_db, monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return "not json at all, sorry"

    async def fake_ollama_cfg():
        return {"provider": "ollama", "model": "test", "base_url": "http://x", "timeout": 5}

    monkeypatch.setattr(planner_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_module, "_ollama_cfg", fake_ollama_cfg)

    plan = await DiscoveryPlanner().plan(
        query="widget makers", niche="widget makers", city="Ohio", country="", mode="QUICK",
    )
    assert plan.classification_method == "llm_fallback"
    assert plan.recommended_sources  # never empty
    assert plan.confidence == 0.0


async def test_llm_exception_never_raises_and_falls_back(clean_db, monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        raise ConnectionError("ollama down")

    async def fake_ollama_cfg():
        return {"provider": "ollama", "model": "test", "base_url": "http://x", "timeout": 5}

    monkeypatch.setattr(planner_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_module, "_ollama_cfg", fake_ollama_cfg)

    # Must not raise.
    plan = await DiscoveryPlanner().plan(
        query="widget makers", niche="widget makers", city="Ohio", country="", mode="QUICK",
    )
    assert plan.classification_method == "llm_fallback"
    assert plan.intent in {"LOCAL_BUSINESS", "STARTUP_TECH_COMPANY", "AMBIGUOUS"}


async def test_query_variants_bounded_for_quick_mode(clean_db):
    plan = await DiscoveryPlanner().plan(
        query="restaurant", niche="restaurant", city="Texas", country="", mode="QUICK",
    )
    assert len(plan.query_variants) <= 2  # discovery_quick_max_variants default


async def test_query_variants_bounded_for_campaign_mode(clean_db):
    plan = await DiscoveryPlanner().plan(
        query="restaurant", niche="restaurant", city="Texas", country="", mode="CAMPAIGN",
    )
    assert len(plan.query_variants) <= 4  # discovery_campaign_max_variants default


async def test_quick_mode_caps_recommended_sources(clean_db):
    from backend.config import get_settings
    cap = get_settings().discovery_quick_max_sources
    plan = await DiscoveryPlanner().plan(
        query="dental clinic", niche="dental clinic", city="Texas", country="", mode="QUICK",
    )
    assert len(plan.recommended_sources) <= cap


async def test_campaign_mode_does_not_cap_sources_to_two(clean_db, monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return (
            '{"intent": "STARTUP_TECH_COMPANY", "confidence": 0.6, '
            '"recommended_sources": ["GOOGLE_SEARCH", "BING_SEARCH", "YELP"], "query_variants": ["x"]}'
        )

    async def fake_ollama_cfg():
        return {"provider": "ollama", "model": "test", "base_url": "http://x", "timeout": 5}

    monkeypatch.setattr(planner_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_module, "_ollama_cfg", fake_ollama_cfg)

    plan = await DiscoveryPlanner().plan(
        query="something ambiguous", niche="something ambiguous", city="Texas", country="", mode="CAMPAIGN",
    )
    assert len(plan.recommended_sources) == 3  # not truncated to quick's cap of 2


async def test_never_fabricates_query_variants_with_no_input(clean_db, monkeypatch):
    # Empty query/niche is ambiguous by definition (no keyword can match), so
    # this exercises the LLM path too — mock it so the test never depends on
    # a live Ollama instance being reachable.
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"intent": "AMBIGUOUS", "confidence": 0.1, "recommended_sources": ["GOOGLE_SEARCH"], "query_variants": []}'

    async def fake_ollama_cfg():
        return {"provider": "ollama", "model": "test", "base_url": "http://x", "timeout": 5}

    monkeypatch.setattr(planner_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_module, "_ollama_cfg", fake_ollama_cfg)

    plan = await DiscoveryPlanner().plan(query="", niche="", city="", country="", mode="QUICK")
    # No input to work with — must not invent content, variants stay empty.
    assert plan.query_variants == []
