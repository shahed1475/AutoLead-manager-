"""Lead audit: deterministic checks, each with a source and a date. Missing
data is 'unknown' — never reported as a problem the business has."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend.audit import lead_audit as la

pytestmark = pytest.mark.asyncio
NOW = "2026-09-25T10:00:00+00:00"

HEALTHY_HTML = (
    '<html><head><meta name="viewport" content="width=device-width">'
    '<title>Alpha</title></head><body><a href="https://wa.me/8801">Chat</a>'
    '<a href="/book">Book now</a></body></html>'
)


def _site(**over):
    base = {
        "url": "https://alpha.example", "has_ssl": True, "error": None, "raw_html": HEALTHY_HTML,
        "page_title": "Alpha Dental", "meta_description": "Dentist in Dhaka",
        "has_contact_form": True, "has_phone_on_page": True, "cta_buttons": ["Book now"],
        "social_media_links": ["https://facebook.com/alpha"],
    }
    base.update(over)
    return base


def _by_key(checks):
    return {c["key"]: c for c in checks}


def test_no_website_is_an_issue_and_site_checks_are_unknown():
    checks = _by_key(la.run_checks({"business_name": "A", "website": None, "rating": 4.6,
                                    "reviews_count": 80, "source": "google_maps"}, None, NOW))
    assert checks["has_website"]["status"] == "issue"
    for key in ("https", "mobile_ready", "contact_form", "whatsapp_link", "online_booking"):
        assert checks[key]["status"] == "unknown", key
    assert checks["rating"]["status"] == "pass"
    assert all(c["checked_at"] == NOW for c in checks.values())


def test_unreachable_site_makes_site_checks_unknown():
    checks = _by_key(la.run_checks({"website": "https://alpha.example"},
                                   _site(error="timeout", raw_html=""), NOW))
    assert checks["site_reachable"]["status"] == "issue"
    assert checks["https"]["status"] == "unknown"
    assert checks["contact_form"]["status"] == "unknown"


def test_healthy_site_passes_and_sources_point_at_the_site():
    checks = _by_key(la.run_checks({"website": "https://alpha.example", "rating": 4.8,
                                    "reviews_count": 120}, _site(), NOW))
    for key in ("has_website", "site_reachable", "https", "mobile_ready", "page_title",
                "meta_description", "contact_form", "phone_on_site", "whatsapp_link",
                "online_booking", "social_links", "rating", "reviews"):
        assert checks[key]["status"] == "pass", key
    assert checks["https"]["source"] == "https://alpha.example"


def test_missing_rating_is_unknown_not_bad():
    checks = _by_key(la.run_checks({"website": None, "rating": None, "reviews_count": None}, None, NOW))
    assert checks["rating"]["status"] == "unknown"
    assert checks["reviews"]["status"] == "unknown"


def test_low_rating_and_few_reviews_are_issues():
    checks = _by_key(la.run_checks({"website": None, "rating": 3.4, "review_count": 5}, None, NOW))
    assert checks["rating"]["status"] == "issue"
    assert checks["reviews"]["status"] == "issue"


def test_score_ignores_unknown_checks():
    s = la.summarize([{"status": "pass"}, {"status": "issue"}, {"status": "unknown"}])
    assert s == {"score": 50, "passed": 1, "issues": 1, "unknown": 1}
    assert la.summarize([{"status": "unknown"}])["score"] is None


async def test_audit_endpoints(clean_db, monkeypatch):
    from backend.main import app
    lead_id = await clean_db.create_lead({"business_name": "Alpha Dental", "website": "https://alpha.example",
                                          "rating": 4.8, "reviews_count": 120})

    async def fake_analyze(url, timeout=10):
        return _site()
    monkeypatch.setattr(la, "analyze_website", fake_analyze)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get(f"/api/leads/{lead_id}/audit")).status_code == 404
        r = await c.post(f"/api/leads/{lead_id}/audit")
        assert r.status_code == 200
        body = r.json()
        assert body["business_name"] == "Alpha Dental" and body["score"] == 100
        again = (await c.get(f"/api/leads/{lead_id}/audit")).json()
        assert again["id"] == body["id"] and len(again["checks"]) == len(body["checks"])
        assert (await c.post("/api/leads/999999/audit")).status_code == 404


def test_analyzer_only_asks_for_encodings_it_can_decode():
    """Advertising 'br' without a Brotli decoder made every Brotli site come
    back as unreadable bytes (no title, no contact form, ...)."""
    import importlib.util
    from backend.enrichment import website_analyzer as wa
    offered = {e.strip() for e in wa._HEADERS["Accept-Encoding"].split(",")}
    if not (importlib.util.find_spec("brotli") or importlib.util.find_spec("brotlicffi")):
        assert "br" not in offered
    if not importlib.util.find_spec("zstandard"):
        assert "zstd" not in offered
