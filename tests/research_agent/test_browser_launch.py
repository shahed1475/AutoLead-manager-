"""
test_browser_launch.py — BrowserController.start() must fail *cleanly* when
Playwright's Chromium isn't installed, never surface a raw Playwright trace,
and try the headless-shell build before giving up. (Root cause of "the
Research Agent opens Google but never completes" on a machine where only the
headless-shell build was downloaded.)
"""
import sys
import types

import pytest

from backend.research_agent.browser import BrowserController

pytestmark = pytest.mark.asyncio

_MISSING_MSG = (
    "BrowserType.launch: Executable doesn't exist at "
    "C:\\Users\\x\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe"
)


class _FakePage:
    async def goto(self, *a, **kw): pass
    def set_default_timeout(self, *a, **kw): pass


class _FakeContext:
    async def add_init_script(self, *a, **kw): pass
    async def new_page(self): return _FakePage()
    async def close(self): pass


class _FakeBrowser:
    async def new_context(self, **kw): return _FakeContext()
    async def close(self): pass


class _FakeChromium:
    def __init__(self, fail_headed=True, fail_headless=True):
        self.fail_headed = fail_headed
        self.fail_headless = fail_headless
        self.launches = []

    async def launch(self, headless=False, args=None):
        self.launches.append(headless)
        if not headless and self.fail_headed:
            raise RuntimeError(_MISSING_MSG)
        if headless and self.fail_headless:
            raise RuntimeError(_MISSING_MSG)
        return _FakeBrowser()


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _FakeAsyncPlaywrightCM:
    def __init__(self, chromium):
        self._chromium = chromium

    async def start(self):
        return _FakePlaywright(self._chromium)


def _install_fake_playwright(monkeypatch, chromium):
    fake_mod = types.ModuleType("playwright.async_api")
    fake_mod.async_playwright = lambda: _FakeAsyncPlaywrightCM(chromium)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_mod)


async def test_missing_chromium_raises_actionable_message(monkeypatch):
    chromium = _FakeChromium(fail_headed=True, fail_headless=True)
    _install_fake_playwright(monkeypatch, chromium)

    bc = BrowserController(headless=False)
    with pytest.raises(RuntimeError) as exc:
        await bc.start()

    msg = str(exc.value)
    assert "not installed" in msg
    assert "playwright install chromium" in msg
    assert "Executable doesn't exist" not in msg      # raw trace never surfaced
    assert chromium.launches == [False, True]          # tried headed, then headless-shell


async def test_falls_back_to_headless_shell_when_headed_missing(monkeypatch):
    chromium = _FakeChromium(fail_headed=True, fail_headless=False)
    _install_fake_playwright(monkeypatch, chromium)

    bc = BrowserController(headless=False)
    await bc.start()   # should NOT raise — headless-shell launch succeeded
    assert chromium.launches == [False, True]
    await bc.stop()
