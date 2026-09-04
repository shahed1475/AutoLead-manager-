import pytest

from backend.research_agent.pacing import instant_controller
from backend.research_agent.reader import read_page

from ._fakes import ScriptedPage

pytestmark = pytest.mark.asyncio

INITIAL = {"url": "https://clinic.test/team", "text": "Amanda Reyes — Lead Dentist", "links": [], "headings": []}


def _round(text, links=None, headings=None, scroll_height=2000, scroll_y=0, inner_height=900, card_count=1):
    return {
        "text": text, "links": links or [], "headings": headings or [],
        "metrics": {"scrollHeight": scroll_height, "scrollY": scroll_y,
                    "innerHeight": inner_height, "cardCount": card_count},
    }


async def test_short_page_stops_immediately_at_bottom_no_scrolling():
    # round 0's metrics say scrollY(0) + innerHeight(900) >= scrollHeight(900) -> already at bottom
    page = ScriptedPage([_round("(unused — initial wins)", scroll_height=900, scroll_y=0, inner_height=900)])
    initial = {"url": "https://thin.test", "text": "Thin Consulting Co. Contact us.", "links": [], "headings": []}
    content = await read_page(page, instant_controller(), initial=initial, max_scrolls=6, page_time_cap_s=5)
    assert content.stopped_reason == "bottom"
    assert content.scroll_rounds == 0
    assert content.text == "Thin Consulting Co. Contact us."   # exactly the initial snapshot, unmodified


async def test_reveals_lazy_loaded_content_on_scroll():
    rounds = [
        _round("Amanda Reyes — Lead Dentist", scroll_height=2000, scroll_y=0, card_count=1),         # round 0: not yet at bottom
        _round("Amanda Reyes — Lead Dentist\nMarcus Bell — Practice Manager",
               scroll_height=2000, scroll_y=900, card_count=2),                                        # round 1: still not at bottom
        _round("Amanda Reyes — Lead Dentist\nMarcus Bell — Practice Manager\nDana Whitfield — Office Manager",
               scroll_height=2000, scroll_y=1800, card_count=3),                                       # round 2: now at bottom
    ]
    page = ScriptedPage(rounds)
    content = await read_page(page, instant_controller(), initial=INITIAL, max_scrolls=10, page_time_cap_s=5)
    assert "Marcus Bell" in content.text          # invisible to a one-shot snapshot
    assert "Dana Whitfield" in content.text
    assert content.stopped_reason == "bottom"
    assert content.scroll_rounds == 2


async def test_stops_after_two_stable_rounds_with_no_new_content():
    round0 = _round("Static content that never changes.", scroll_height=3000, scroll_y=0, card_count=1)
    same_after_scroll = _round("Static content that never changes.", scroll_height=3000, scroll_y=0, card_count=1)
    page = ScriptedPage([round0, same_after_scroll, same_after_scroll, same_after_scroll])
    content = await read_page(page, instant_controller(), initial=INITIAL, max_scrolls=10, page_time_cap_s=5)
    assert content.stopped_reason == "stable"
    assert content.scroll_rounds == 2               # two unchanged rounds after the first scroll


async def test_stops_at_max_scrolls():
    growing = [
        _round(f"content round {i}", scroll_height=5000, scroll_y=i * 100, card_count=i + 1)
        for i in range(10)
    ]
    page = ScriptedPage(growing)
    content = await read_page(page, instant_controller(), initial=INITIAL, max_scrolls=3, page_time_cap_s=30)
    assert content.stopped_reason == "max_scrolls"
    assert content.scroll_rounds == 3


async def test_empty_initial_and_no_scroll_growth_reports_empty():
    page = ScriptedPage([_round("", links=[], scroll_height=900, scroll_y=0, inner_height=900)])
    empty_initial = {"url": "https://empty.test", "text": "", "links": [], "headings": []}
    content = await read_page(page, instant_controller(), initial=empty_initial, max_scrolls=6, page_time_cap_s=5)
    assert content.stopped_reason == "empty"
    assert content.text == ""


async def test_no_scroll_capability_falls_back_to_the_initial_snapshot():
    """A page double with no working evaluate() (today's ScriptedBrowser
    fake, or a real page that's already closed) must not raise — read_page
    returns exactly the initial snapshot, unmodified, stopped_reason
    'bottom'. This is what keeps every pre-existing scripted-browser test
    working once agent.py routes extract_page_text through the reader."""
    class _NoEvaluatePage:
        url = "https://acmedental.test"

    content = await read_page(
        _NoEvaluatePage(), instant_controller(), initial=INITIAL, max_scrolls=6, page_time_cap_s=5,
    )
    assert content.stopped_reason == "bottom"
    assert content.scroll_rounds == 0
    assert content.text == INITIAL["text"]


async def test_evaluate_error_mid_scroll_returns_error_content_never_raises():
    class _BreaksDuringScroll:
        """Answers settle()'s readyState/text-length checks and the first
        metrics probe normally, succeeds on the scroll step itself, then
        fails on the re-fetch afterward — simulating a page that navigates
        away or closes mid-scroll."""
        url = "https://broken.test"

        def __init__(self):
            self._scrolled = False

        async def evaluate(self, js):
            if "document.readyState" in js:
                return "complete"
            if "innerText.length" in js:
                return 42  # constant -> settle() stabilises quickly
            if "scrollBy" in js:
                self._scrolled = True
                return None
            if self._scrolled:
                raise RuntimeError("navigation interrupted")
            if "scrollHeight" in js:
                return {"scrollHeight": 5000, "scrollY": 0, "innerHeight": 900, "cardCount": 1}  # not at bottom -> triggers a scroll
            raise RuntimeError("unexpected evaluate call")

    content = await read_page(
        _BreaksDuringScroll(), instant_controller(), initial=INITIAL, max_scrolls=6, page_time_cap_s=5,
    )
    assert content.stopped_reason == "error"
    assert content.url == "https://broken.test"


async def test_page_time_cap_stops_a_slow_growing_page():
    # Never stabilises and never reaches bottom — only the time cap can stop it.
    growing = [
        _round(f"content {i}", scroll_height=100000, scroll_y=i * 10, card_count=i + 1)
        for i in range(50)
    ]
    page = ScriptedPage(growing)
    content = await read_page(page, instant_controller(), initial=INITIAL, max_scrolls=1000, page_time_cap_s=0.05)
    assert content.stopped_reason == "page_time_cap"
