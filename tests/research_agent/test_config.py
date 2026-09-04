import pytest

from backend.research_agent import config as racfg

pytestmark = pytest.mark.asyncio


def test_resolve_depth_known_names():
    assert racfg.resolve_depth("quick") == "quick"
    assert racfg.resolve_depth("Standard") == "standard"
    assert racfg.resolve_depth("DEEP") == "deep"
    assert racfg.resolve_depth("max") == "max"


def test_resolve_depth_unknown_or_none_falls_back_to_standard():
    assert racfg.resolve_depth("bogus") == "standard"
    assert racfg.resolve_depth(None) == "standard"
    assert racfg.resolve_depth("") == "standard"


async def test_default_config_matches_standard_depth(clean_db):
    default_cfg = await racfg.get_research_config()
    standard_cfg = await racfg.get_research_config(depth="standard")
    assert default_cfg["research_agent_max_pages_per_lead"] == standard_cfg["research_agent_max_pages_per_lead"]
    assert default_cfg["research_agent_pacing_profile"] == "standard"


async def test_deep_preset_widens_budgets(clean_db):
    cfg = await racfg.get_research_config(depth="deep")
    assert cfg["research_agent_pacing_profile"] == "deliberate"
    assert cfg["research_agent_max_pages_per_lead"] == 9
    assert cfg["research_agent_max_scrolls_per_page"] == 12
    assert cfg["research_agent_max_searches_per_lead"] == 6
    assert cfg["research_agent_max_actions_per_lead"] == 24
    assert cfg["research_agent_max_time_per_lead_seconds"] == 420
    assert cfg["research_agent_max_domain_seconds"] == 240
    assert cfg["research_agent_page_time_cap_seconds"] == 75


async def test_quick_preset_is_lean(clean_db):
    cfg = await racfg.get_research_config(depth="quick")
    assert cfg["research_agent_pacing_profile"] == "fast"
    assert cfg["research_agent_max_pages_per_lead"] == 2
    assert cfg["research_agent_max_actions_per_lead"] == 8


async def test_db_setting_overrides_the_preset(clean_db):
    db = clean_db
    await db.upsert_setting("research_agent_max_pages_per_lead", "3")
    cfg = await racfg.get_research_config(depth="deep")
    assert cfg["research_agent_max_pages_per_lead"] == 3   # DB wins over deep's preset value of 9


async def test_unset_global_depth_setting_used_when_no_depth_passed(clean_db):
    db = clean_db
    await db.upsert_setting("research_agent_research_depth", "max")
    cfg = await racfg.get_research_config()
    assert cfg["research_agent_research_depth"] == "max"
    assert cfg["research_agent_max_pages_per_lead"] == 14


async def test_explicit_depth_wins_over_stored_global_depth(clean_db):
    db = clean_db
    await db.upsert_setting("research_agent_research_depth", "max")
    cfg = await racfg.get_research_config(depth="quick")
    assert cfg["research_agent_research_depth"] == "quick"


async def test_new_keys_present_and_typed(clean_db):
    cfg = await racfg.get_research_config()
    assert isinstance(cfg["research_agent_allow_professional_profiles"], bool)
    assert cfg["research_agent_allow_professional_profiles"] is False
    assert isinstance(cfg["research_agent_time_budget_seconds"], int)
    assert isinstance(cfg["research_agent_max_queued_links"], int)
