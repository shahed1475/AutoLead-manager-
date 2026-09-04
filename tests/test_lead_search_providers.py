"""Phase 7 — search-provider catalog + status."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend import database as db
from backend.discovery.provider_catalog import get_provider_catalog

pytestmark = pytest.mark.asyncio


async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_catalog_endpoint_shape(clean_db):
    async with await _client() as c:
        body = (await c.get("/api/lead-search/providers")).json()
    ids = {p["id"] for p in body["providers"]}
    assert {"GOOGLE_MAPS", "YELLOW_PAGES", "DUCKDUCKGO", "BING_SEARCH"} <= ids
    assert all({"id", "name", "status", "kind"} <= p.keys() for p in body["providers"])
    assert body["counts_by_status"]


async def test_directory_sources_are_available(clean_db):
    cat = {p["id"]: p for p in await get_provider_catalog()}
    assert cat["GOOGLE_MAPS"]["status"] == "available"
    assert cat["GOOGLE_MAPS"]["enabled"] is True


async def test_paid_only_engines_are_api_required(clean_db):
    cat = {p["id"]: p for p in await get_provider_catalog()}
    assert cat["KAGI"]["status"] == "api_required"
    assert cat["PERPLEXITY"]["status"] == "api_required"
    assert cat["AOL"]["status"] == "unsupported"


async def test_keyed_engine_flips_when_key_set(clean_db):
    before = {p["id"]: p for p in await get_provider_catalog()}
    assert before["SEARXNG"]["status"] == "not_configured"
    assert before["KAGI"]["status"] == "api_required"
    await db.upsert_setting("searxng_instance_url", "https://searx.example.net")
    await db.upsert_setting("kagi_api_key", "kagi-xxx")
    after = {p["id"]: p for p in await get_provider_catalog()}
    assert after["SEARXNG"]["status"] == "configured"
    assert after["KAGI"]["status"] == "configured"


async def test_per_provider_disable_flag(clean_db):
    await db.upsert_setting("duckduckgo_search_enabled", "false")
    cat = {p["id"]: p for p in await get_provider_catalog()}
    assert cat["DUCKDUCKGO"]["enabled"] is False
