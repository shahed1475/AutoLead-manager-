"""Phase 8 — DuckDuckGo as a real dispatchable web-search source."""
import pytest

from backend.discovery.adapters import get_registry
from backend.scrapers import duckduckgo as ddg

pytestmark = pytest.mark.asyncio


async def test_ddg_scrape_builds_leads_from_urls(monkeypatch):
    monkeypatch.setattr(ddg, "expand_niche", lambda n: [n])
    monkeypatch.setattr(ddg, "ddg_collect_urls",
                        lambda q, s, log, max_urls=20: ["https://brightsmiles.dental", "https://cityortho.com"])

    def fake_info(url, session, ua, log):
        return {"business_name": "Bright Smiles", "email": "hi@brightsmiles.dental",
                "phone": "5551112222", "address": "1 Main St"}

    monkeypatch.setattr(ddg, "_extract_business_info", fake_info)
    monkeypatch.setattr(ddg.time, "sleep", lambda *_a: None)

    leads = await ddg.scrape("dental clinic", "Reno", "USA", max_results=5)
    assert len(leads) == 2
    assert leads[0]["source"] == "DUCKDUCKGO"
    assert leads[0]["website"].startswith("http")
    assert leads[0]["email"] == "hi@brightsmiles.dental"


async def test_ddg_scrape_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(ddg, "ddg_collect_urls", boom)
    leads = await ddg.scrape("dental clinic", "Reno", "USA", max_results=5)
    assert leads == []


async def test_registry_dispatches_duckduckgo(monkeypatch):
    reg = get_registry()
    assert reg.get("DUCKDUCKGO") is not None

    async def fake_dispatch(source, niche, city, country, budget, cfg, log_fn):
        return [{"business_name": "X", "website": "https://x.com", "source": source}]

    monkeypatch.setattr("backend.discovery.adapters._dispatch_source", fake_dispatch)
    res = await reg.execute("DUCKDUCKGO", "dental", "Reno", "USA", 5, {})
    assert res.leads and res.leads[0]["source"] == "DUCKDUCKGO"
