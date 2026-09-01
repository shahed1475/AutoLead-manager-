import pytest

from backend.research_agent import planner as planner_mod

pytestmark = pytest.mark.asyncio


async def _mock_cfg():
    return {"provider": "ollama", "model": "test", "base_url": "http://x", "timeout": 5}


async def test_single_city_needs_no_expansion():
    tasks = await planner_mod.expand_geography("Abbeville, USA", target_count=5, max_units=5)
    assert len(tasks) == 1
    assert tasks[0].city == "Abbeville"
    assert tasks[0].target_count == 5


async def test_single_city_with_no_comma_and_no_country_still_not_expanded():
    """Regression: a bare city name typed with no comma (the common case
    when a caller doesn't separately supply country) must not be routed
    through broad-scope expansion just because it lacks punctuation."""
    tasks = await planner_mod.expand_geography("Abbeville", target_count=4, max_units=5)
    assert len(tasks) == 1
    assert tasks[0].city == "Abbeville"


async def test_state_name_alone_triggers_expansion_not_treated_as_a_city(monkeypatch):
    """Regression: 'California' and 'Abbeville, USA' have the same 'first
    segment, then nothing or a comma' shape — a bare comma can't
    distinguish them. Must key off recognizing the region name itself."""
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"cities": ["Los Angeles, CA", "San Francisco, CA"]}'

    monkeypatch.setattr(planner_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_mod, "_ollama_cfg", _mock_cfg)

    tasks = await planner_mod.expand_geography("California", target_count=10, max_units=5)
    assert len(tasks) == 2  # expanded, not treated as a single "California" city


async def test_state_plus_country_still_expands(monkeypatch):
    """The exact shape the API's _compose_location can never produce for a
    broad scope (it deliberately avoids appending country to a bare
    'location'), but expand_geography itself must still handle it
    correctly if ever called this way directly."""
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"cities": ["Houston, TX", "Dallas, TX"]}'

    monkeypatch.setattr(planner_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_mod, "_ollama_cfg", _mock_cfg)

    tasks = await planner_mod.expand_geography("Texas, USA", target_count=10, max_units=5)
    assert len(tasks) == 2  # 'Texas' is a known state -> broad scope, expanded
    assert all(t.city != "Texas" for t in tasks)


async def test_city_state_country_all_given_stays_single_city():
    tasks = await planner_mod.expand_geography("Abbeville, LA, USA", target_count=4, max_units=5)
    assert len(tasks) == 1
    assert tasks[0].city == "Abbeville"
    assert tasks[0].state == "LA"
    assert tasks[0].country == "USA"


async def test_broad_scope_expands_via_llm(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"cities": ["Los Angeles, CA", "San Francisco, CA", "San Diego, CA"]}'

    monkeypatch.setattr(planner_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_mod, "_ollama_cfg", _mock_cfg)

    tasks = await planner_mod.expand_geography("California", target_count=30, max_units=5)
    assert len(tasks) == 3
    assert sum(t.target_count for t in tasks) == 30
    assert {t.city for t in tasks} == {"Los Angeles", "San Francisco", "San Diego"}


async def test_broad_scope_capped_at_max_units(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"cities": ["A, CA", "B, CA", "C, CA", "D, CA", "E, CA", "F, CA", "G, CA"]}'

    monkeypatch.setattr(planner_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_mod, "_ollama_cfg", _mock_cfg)

    tasks = await planner_mod.expand_geography("California", target_count=100, max_units=3)
    assert len(tasks) == 3  # never blindly generates thousands of tasks


async def test_llm_failure_falls_back_to_known_city_list(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        raise ConnectionError("ollama down")

    monkeypatch.setattr(planner_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_mod, "_ollama_cfg", _mock_cfg)

    tasks = await planner_mod.expand_geography("USA", target_count=20, max_units=5)
    assert len(tasks) == 5  # deterministic fallback list, never zero tasks
    assert sum(t.target_count for t in tasks) == 20


async def test_unrecognized_single_word_treated_as_a_city_not_expanded(monkeypatch):
    """A name the planner doesn't recognize as a state/country (e.g. a
    smaller town not in any lookup list) is assumed to be a specific city —
    never routed through broad-scope LLM expansion on a guess, and never
    needs an LLM call at all for this case."""
    called = {"n": 0}

    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        called["n"] += 1
        return "not valid json"

    monkeypatch.setattr(planner_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_mod, "_ollama_cfg", _mock_cfg)

    tasks = await planner_mod.expand_geography("Freedonia", target_count=10, max_units=5)
    assert len(tasks) == 1
    assert tasks[0].city == "Freedonia"
    assert called["n"] == 0  # single-city path never needs the LLM


async def test_unknown_broad_scope_hint_with_llm_failure_does_not_fabricate_cities(monkeypatch):
    """A genuinely broad-scope request ('worldwide') whose LLM expansion
    fails and has no deterministic fallback entry must not invent cities —
    it degrades to one unexpanded task rather than guessing."""
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return "not valid json"

    monkeypatch.setattr(planner_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_mod, "_ollama_cfg", _mock_cfg)
    monkeypatch.setattr(planner_mod, "_FALLBACK_CITIES", {})  # simulate no fallback available

    tasks = await planner_mod.expand_geography("Worldwide", target_count=10, max_units=5)
    assert len(tasks) == 1
    assert tasks[0].city is None
    assert tasks[0].country == "Worldwide"


async def test_empty_location_returns_no_tasks():
    assert await planner_mod.expand_geography("", target_count=10, max_units=5) == []


async def test_worldwide_scope_triggers_expansion_not_single_city(monkeypatch):
    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '{"cities": ["New York, USA", "London, UK"]}'

    monkeypatch.setattr(planner_mod, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(planner_mod, "_ollama_cfg", _mock_cfg)

    tasks = await planner_mod.expand_geography("Worldwide", target_count=10, max_units=5)
    assert len(tasks) == 2
