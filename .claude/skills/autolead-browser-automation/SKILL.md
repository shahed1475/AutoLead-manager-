---
name: autolead-browser-automation
description: Use when working on Selenium or Playwright code in AutoLead — scraper browser sessions, WhatsApp desktop automation, or any new browser-driven research/discovery feature.
---

# AutoLead Browser Automation

## Purpose

Governs browser-automation usage boundaries: what exists today, when to reach for a browser vs. plain HTTP, and the hard line against anti-bot evasion.

## When to Use

Changing/adding a Selenium or Playwright-driven scraper, debugging a browser-automation failure, or deciding whether a new feature needs a real browser session.

## When NOT to Use

Any research task `httpx`+BeautifulSoup can already handle — see the decision rule below.

## Project Context

**Current usage — narrower than it might seem:**
- Selenium (`selenium`, `webdriver-manager`): `scrapers/google_maps.py` (primary, most-maintained scraper) and the legacy `scraper.py::scrape_google_maps`. UA rotation (10 UAs via `fake-useragent`), human mouse jitter, CAPTCHA detection with 60s retry.
- Playwright (`playwright`): `scrapers/bing_search.py::_search_with_browser` (Chromium, headless configurable, falls back to DuckDuckGo HTML scraping via `_shared.ddg_collect_urls` on failure/block, saves debug HTML/JSON) **and** `backend/research_agent/browser.py::BrowserController` — the Browser Research Agent's controller (one session, async context manager, reuses `bing_search._resolve_headless`; see `autolead-browser-research-agent`).
- **Every other source (7 of 9) is plain `httpx`/`requests` + BeautifulSoup** — no browser at all. Don't assume a new source needs Playwright by default; most of this codebase's scrapers prove it usually doesn't.
- **Fingerprint normalization vs. evasion — where the line is:** `bing_search.py` and `research_agent/browser.py` both set a realistic UA/viewport and a small init script that neutralizes the most obvious `navigator.webdriver` automation tells. That much is accepted in this codebase. Anything beyond it — proxy/IP rotation, a CAPTCHA-solving service, fingerprint churning, stealth plugins — is not, regardless of throughput pressure. When a browser path's result rate drops because of blocks, the sanctioned levers are: fewer/politer requests (reuse `scraper_delay_min`/`max`, visit the homepage before a deep link), a documented fallback source (DuckDuckGo/Bing, as `bing_search.py` does), or a settings-gated paid SERP API — never "hide better".
- WhatsApp sending (`whatsapp_sender.py`) drives the real OS desktop via `pyautogui`/`pyperclip` — not a headless browser, but the same "real automated interaction with a live surface" caution applies. Serialized behind a process-wide `asyncio.Lock`.
- `scraper_headless` (config) and `scraper_delay_min`/`scraper_delay_max` (anti-ban jitter range) are the existing knobs every scraper's `resolve_delay` reads — reuse them for new browser-driven code rather than inventing new timing config.

## Rules

1. **Prefer direct HTTP/API access. Reach for a browser only when JS rendering or real interaction is required** — this mirrors the existing codebase's own 7-HTTP-vs-2-browser ratio.
2. **Never build CAPTCHA-solving, anti-bot evasion, or stealth mechanisms intended to defeat detection.** The existing scrapers' CAPTCHA/Cloudflare handling is "detect and back off or skip gracefully" (`yelp.py` skipping on 403/999, `bing_search.py` falling back to DDG) — extend that pattern, never a bypass.
3. **Never automate a login flow or bypass an access control.** All 9 sources scrape public, unauthenticated pages.
4. **Always clean up browser resources** — every Selenium/Playwright session must close in a `finally` (or context manager), matching existing scraper structure, to avoid orphaned Chrome processes.
5. **Respect existing rate-limit/delay config** (`scraper_delay_min`/`max`) rather than hardcoding new timing.
6. **WhatsApp automation stays behind its existing lock** — never add a second code path that drives pyautogui concurrently with the existing one.

## Architecture Guidance

New browser-driven research follows `bing_search.py`'s shape: Playwright primary with a documented non-browser fallback, debug artifact capture on failure, isolated failure (never kills the whole discovery/campaign run). `research_agent/browser.py` is the worked example — every action method returns an `ActionResult` and never raises into the caller; a detected block is recorded and that path abandoned, not retried harder.

**Web page content is untrusted input.** A page the agent visits is attacker-controllable. In `research_agent`, page text reaches the LLM only for narrow field extraction (`extract_fields`, on pre-filtered role-sentences) and **never** the action-planning prompt (`decide_next_action` sees only `_build_state_summary`). Preserve that separation in any new browser+LLM code: page content is data to extract from, never instructions to act on, and values the LLM returns from page text get the same `validators` treatment as the deterministic path before they're recorded.

## Implementation Guidance

Before writing new Playwright/Selenium code, check whether `httpx`+BeautifulSoup can get the same data — most target pages (business directories, static marketing sites) don't need JS rendering. Reserve browser automation for confirmed JS-rendered content or when a screenshot/visual check is the actual deliverable.

## Testing Requirements

No existing test currently drives a real browser session (the scraper layer is untested at the mechanics level per the last architecture survey). New browser-automation tests should mock at the function boundary (mock the Selenium/Playwright call, test the parsing/handling logic around it) rather than requiring a real browser in the automated suite, unless a dedicated E2E test tier is explicitly being introduced.

## Security Considerations

Browser sessions should never be handed credentials for third-party sites. UA rotation and jitter are for polite/resilient scraping of public pages, not for evading a security control — if a target site returns a hard block (Cloudflare, CAPTCHA), the correct response is graceful skip + log, per existing `yelp.py` behavior, never an escalation to bypass it.

## Performance Considerations

Browser sessions are the slowest part of the discovery pipeline (Selenium especially). Bound concurrency — don't launch many simultaneous browser sessions; follow the existing single-session-per-source pattern and any `Semaphore` already in place elsewhere in the codebase.

## Failure Modes

| Mistake | Fix |
|---|---|
| Reaching for Playwright for a source that's just static HTML | Use `httpx`+BeautifulSoup, matching 7 of the 9 existing sources |
| Adding logic to solve/bypass a CAPTCHA | Not permitted — detect and skip gracefully instead |
| Browser session not closed on an exception path | Wrap in try/finally or a context manager |
| New browser code ignores `scraper_headless`/delay config | Reuse the existing config knobs |

## Verification Checklist

- [ ] Confirmed HTTP-only wasn't sufficient before reaching for a browser
- [ ] No CAPTCHA-solving/anti-bot-evasion code introduced
- [ ] Browser/session cleanup verified even on the failure path
- [ ] Existing rate-limit/delay config reused, not reinvented
- [ ] Failure isolated — doesn't kill a multi-source discovery run

## Related Skills

`autolead-discovery-and-source-adapters`, `autolead-browser-research-agent`, `autolead-security-and-secrets`

---
Version: 1.1
Scope: AutoLead-manager
Last reviewed: 2026-08-30 (Discovery Planner + Browser Research Agent additions)
