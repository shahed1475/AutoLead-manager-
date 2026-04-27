"""
Google Maps lead scraper — Selenium + Chrome (webdriver-manager).
Email discovery — requests + BeautifulSoup.

All runtime configuration (headless, delays) is read from the DB on every call
so changes made in the Settings UI take effect without restarting the server.

Threading model
---------------
Selenium is synchronous. Both scraping functions run inside asyncio.to_thread()
so the FastAPI event loop is never blocked. The optional log_callback is a plain
sync callable that the caller can use to bridge back to async (e.g. via
asyncio.run_coroutine_threadsafe) for real-time SSE streaming.
"""
import asyncio
import random
import re
import time
import urllib.parse
import urllib3
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from .config import get_settings
from . import database as db

# Silence InsecureRequestWarning — many small-biz sites have bad SSL certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Try to import Selenium at module level — graceful degradation if missing ──
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
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager

    _SELENIUM_OK = True
except ImportError:
    _SELENIUM_OK = False

_env = get_settings()

# ── Constants ─────────────────────────────────────────────────────────────────

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Regex: RFC-5321-ish email — good enough for contact pages
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Email patterns we never want (false positives)
_EMAIL_SPAM = frozenset({
    "example", "yourname", "yourdomain", "domain.com",
    "noreply", "no-reply", "sentry", "wixpress",
    "spamavert", "@2x", "schema.org",
})

# Extra paths to check for contact email when homepage has none
_CONTACT_PATHS = [
    "/contact", "/contact-us", "/contacts",
    "/about",   "/about-us",
    "/get-in-touch", "/reach-us",
]

# Selector candidates for detail-panel phone/website/address (most → least reliable)
# Google Maps 2024/2025 encodes phone in data-item-id like "phone:tel:+19175551234"
_SEL_PHONE_ITEM = '[data-item-id^="phone:tel:"]'
_SEL_PHONE_TEL  = 'a[href^="tel:"]'
_SEL_WEBSITE    = 'a[data-item-id="authority"]'
_SEL_ADDRESS    = 'button[data-item-id="address"]'
_SEL_NAME       = 'h1.DUwDvf, h1.fontHeadlineLarge, h1[class*="fontHeadline"], h1'
_SEL_FEED       = '[role="feed"]'
_SEL_CARD       = '[role="feed"] > div'
_SEL_CARD_NAME  = '.qBF1Pd, .fontHeadlineSmall, [class*="fontHeadline"]'


# ── DB-driven runtime config ──────────────────────────────────────────────────

async def _scraper_cfg() -> Dict[str, Any]:
    """Read scraper settings from DB so UI changes take effect without restart."""
    stored = await db.get_all_settings()
    return {
        "headless":  stored.get("scraper_headless", "true").lower() != "false",
        "delay_min": float(stored.get("scraper_delay_min") or _env.scraper_delay_min),
        "delay_max": float(stored.get("scraper_delay_max") or _env.scraper_delay_max),
    }


# ── Chrome driver factory ─────────────────────────────────────────────────────

def _build_driver(headless: bool) -> "webdriver.Chrome":
    opts = ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")          # modern headless API
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"user-agent={_USER_AGENT}")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    service = ChromeService(ChromeDriverManager().install())
    driver  = webdriver.Chrome(service=service, options=opts)

    # Stealth: hide navigator.webdriver from page scripts
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


# ── Element helpers ───────────────────────────────────────────────────────────

def _text(driver: "webdriver.Chrome", selector: str, default: str = "") -> str:
    try:
        return (driver.find_element(By.CSS_SELECTOR, selector).text or "").strip()
    except (NoSuchElementException, StaleElementReferenceException):
        return default


def _attr(driver: "webdriver.Chrome", selector: str, attribute: str, default: str = "") -> str:
    try:
        el  = driver.find_element(By.CSS_SELECTOR, selector)
        val = el.get_attribute(attribute)
        return (val or "").strip()
    except (NoSuchElementException, StaleElementReferenceException):
        return default


def _scroll_feed(driver: "webdriver.Chrome", px: int = 1200) -> None:
    try:
        feed = driver.find_element(By.CSS_SELECTOR, _SEL_FEED)
        driver.execute_script("arguments[0].scrollBy(0, arguments[1])", feed, px)
        time.sleep(1.5)
    except (NoSuchElementException, WebDriverException):
        pass


def _dismiss_consent(driver: "webdriver.Chrome") -> None:
    """Click EU consent / cookie dialogs if present."""
    for selector in [
        'button[aria-label*="Accept all"]',
        'button[aria-label*="Accept"]',
        'button[jsname="b3VHJd"]',
    ]:
        try:
            btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            btn.click()
            time.sleep(0.8)
            return
        except TimeoutException:
            continue


# ── Email helpers ─────────────────────────────────────────────────────────────

def _is_real_email(email: str) -> bool:
    lower = email.lower()
    return not any(spam in lower for spam in _EMAIL_SPAM)


def _emails_from_html(html: str) -> List[str]:
    """Extract unique, non-spam emails from an HTML string."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "meta"]):
        tag.decompose()
    text   = soup.get_text(separator=" ")
    raw    = _EMAIL_RE.findall(text)
    seen   = set()
    result = []
    for e in raw:
        if e not in seen and _is_real_email(e):
            seen.add(e)
            result.append(e)
    return result


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _find_email_sync(website_url: str) -> Optional[str]:
    """
    Discover a contact email for a website.

    Strategy (in order):
    1. Homepage HTML
    2. mailto: href links (most reliable when present)
    3. Common contact/about sub-pages
    """
    base_url = _normalize_url(website_url)
    session  = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})

    def _fetch(url: str) -> Optional[str]:
        try:
            resp = session.get(url, timeout=10, verify=False, allow_redirects=True)
            if resp.status_code != 200:
                return None

            # 1. Check mailto: links first (highest confidence)
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
                email = a["href"].replace("mailto:", "").split("?")[0].strip()
                if email and _is_real_email(email):
                    return email

            # 2. Regex scan of visible text
            emails = _emails_from_html(resp.text)
            return emails[0] if emails else None
        except requests.RequestException:
            return None

    # Try homepage
    email = _fetch(base_url)
    if email:
        return email

    # Try contact/about sub-pages
    for path in _CONTACT_PATHS:
        email = _fetch(base_url + path)
        if email:
            return email

    return None


# ── Core Google Maps scraper (sync — runs inside asyncio.to_thread) ───────────

def _scrape_sync(
    niche: str,
    city: str,
    max_results: int,
    cfg: Dict[str, Any],
    log_fn: Callable[[str], None],
) -> List[Dict[str, Any]]:
    """
    Selenium-driven Google Maps scraper.

    Returns a list of lead dicts (email=None — caller fills via find_email_from_website).
    All DOM interactions wrap in try/except so a single bad result never aborts the run.
    """
    if not _SELENIUM_OK:
        log_fn("❌ Selenium not installed. Run: pip install selenium webdriver-manager")
        return []

    query    = urllib.parse.quote(f"{niche} in {city}")
    maps_url = f"https://www.google.com/maps/search/{query}"
    leads: List[Dict[str, Any]] = []
    driver: Optional["webdriver.Chrome"] = None

    try:
        log_fn(f"🌐 Launching {'headless' if cfg['headless'] else 'visible'} Chrome ...")
        driver = _build_driver(cfg["headless"])
        wait   = WebDriverWait(driver, 20)

        log_fn(f"🔍 Searching Google Maps: {niche} in {city}")
        driver.get(maps_url)

        _dismiss_consent(driver)

        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, _SEL_FEED)))
        except TimeoutException:
            log_fn("⚠️  No results feed found — check your niche or city spelling")
            return leads

        seen_names:  set[str] = set()
        collected              = 0
        scroll_rounds          = 0
        max_scroll_rounds      = 25   # safety cap on infinite scroll attempts

        while collected < max_results and scroll_rounds < max_scroll_rounds:
            # Re-fetch cards every outer iteration (DOM mutates after each click)
            try:
                cards = driver.find_elements(By.CSS_SELECTOR, _SEL_CARD)
            except WebDriverException:
                break

            new_this_round = 0

            for card in cards:
                if collected >= max_results:
                    break
                try:
                    # ── Card-level name (fast check before clicking) ──────────
                    card_name = ""
                    for cn_sel in _SEL_CARD_NAME.split(", "):
                        try:
                            cn_els = card.find_elements(By.CSS_SELECTOR, cn_sel.strip())
                            if cn_els:
                                card_name = (cn_els[0].text or "").strip()
                                if card_name:
                                    break
                        except (NoSuchElementException, StaleElementReferenceException):
                            continue
                    if not card_name:
                        continue
                    if not card_name or card_name in seen_names:
                        continue

                    # ── Click to open detail panel ────────────────────────────
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'})", card)
                    time.sleep(0.25)
                    try:
                        card.click()
                    except WebDriverException:
                        driver.execute_script("arguments[0].click()", card)

                    # Wait for detail panel heading
                    try:
                        WebDriverWait(driver, 8).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, _SEL_NAME))
                        )
                    except TimeoutException:
                        pass   # proceed anyway — panel may have a different heading class

                    # ── Extract from detail panel ─────────────────────────────
                    # Name — try each selector in order
                    name = ""
                    for name_sel in _SEL_NAME.split(", "):
                        try:
                            el = driver.find_element(By.CSS_SELECTOR, name_sel.strip())
                            name = (el.text or "").strip()
                            if name:
                                break
                        except (NoSuchElementException, StaleElementReferenceException):
                            continue
                    name = name or card_name

                    # Phone — data-item-id encodes "phone:tel:+1..." in modern Maps
                    phone = ""
                    try:
                        ph_els = driver.find_elements(By.CSS_SELECTOR, _SEL_PHONE_ITEM)
                        if ph_els:
                            raw_id = ph_els[0].get_attribute("data-item-id") or ""
                            phone  = raw_id.replace("phone:tel:", "").replace("phone:", "").strip()
                        if not phone:
                            tel_els = driver.find_elements(By.CSS_SELECTOR, _SEL_PHONE_TEL)
                            if tel_els:
                                phone = (tel_els[0].get_attribute("href") or "").replace("tel:", "").strip()
                    except (NoSuchElementException, StaleElementReferenceException):
                        pass

                    # Website — authority link is the external website on Maps
                    website = ""
                    try:
                        web_els = driver.find_elements(By.CSS_SELECTOR, _SEL_WEBSITE)
                        if web_els:
                            website = web_els[0].get_attribute("href") or ""
                    except (NoSuchElementException, StaleElementReferenceException):
                        pass

                    # Address — button[data-item-id="address"] aria-label or inner text
                    address = ""
                    try:
                        addr_els = driver.find_elements(By.CSS_SELECTOR, _SEL_ADDRESS)
                        if addr_els:
                            raw_addr = addr_els[0].get_attribute("aria-label") or ""
                            address  = raw_addr.replace("Address: ", "").strip()
                            if not address:
                                try:
                                    inner   = addr_els[0].find_element(By.CSS_SELECTOR, '.fontBodyMedium')
                                    address = inner.text.strip()
                                except NoSuchElementException:
                                    address = addr_els[0].text.strip()
                    except (NoSuchElementException, StaleElementReferenceException):
                        pass

                    # ── Commit ────────────────────────────────────────────────
                    seen_names.add(name)
                    leads.append({
                        "business_name": name,
                        "phone":         phone   or None,
                        "website":       website or None,
                        "email":         None,        # filled later
                        "niche":         niche,
                        "city":          city,
                        "address":       address or None,
                    })
                    collected      += 1
                    new_this_round += 1

                    log_fn(
                        f"✅ [{collected}/{max_results}] {name}"
                        + (f" — {phone}" if phone else "")
                    )

                    # Human-like random pause between result clicks
                    time.sleep(random.uniform(cfg["delay_min"], cfg["delay_max"]))

                except StaleElementReferenceException:
                    continue   # card went stale after DOM mutation — skip
                except WebDriverException as exc:
                    log_fn(f"⚠️  Browser error on result: {exc}")
                    continue

            if new_this_round == 0:
                # No fresh cards found — scroll the feed to load more
                scroll_rounds += 1
                if collected < max_results:
                    log_fn(f"📜 Scrolling for more ({collected}/{max_results}) ...")
                    _scroll_feed(driver)

        log_fn(f"🏁 Done — {len(leads)} businesses scraped")

    except WebDriverException as exc:
        log_fn(f"❌ Fatal browser error: {exc}")

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return leads


# ── Public async API ──────────────────────────────────────────────────────────

async def scrape_google_maps(
    niche: str,
    city: str,
    max_results: int = 20,
    query: str = "",              # kept for backward compat — ignored (niche+city is used)
    progress_callback=None,       # async Callable(done, total, name) — legacy callers
    log_callback: Optional[Callable[[str], None]] = None,  # sync msg → SSE streaming
) -> List[Dict[str, Any]]:
    """
    Scrape Google Maps for businesses matching `niche` in `city`.

    Parameters
    ----------
    niche            : e.g. "dentist", "plumber", "restaurant"
    city             : e.g. "New York", "London"
    max_results      : cap on how many listings to scrape
    progress_callback: async (done, total, name) — called after each lead found
    log_callback     : sync (msg: str) — real-time status text for SSE
    """
    cfg  = await _scraper_cfg()
    loop = asyncio.get_running_loop()

    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    def _progress(done: int, total: int, name: str) -> None:
        """Thread-safe bridge: schedule async progress_callback on the event loop."""
        if progress_callback:
            asyncio.run_coroutine_threadsafe(
                progress_callback(done, total, name), loop
            )

    def _combined_log(msg: str) -> None:
        _log(msg)
        # Parse "✅ [N/total] name" emitted by _scrape_sync to drive progress_callback
        if msg.startswith("✅ [") and "/" in msg:
            try:
                inner = msg.split("[", 1)[1].split("]", 1)[0]   # e.g. "3/20"
                done  = int(inner.split("/")[0])
                # Extract name after "] "
                name_part = msg.split("] ", 1)[1].split(" — ")[0] if "] " in msg else ""
                _progress(done, max_results, name_part)
            except Exception:
                pass

    # ── Phase 1: Scrape Google Maps in a thread ───────────────────────────────
    _log(f"🚀 Starting scrape: {niche} in {city} (max {max_results})")
    leads: List[Dict[str, Any]] = await asyncio.to_thread(
        _scrape_sync, niche, city, max_results, cfg, _combined_log
    )

    # ── Phase 2: Email discovery (async, sequential to be polite to servers) ──
    leads_with_sites = [l for l in leads if l.get("website")]
    if leads_with_sites:
        _log(f"📧 Finding emails for {len(leads_with_sites)} websites ...")

    for lead in leads_with_sites:
        _log(f"   ↳ {lead['business_name']} ({lead['website']}) ...")
        email = await find_email_from_website(lead["website"])
        if email:
            lead["email"] = email
            _log(f"     ✉️  {email}")

    found_email_count = sum(1 for l in leads if l.get("email"))
    _log(f"✅ Complete — {len(leads)} leads, {found_email_count} with email")

    return leads


async def find_email_from_website(website_url: str) -> Optional[str]:
    """
    Discover a contact email from a website.

    Uses requests + BeautifulSoup in a thread pool.
    Checks:
      1. mailto: href links on homepage (highest confidence)
      2. Regex scan of homepage visible text
      3. Common contact/about sub-pages (/contact, /about, etc.)

    Returns the first valid email found, or None.
    """
    if not website_url:
        return None
    return await asyncio.to_thread(_find_email_sync, website_url)


# ── Yelp scraper (sync — runs inside asyncio.to_thread) ───────────────────────

# Common US city hints for warning when city may not have Yelp/YP coverage
_US_HINTS = frozenset({
    "new york", "los angeles", "chicago", "houston", "phoenix", "philadelphia",
    "san antonio", "san diego", "dallas", "san jose", "austin", "jacksonville",
    "fort worth", "columbus", "charlotte", "indianapolis", "san francisco",
    "seattle", "denver", "boston", "nashville", "las vegas", "portland",
    "memphis", "baltimore", "louisville", "milwaukee", "albuquerque", "tucson",
    "fresno", "sacramento", "mesa", "atlanta", "omaha", "miami", "minneapolis",
    "tulsa", "cleveland", "raleigh", "virginia beach", "tampa", "new orleans",
    "nyc", "la", "sf", "dc", "washington",
})


def _is_us_city(city: str) -> bool:
    return any(hint in city.lower() for hint in _US_HINTS)


def _scrape_yelp_sync(
    niche: str,
    city: str,
    max_results: int,
    cfg: Dict[str, Any],
    log_fn: Callable[[str], None],
) -> List[Dict[str, Any]]:
    """Selenium-driven Yelp scraper. Returns leads tagged source='YELP'."""
    if not _SELENIUM_OK:
        log_fn("❌ Selenium not installed — cannot scrape Yelp")
        return []

    if not _is_us_city(city):
        log_fn(f"⚠️  Yelp is US-centric — '{city}' may return sparse results")

    niche_enc = urllib.parse.quote(niche)
    city_enc  = urllib.parse.quote(city)
    url       = f"https://www.yelp.com/search?find_desc={niche_enc}&find_loc={city_enc}"

    leads:  List[Dict[str, Any]] = []
    driver: Optional["webdriver.Chrome"] = None

    try:
        log_fn(f"⭐ Launching {'headless' if cfg['headless'] else 'visible'} Chrome for Yelp …")
        driver = _build_driver(cfg["headless"])
        driver.get(url)
        time.sleep(3)   # allow React to hydrate

        # ── Collect business page URLs from search results ────────────────────
        business_urls: List[str] = []
        link_selectors = [
            'h3 a[href*="/biz/"]',
            'a[href*="/biz/"]',
        ]

        def _gather_urls() -> None:
            for sel in link_selectors:
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                    for el in els:
                        href = el.get_attribute("href") or ""
                        if "/biz/" in href:
                            base = href.split("?")[0]
                            if base not in business_urls:
                                business_urls.append(base)
                    if business_urls:
                        return
                except WebDriverException:
                    continue

        _gather_urls()

        # Scroll to load more if needed
        for _ in range(4):
            if len(business_urls) >= max_results:
                break
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            _gather_urls()

        log_fn(f"⭐ Found {len(business_urls)} Yelp listing URLs — visiting each …")

        # ── Visit each business detail page ──────────────────────────────────
        for i, biz_url in enumerate(business_urls[:max_results]):
            try:
                driver.get(biz_url)
                time.sleep(random.uniform(cfg["delay_min"], cfg["delay_max"]))

                # Detect CAPTCHA / bot-block
                if "yelp.com/biz/" not in driver.current_url:
                    log_fn("⚠️  Yelp CAPTCHA/block detected — returning what was collected")
                    break

                # Name: first h1 on page
                name = ""
                try:
                    name = driver.find_element(By.CSS_SELECTOR, "h1").text.strip()
                except NoSuchElementException:
                    pass
                if not name:
                    continue

                # Phone: tel: href
                phone = ""
                try:
                    phone_els = driver.find_elements(By.CSS_SELECTOR, 'a[href^="tel:"]')
                    if not phone_els:
                        phone_els = driver.find_elements(
                            By.XPATH, '//a[starts-with(@href,"tel:")]'
                        )
                    if phone_els:
                        phone = (phone_els[0].get_attribute("href") or "").replace("tel:", "").strip()
                except WebDriverException:
                    pass

                # Website: biz_redir link → decode real URL
                website = ""
                try:
                    web_els = driver.find_elements(
                        By.CSS_SELECTOR, 'a[href*="biz_redir"], a[href*="redirect_url"]'
                    )
                    if web_els:
                        redir   = web_els[0].get_attribute("href") or ""
                        parsed  = urllib.parse.urlparse(redir)
                        qs      = urllib.parse.parse_qs(parsed.query)
                        website = qs.get("url", [""])[0] or redir
                except WebDriverException:
                    pass

                # Address
                address = ""
                for addr_sel in ('address', 'p[itemprop="address"]', 'span[itemprop="streetAddress"]'):
                    try:
                        el      = driver.find_element(By.CSS_SELECTOR, addr_sel)
                        address = el.text.strip().replace("\n", ", ")
                        if address:
                            break
                    except NoSuchElementException:
                        continue

                leads.append({
                    "business_name": name,
                    "phone":   phone   or None,
                    "website": website or None,
                    "email":   None,
                    "niche":   niche,
                    "city":    city,
                    "address": address or None,
                    "source":  "YELP",
                })
                log_fn(
                    f"✅ [{i + 1}/{min(max_results, len(business_urls))}] ⭐ Yelp: {name}"
                    + (f" — {phone}" if phone else "")
                )

            except (WebDriverException, StaleElementReferenceException):
                continue

        log_fn(f"🏁 Yelp done — {len(leads)} businesses scraped")

    except WebDriverException as exc:
        log_fn(f"❌ Yelp browser error: {exc}")

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return leads


# ── Yellow Pages scraper (sync — runs inside asyncio.to_thread) ───────────────

def _scrape_yellowpages_sync(
    niche: str,
    city: str,
    max_results: int,
    cfg: Dict[str, Any],
    log_fn: Callable[[str], None],
) -> List[Dict[str, Any]]:
    """Selenium-driven Yellow Pages scraper. Returns leads tagged source='YELLOW_PAGES'."""
    if not _SELENIUM_OK:
        log_fn("❌ Selenium not installed — cannot scrape Yellow Pages")
        return []

    if not _is_us_city(city):
        log_fn(f"⚠️  Yellow Pages is US-centric — '{city}' may return sparse results")

    niche_enc = urllib.parse.quote(niche)
    city_enc  = urllib.parse.quote(city)
    url       = (
        f"https://www.yellowpages.com/search"
        f"?search_terms={niche_enc}&geo_location_terms={city_enc}"
    )

    leads:  List[Dict[str, Any]] = []
    driver: Optional["webdriver.Chrome"] = None

    try:
        log_fn(f"📒 Launching {'headless' if cfg['headless'] else 'visible'} Chrome for Yellow Pages …")
        driver = _build_driver(cfg["headless"])

        page_num   = 1
        collected  = 0

        while collected < max_results:
            page_url = url if page_num == 1 else f"{url}&page={page_num}"
            driver.get(page_url)
            time.sleep(2)

            # Card selectors (tried in order)
            cards = []
            for card_sel in (".organic .result", ".result.organic", ".organic"):
                try:
                    cards = driver.find_elements(By.CSS_SELECTOR, card_sel)
                    if cards:
                        break
                except WebDriverException:
                    continue

            if not cards:
                log_fn("⚠️  No Yellow Pages result cards found — check niche/city spelling")
                break

            for card in cards:
                if collected >= max_results:
                    break
                try:
                    # Name
                    name = ""
                    for name_sel in (".business-name span", "h2.n span", ".business-name a"):
                        try:
                            name = card.find_element(By.CSS_SELECTOR, name_sel).text.strip()
                            if name:
                                break
                        except NoSuchElementException:
                            continue
                    if not name:
                        continue

                    # Phone — inline on YP cards, no click-through needed
                    phone = ""
                    for ph_sel in (".phones.phone.primary", ".phone.phones", ".phone"):
                        try:
                            phone = card.find_element(By.CSS_SELECTOR, ph_sel).text.strip()
                            if phone:
                                break
                        except NoSuchElementException:
                            continue

                    # Website
                    website = ""
                    try:
                        web_el  = card.find_element(By.CSS_SELECTOR, ".track-visit-website")
                        website = web_el.get_attribute("href") or ""
                    except NoSuchElementException:
                        pass

                    # Address
                    address = ""
                    try:
                        address = card.find_element(By.CSS_SELECTOR, ".adr").text.strip().replace("\n", ", ")
                    except NoSuchElementException:
                        pass

                    leads.append({
                        "business_name": name,
                        "phone":   phone   or None,
                        "website": website or None,
                        "email":   None,
                        "niche":   niche,
                        "city":    city,
                        "address": address or None,
                        "source":  "YELLOW_PAGES",
                    })
                    collected += 1
                    log_fn(
                        f"✅ [{collected}/{max_results}] 📒 YP: {name}"
                        + (f" — {phone}" if phone else "")
                    )
                    time.sleep(random.uniform(cfg["delay_min"] * 0.3, cfg["delay_max"] * 0.3))

                except (StaleElementReferenceException, WebDriverException):
                    continue

            if collected >= max_results:
                break

            # Paginate
            try:
                next_btn = driver.find_element(By.CSS_SELECTOR, "a.next.ajax-page, a[rel='next']")
                next_btn.click()
                page_num += 1
                time.sleep(2)
            except (NoSuchElementException, WebDriverException):
                break   # no more pages

        log_fn(f"🏁 Yellow Pages done — {len(leads)} businesses scraped")

    except WebDriverException as exc:
        log_fn(f"❌ Yellow Pages browser error: {exc}")

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return leads


# ── Multi-source orchestrator ─────────────────────────────────────────────────

async def scrape_multi_source(
    sources: List[str],
    niche: str,
    city: str,
    max_results: int,
    headless: bool = False,
    log_callback: Optional[Callable[[str], None]] = None,
    source_caps: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """
    Scrape leads from one or more sources sequentially (one browser at a time).

    If source_caps is provided it overrides the even budget split; each key maps
    a source id (e.g. "GOOGLE_MAPS") to its individual max_results limit.
    Cross-source deduplication by phone (primary) then business name (secondary).
    Email enrichment runs once on the combined result set.
    """
    if not sources:
        sources = ["GOOGLE_MAPS"]

    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    # ── Budget split ──────────────────────────────────────────────────────────
    n    = len(sources)
    base = max_results // n
    rem  = max_results % n
    default_budgets = [base + (1 if i < rem else 0) for i in range(n)]

    if source_caps:
        budgets = [source_caps.get(s, default_budgets[i]) for i, s in enumerate(sources)]
    else:
        budgets = default_budgets

    _log(f"🗂️  Sources: {sources}  |  budget split: {dict(zip(sources, budgets))}")

    # Headless param overrides the DB setting for this run
    cfg     = await _scraper_cfg()
    cfg_run = {**cfg, "headless": headless}

    from .scrapers.google_search import search_businesses as _gs_search
    from .scrapers import google_maps as _gm_scraper

    _SCRAPER_MAP: Dict[str, Tuple] = {
        "GOOGLE_MAPS":   (None,                     "🗺 Google Maps"),      # async — handled below
        "YELP":          (_scrape_yelp_sync,        "⭐ Yelp"),
        "YELLOW_PAGES":  (_scrape_yellowpages_sync, "📒 Yellow Pages"),
        "GOOGLE_SEARCH": (None,                     "🔍 Google Search"),   # async — handled below
    }

    all_leads: List[Dict[str, Any]] = []

    # ── Run each source sequentially ──────────────────────────────────────────
    for source, budget in zip(sources, budgets):
        if budget <= 0:
            continue

        entry = _SCRAPER_MAP.get(source)
        if not entry:
            _log(f"⚠️  Unknown source '{source}' — skipping")
            continue

        sync_fn, label = entry
        _log(f"━━━ Starting {label} (budget: {budget}) ━━━")

        if source == "GOOGLE_MAPS":
            batch: List[Dict] = await _gm_scraper.scrape(
                niche, city, max_results=budget, cfg=cfg_run, log_callback=_log
            )
        elif source == "GOOGLE_SEARCH":
            # Async scraper — call directly without to_thread
            batch = await _gs_search(niche, city, max_results=budget, log_callback=_log)
        else:
            batch = await asyncio.to_thread(
                sync_fn, niche, city, budget, cfg_run, _log
            )

        _log(f"   Collected {len(batch)} leads from {label}")
        all_leads.extend(batch)

    # ── Email enrichment (combined, avoids duplicate fetches) ─────────────────
    to_enrich = [l for l in all_leads if l.get("website") and not l.get("email")]
    if to_enrich:
        _log(f"📧 Finding emails for {len(to_enrich)} websites …")
    for lead in to_enrich:
        email = await find_email_from_website(lead["website"])
        if email:
            lead["email"] = email

    # ── Cross-source deduplication ────────────────────────────────────────────
    seen_phones: set = set()
    seen_names:  set = set()
    deduped: List[Dict[str, Any]] = []

    for lead in all_leads:
        phone = (lead.get("phone") or "").strip()
        name  = (lead.get("business_name") or "").strip().lower()

        if phone and phone in seen_phones:
            _log(f"   ⏭️  Dedup (phone): {lead.get('business_name')}")
            continue
        if name and name in seen_names:
            _log(f"   ⏭️  Dedup (name): {lead.get('business_name')}")
            continue

        if phone:
            seen_phones.add(phone)
        if name:
            seen_names.add(name)
        deduped.append(lead)

    removed = len(all_leads) - len(deduped)
    _log(
        f"✅ Multi-source complete — {len(deduped)} unique leads"
        + (f" ({removed} duplicates removed)" if removed else "")
    )
    return deduped
