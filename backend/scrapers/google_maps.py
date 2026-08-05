"""
Improved Google Maps scraper — Selenium + Chrome (webdriver-manager).

Improvements over the legacy _scrape_sync in scraper.py:
  • 3 query variants per niche+city combo to hit 50–80 unique results
  • Two-phase: Phase 1 collects result-panel URLs, Phase 2 visits each detail page
  • Extracts rating (float) and reviews_count (int) in addition to core fields
  • 10-UA rotation + random human-like delays (3–8 s) for anti-ban
  • CAPTCHA detection with 60-second pause and single retry
  • Full error recovery — one bad listing never aborts the run

Threading model
---------------
Selenium is synchronous. The public async entry-point `scrape` runs the sync
work inside asyncio.to_thread() so the FastAPI event loop is never blocked.
The log_callback is a plain sync callable; the caller bridges it to async via
asyncio.run_coroutine_threadsafe when SSE streaming is needed.
"""

import asyncio
import os
import random
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

# ── Selenium — graceful degradation when missing ─────────────────────────────
try:
    from selenium import webdriver
    from selenium.common.exceptions import (
        NoSuchElementException,
        StaleElementReferenceException,
        TimeoutException,
        WebDriverException,
    )
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager

    _SELENIUM_OK = True
except ImportError:
    _SELENIUM_OK = False


# ── User-agent pool (10 real desktop browser UAs) ─────────────────────────────
_USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# CSS selectors — Maps 2024/2025 layout
_SEL_FEED         = '[role="feed"]'
_SEL_RESULT_LINK  = 'a.hfpxzc'           # each result card's anchor with href
_SEL_NAME         = 'h1.DUwDvf, h1.fontHeadlineLarge, h1[class*="fontHeadline"], h1'
_SEL_PHONE_ITEM   = '[data-item-id^="phone:tel:"]'
_SEL_PHONE_TEL    = 'a[href^="tel:"]'
_SEL_WEBSITE      = 'a[data-item-id="authority"]'
_SEL_ADDRESS      = 'button[data-item-id="address"]'
_SEL_RATING       = 'div.F7nice span[aria-hidden="true"], span.ceNzKf, div[jsaction*="rating"] span'
_SEL_REVIEWS      = 'div.F7nice span[aria-label*="review"], button[jsaction*="review"] span[aria-label]'
_SEL_CAPTCHA      = 'form#captcha-form, div#captcha, iframe[src*="recaptcha"], div[class*="recaptcha"]'


# ── Driver factory ────────────────────────────────────────────────────────────

def _system_chrome_paths() -> tuple[Optional[str], Optional[str]]:
    """
    Return (chrome_binary, chromedriver_path) from CHROME_BIN / CHROMEDRIVER_PATH
    env vars when both point to real files on disk (set inside the Docker image,
    where system Chromium + a version-matched chromedriver are pre-installed).

    webdriver-manager always downloads the LATEST chromedriver, which can be a
    version ahead of an apt-installed Chromium and fails with
    SessionNotCreatedException — using the pre-matched system pair avoids that
    entirely. Returns (None, None) when unset (e.g. native Windows/venv runs),
    so callers fall back to webdriver-manager's auto-detection there.
    """
    chrome_bin = os.environ.get("CHROME_BIN")
    driver_bin = os.environ.get("CHROMEDRIVER_PATH")
    if chrome_bin and driver_bin and os.path.isfile(chrome_bin) and os.path.isfile(driver_bin):
        return chrome_bin, driver_bin
    return None, None


def _resolve_headless(requested: bool) -> bool:
    """
    Force headless on a Linux host with no X display (e.g. inside Docker) —
    a visible Chrome window can never render there, and Selenium fails
    immediately with SessionNotCreatedException ("Chrome instance exited")
    if a non-headless launch is attempted. The UI's "show browser window"
    checkbox is unchecked (non-headless) by default, so without this the
    scraper silently finds zero leads on every Docker deployment.
    """
    if not requested and sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return True
    return requested


def _build_driver(headless: bool, user_agent: Optional[str] = None) -> "webdriver.Chrome":
    headless = _resolve_headless(headless)
    ua   = user_agent or random.choice(_USER_AGENTS)
    opts = ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=en-US,en;q=0.9")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--user-agent={ua}")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    chrome_bin, driver_bin = _system_chrome_paths()
    if chrome_bin:
        opts.binary_location = chrome_bin
    service = ChromeService(driver_bin or ChromeDriverManager().install())
    driver  = webdriver.Chrome(service=service, options=opts)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        """
    })
    return driver


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dismiss_consent(driver: "webdriver.Chrome") -> None:
    for sel in [
        'button[aria-label*="Accept all"]',
        'button[aria-label*="Accept"]',
        'button[jsname="b3VHJd"]',
    ]:
        try:
            btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
            )
            btn.click()
            time.sleep(0.8)
            return
        except TimeoutException:
            continue


def _is_captcha(driver: "webdriver.Chrome") -> bool:
    title = driver.title.lower()
    if "captcha" in title or "unusual traffic" in title or "robot" in title:
        return True
    try:
        driver.find_element(By.CSS_SELECTOR, _SEL_CAPTCHA)
        return True
    except NoSuchElementException:
        return False


def _human_mouse_move(driver: "webdriver.Chrome") -> None:
    """Jitter the mouse to random viewport positions — looks human."""
    try:
        actions = ActionChains(driver)
        for _ in range(random.randint(2, 5)):
            x = random.randint(200, 1400)
            y = random.randint(100, 800)
            actions.move_by_offset(x, y)
        actions.perform()
    except Exception:
        pass


def _scroll_feed(driver: "webdriver.Chrome", px: int = 1500) -> None:
    try:
        feed = driver.find_element(By.CSS_SELECTOR, _SEL_FEED)
        driver.execute_script("arguments[0].scrollBy(0, arguments[1])", feed, px)
        time.sleep(random.uniform(1.2, 2.5))
    except (NoSuchElementException, WebDriverException):
        pass


def _parse_reviews(text: str) -> Optional[int]:
    """'(1,234 reviews)' or '1234' → 1234"""
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _parse_rating(text: str) -> Optional[float]:
    """'4.5' or '4,5' → 4.5"""
    clean = text.strip().replace(",", ".")
    try:
        val = float(clean)
        return val if 1.0 <= val <= 5.0 else None
    except ValueError:
        return None


# ── Phase 1: collect result URLs from the search feed ────────────────────────

def _collect_result_urls(
    driver: "webdriver.Chrome",
    target: int,
    log_fn: Callable[[str], None],
) -> List[str]:
    """Scroll the Maps results panel and harvest all `a.hfpxzc` hrefs."""
    urls:  List[str] = []
    seen:  set       = set()
    stall  = 0

    while len(urls) < target and stall < 6:
        try:
            anchors = driver.find_elements(By.CSS_SELECTOR, _SEL_RESULT_LINK)
        except WebDriverException:
            break

        prev_count = len(urls)
        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
                if href and href not in seen:
                    seen.add(href)
                    urls.append(href)
            except StaleElementReferenceException:
                continue

        if len(urls) == prev_count:
            stall += 1
        else:
            stall = 0

        if len(urls) < target:
            _scroll_feed(driver)
            log_fn(f"📜 Collected {len(urls)} URLs, scrolling for more …")

    return urls[:target]


# ── Phase 2: visit each detail URL and extract all fields ────────────────────

def _extract_detail(
    driver: "webdriver.Chrome",
    url: str,
    niche: str,
    city: str,
    idx: int,
    total: int,
    log_fn: Callable[[str], None],
    captcha_retried: bool,
    country: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Navigate to a Maps business detail URL and extract all fields.
    Returns a lead dict or None on failure.
    captcha_retried is passed by reference via a mutable list in the caller
    so we can signal that we already did one retry.
    """
    try:
        driver.get(url)
        time.sleep(random.uniform(1.5, 3.0))

        if _is_captcha(driver):
            return {"__captcha__": True}

        # Wait for h1 (detail panel loaded)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _SEL_NAME))
            )
        except TimeoutException:
            pass

        # ── Name ──────────────────────────────────────────────────────────────
        name = ""
        for sel in _SEL_NAME.split(", "):
            try:
                el   = driver.find_element(By.CSS_SELECTOR, sel.strip())
                name = (el.text or "").strip()
                if name:
                    break
            except (NoSuchElementException, StaleElementReferenceException):
                continue
        if not name:
            return None

        # ── Phone ─────────────────────────────────────────────────────────────
        phone = ""
        try:
            ph_els = driver.find_elements(By.CSS_SELECTOR, _SEL_PHONE_ITEM)
            if ph_els:
                raw = ph_els[0].get_attribute("data-item-id") or ""
                phone = raw.replace("phone:tel:", "").replace("phone:", "").strip()
            if not phone:
                tel_els = driver.find_elements(By.CSS_SELECTOR, _SEL_PHONE_TEL)
                if tel_els:
                    phone = (tel_els[0].get_attribute("href") or "").replace("tel:", "").strip()
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        # ── Website ───────────────────────────────────────────────────────────
        website = ""
        try:
            web_els = driver.find_elements(By.CSS_SELECTOR, _SEL_WEBSITE)
            if web_els:
                website = web_els[0].get_attribute("href") or ""
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        # ── Address ───────────────────────────────────────────────────────────
        address = ""
        try:
            addr_els = driver.find_elements(By.CSS_SELECTOR, _SEL_ADDRESS)
            if addr_els:
                raw = addr_els[0].get_attribute("aria-label") or ""
                address = raw.replace("Address: ", "").strip()
                if not address:
                    try:
                        inner   = addr_els[0].find_element(By.CSS_SELECTOR, ".fontBodyMedium")
                        address = inner.text.strip()
                    except NoSuchElementException:
                        address = addr_els[0].text.strip()
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        # ── Rating ────────────────────────────────────────────────────────────
        rating: Optional[float] = None
        try:
            for sel in _SEL_RATING.split(", "):
                rating_els = driver.find_elements(By.CSS_SELECTOR, sel.strip())
                if rating_els:
                    rating = _parse_rating(rating_els[0].text)
                    if rating:
                        break
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        # ── Reviews count ─────────────────────────────────────────────────────
        reviews_count: Optional[int] = None
        try:
            for sel in _SEL_REVIEWS.split(", "):
                rev_els = driver.find_elements(By.CSS_SELECTOR, sel.strip())
                if rev_els:
                    aria_label = rev_els[0].get_attribute("aria-label") or rev_els[0].text
                    reviews_count = _parse_reviews(aria_label)
                    if reviews_count is not None:
                        break
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        # ── Country: prefer the campaign's actual country param over guessing
        # from the address — the last comma-segment of a scraped address is
        # often a postal code or state, not a country. Only fall back to that
        # heuristic when the caller didn't supply a country at all.
        country_out: Optional[str] = country.strip() if country else None
        if not country_out and address:
            parts = [p.strip() for p in address.split(",")]
            if len(parts) >= 2:
                country_out = parts[-1]

        log_fn(
            f"✅ [{idx}/{total}] {name}"
            + (f" ★{rating}" if rating else "")
            + (f" ({reviews_count} rev)" if reviews_count else "")
            + (f" — {phone}" if phone else "")
        )

        return {
            "business_name": name,
            "phone":         phone         or None,
            "website":       website       or None,
            "address":       address       or None,
            "rating":        rating,
            "reviews_count": reviews_count,
            "niche":         niche,
            "city":          city,
            "country":       country_out,
            "source":        "GOOGLE_MAPS",
            "raw_url":       url,
            "email":         None,
        }

    except WebDriverException as exc:
        log_fn(f"⚠️  Browser error on result {idx}: {exc}")
        return None


# ── Core sync scraper ─────────────────────────────────────────────────────────

def scrape_sync(
    niche: str,
    city: str,
    max_results: int,
    cfg: Dict[str, Any],
    log_fn: Callable[[str], None],
    country: str = "",
) -> List[Dict[str, Any]]:
    """
    Selenium-driven Google Maps scraper with 3 query variants, pagination,
    extended field extraction, UA rotation, and CAPTCHA recovery.

    Runs synchronously — caller must use asyncio.to_thread().
    Returns list[dict] matching the lead schema.
    """
    if not _SELENIUM_OK:
        log_fn("❌ Selenium not installed. Run: pip install selenium webdriver-manager")
        return []

    if not cfg.get("headless") and _resolve_headless(False):
        log_fn("🖥️  No display available on this host — forcing headless Chrome")
        cfg = {**cfg, "headless": True}

    # 3 query variants — each may surface different listings
    query_variants = [
        f"{niche} in {city}",
        f"{niche} {city}",
        f"best {niche} {city}",
    ]

    per_query   = max(1, max_results // len(query_variants))
    remainder   = max_results - per_query * (len(query_variants) - 1)
    budgets     = [per_query, per_query, remainder]  # give leftover to last query

    all_leads: List[Dict[str, Any]] = []
    seen_urls:  set = set()
    seen_names: set = set()

    for q_idx, (query, budget) in enumerate(zip(query_variants, budgets)):
        if len(all_leads) >= max_results:
            break

        remaining_budget = min(budget, max_results - len(all_leads))
        if remaining_budget <= 0:
            continue

        log_fn(f"🔍 Query {q_idx + 1}/3: \"{query}\" (target: {remaining_budget})")

        driver: Optional["webdriver.Chrome"] = None
        captcha_retried = False

        try:
            ua = random.choice(_USER_AGENTS)
            log_fn(f"🌐 Launching {'headless' if cfg['headless'] else 'visible'} Chrome …")
            driver = _build_driver(cfg["headless"], user_agent=ua)
            wait   = WebDriverWait(driver, 20)

            encoded  = urllib.parse.quote(query)
            maps_url = f"https://www.google.com/maps/search/{encoded}"
            driver.get(maps_url)

            _dismiss_consent(driver)

            if _is_captcha(driver):
                log_fn("🤖 CAPTCHA detected on search page — waiting 60 s …")
                time.sleep(60)
                driver.refresh()
                time.sleep(5)
                if _is_captcha(driver):
                    log_fn("❌ CAPTCHA persists — skipping this query variant")
                    continue

            # Wait for results feed
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, _SEL_FEED)))
            except TimeoutException:
                log_fn("⚠️  No results feed — check niche/city or try without headless mode")
                continue

            # Phase 1: collect result-page URLs
            log_fn(f"📋 Collecting result URLs …")
            _human_mouse_move(driver)
            result_urls = _collect_result_urls(driver, remaining_budget * 2, log_fn)  # 2× over-collect to allow dedup
            log_fn(f"🔗 {len(result_urls)} URLs collected")

            # Phase 2: visit each URL and extract details
            collected_this_query = 0
            for url in result_urls:
                if len(all_leads) >= max_results:
                    break
                if url in seen_urls:
                    continue

                seen_urls.add(url)

                result = _extract_detail(
                    driver, url, niche, city,
                    idx=len(all_leads) + 1,
                    total=max_results,
                    log_fn=log_fn,
                    captcha_retried=captcha_retried,
                    country=country,
                )

                if result is None:
                    continue

                # CAPTCHA detected mid-scrape
                if result.get("__captcha__"):
                    if not captcha_retried:
                        log_fn("🤖 CAPTCHA detected — waiting 60 s then retrying …")
                        time.sleep(60)
                        captcha_retried = True
                        # Re-attempt this URL once
                        result = _extract_detail(
                            driver, url, niche, city,
                            idx=len(all_leads) + 1,
                            total=max_results,
                            log_fn=log_fn,
                            captcha_retried=True,
                            country=country,
                        )
                        if not result or result.get("__captcha__"):
                            log_fn("❌ CAPTCHA persists — stopping this query")
                            break
                    else:
                        log_fn("❌ CAPTCHA (already retried) — stopping this query")
                        break

                name = (result.get("business_name") or "").strip().lower()
                if name in seen_names:
                    log_fn(f"   ⏭️  Dedup: {result['business_name']}")
                    continue

                seen_names.add(name)
                all_leads.append(result)
                collected_this_query += 1

                # Human-like inter-result delay (3–8 s)
                delay = random.uniform(
                    max(cfg.get("delay_min", 3.0), 3.0),
                    max(cfg.get("delay_max", 8.0), 8.0),
                )
                time.sleep(delay)

            log_fn(f"📊 Query {q_idx + 1} done — {collected_this_query} new leads")

        except WebDriverException as exc:
            log_fn(f"❌ Fatal browser error (query {q_idx + 1}): {exc}")

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    log_fn(f"🏁 Google Maps done — {len(all_leads)} unique businesses scraped")
    return all_leads


# ── Public async entry-point ──────────────────────────────────────────────────

async def scrape(
    niche: str,
    city: str,
    max_results: int = 60,
    cfg: Optional[Dict[str, Any]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    country: str = "",
) -> List[Dict[str, Any]]:
    """
    Async wrapper for the Google Maps scraper.

    Parameters
    ----------
    niche        : business category, e.g. "dentist"
    city         : target city, e.g. "Dubai"
    max_results  : maximum number of unique leads to return (default 60)
    cfg          : scraper config dict with keys: headless, delay_min, delay_max
                   If None, defaults to headless=True, delay 3–8 s
    log_callback : sync callable(msg: str) — for SSE streaming via
                   asyncio.run_coroutine_threadsafe if the caller is async
    """
    effective_cfg = {
        "headless":  True,
        "delay_min": 3.0,
        "delay_max": 8.0,
        **(cfg or {}),
    }

    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    _log(f"🚀 Google Maps scraper starting: {niche} in {city} (max {max_results})")
    leads = await asyncio.to_thread(scrape_sync, niche, city, max_results, effective_cfg, _log, country)
    return leads
