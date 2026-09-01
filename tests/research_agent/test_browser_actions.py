"""
test_browser_actions.py — BrowserController action methods with a faked
Playwright page. Covers the two verification defects:
  B2  google_search that returns 0 usable results must NOT report success
  B1  extract_page_text must surface mailto:/tel: links so the loop can mine them
No real browser — `_page` is a scripted double.
"""
import pytest

from backend.research_agent import browser as browser_mod
from backend.research_agent.browser import BrowserController

pytestmark = pytest.mark.asyncio


class FakePage:
    """Duck-types the bits of playwright.Page the action methods touch.
    `evaluate` is dispatched on a substring of the JS source so a test can
    script distinct return values for the results-scrape vs the block-check
    vs the link/heading scrape."""

    def __init__(self, url="https://acme.test", eval_map=None, title="Acme"):
        self.url = url
        self._title = title
        self._eval_map = eval_map or {}
        self.goto_calls = []

    async def goto(self, url, **kw):
        self.goto_calls.append(url)
        self.url = url

    async def wait_for_timeout(self, *a, **kw):
        pass

    def set_default_timeout(self, *a, **kw):
        pass

    async def title(self):
        return self._title

    async def evaluate(self, js, *a, **kw):
        for needle, value in self._eval_map.items():
            if needle in js:
                return value() if callable(value) else value
        return None


def _bc(page):
    bc = BrowserController(headless=True)
    bc._page = page
    return bc


# ── B2: google_search honest empty/blocked detection ───────────────────────

async def test_google_search_zero_results_and_consent_text_is_blocked(monkeypatch):
    page = _bc  # placeholder to keep linters quiet
    page = FakePage(eval_map={
        "document.body": "Before you continue to Google. We use cookies. Accept all / Reject all",
        "div.g": [],
        "querySelectorAll('a')": [],
    })
    # No DDG fallback results either -> stays blocked
    async def no_ddg(self, query):
        return []
    monkeypatch.setattr(BrowserController, "_duckduckgo_search", no_ddg, raising=False)

    result = await _bc(page).google_search("dental clinics in Los Angeles")
    assert result.status == "blocked"
    assert result.data.get("query") == "dental clinics in Los Angeles"


async def test_google_search_zero_results_falls_back_to_duckduckgo(monkeypatch):
    page = FakePage(eval_map={
        "document.body": "some unrelated google page text",
        "div.g": [],
        "querySelectorAll('a')": [],
    })
    ddg_hits = [{"title": "Acme Dental", "url": "https://acmedental.test", "snippet": "Acme Dental LA"}]

    async def fake_ddg(self, query):
        return ddg_hits
    monkeypatch.setattr(BrowserController, "_duckduckgo_search", fake_ddg, raising=False)

    result = await _bc(page).google_search("acme dental los angeles")
    assert result.status == "success"
    assert result.data["results"] == ddg_hits
    assert result.data.get("via") == "duckduckgo"


async def test_google_search_with_real_results_is_success():
    hits = [{"title": "Acme Dental", "url": "https://acmedental.test", "snippet": "..."}]
    page = FakePage(eval_map={
        "document.body": "normal results page",
        "out.push": hits,   # the results-scrape JS contains `out.push(`
    })
    result = await _bc(page).google_search("acme dental")
    assert result.status == "success"
    assert result.data["results"] == hits
    assert "via" not in result.data


# ── B1: extract_page_text surfaces links + headings ────────────────────────

async def test_open_new_tab_closes_the_previous_non_primary_tab():
    class _Tab:
        def __init__(self, url): self.url = url; self.closed = False
        def set_default_timeout(self, *a, **k): pass
        async def goto(self, url, **kw): self.url = url
        async def close(self): self.closed = True
        async def evaluate(self, *a, **k): return ""
    class _Ctx:
        def __init__(self): self.made = []
        async def new_page(self):
            t = _Tab("about:blank"); self.made.append(t); return t

    bc = BrowserController(headless=True)
    primary = _Tab("https://google.com")
    bc._page = primary
    bc._tabs = [primary]
    bc._context = _Ctx()

    await bc.open_new_tab("https://one.test")     # leaves primary open
    assert primary.closed is False
    first_extra = bc._page
    await bc.open_new_tab("https://two.test")     # must close first_extra
    assert first_extra.closed is True
    assert len(bc._tabs) == 2                      # primary + current, not 3


async def test_extract_page_text_returns_contact_links_and_headings():
    page = FakePage(eval_map={
        "document.body ? document.body.innerText : ''":
            "Acme Dental. Call us. Meet our team.",
        "a[href]": [
            {"text": "Email us", "href": "mailto:office@acmedental.test"},
            {"text": "Call", "href": "tel:+13105551234"},
            {"text": "Our Team", "href": "https://acmedental.test/team"},
        ],
        "h1, h2, h3": ["Welcome to Acme Dental", "Our Team"],
    })
    result = await _bc(page).extract_page_text()
    assert result.status == "success"
    hrefs = [l["href"] for l in result.data["links"]]
    assert "mailto:office@acmedental.test" in hrefs
    assert "tel:+13105551234" in hrefs
    assert "Our Team" in result.data["headings"]
    assert result.data["text"].startswith("Acme Dental")
