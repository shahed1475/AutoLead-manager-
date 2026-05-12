"""
Bing Search lead scraper — Browser-based Bing scraping + DDG fallback.

Strategy
--------
Phase 1 — Browser-based Bing scraping (primary):
  Uses Playwright to open Bing in a real browser, extract search results.
  Logs every step: BROWSER_START, NAVIGATE_BING, PAGE_LOADED, RESULTS_FOUND, etc.
  Takes screenshot/HTML dump on failure.

Phase 2 — DDG fallback (backup):
  If browser fails, falls back to DuckDuckGo URL collection.

Phase 3 — Website contact extraction:
  Visit each collected URL; extract name, email, phone.
  Scan /contact, /about sub-pages when homepage is incomplete.

Threading model: sync core runs inside asyncio.to_thread().
log_callback is a plain sync callable.
"""

import asyncio
import json
import logging
import random
import re
import time
import urllib.parse
import urllib3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import requests
from bs4 import BeautifulSoup

from ..validators import clean_email, clean_phone
from ._shared import ddg_collect_urls, expand_niche

# ── Bing redirect URL decoder ─────────────────────────────────────────────────

def _decode_bing_redirect(url: str) -> str:
    """Decode Bing redirect URL to get actual destination."""
    if not url:
        return ""
    # Bing redirect format: /ck/a?!&&p=xxx&u=base64_encoded_url
    if "/ck/a" in url or "bing.com/ck" in url:
        try:
            # Parse URL - handle special chars in query
            import re
            # Extract u parameter using regex for more reliable parsing
            u_match = re.search(r'[?&]u=([^&]+)', url)
            if u_match:
                u_param = u_match.group(1)
                # Try base64 decode first
                try:
                    import base64
                    decoded_bytes = base64.b64decode(u_param)
                    decoded = decoded_bytes.decode('utf-8')
                    if decoded.startswith("http"):
                        return decoded
                except Exception:
                    pass
                # Try URL decode
                decoded = urllib.parse.unquote(u_param)
                if decoded.startswith("http"):
                    return decoded
        except Exception as e:
            pass
    # If not a redirect, return as-is if it's a valid URL
    if url.startswith("http"):
        return url
    return ""


# ── Playwright browser function ────────────────────────────────────────────────

async def _search_with_browser(
    query: str,
    log_fn: Callable[[str], None],
    max_results: int = 30,
) -> List[Dict[str, Any]]:
    """
    Search Bing using Playwright browser and extract business leads.
    Logs every step: BROWSER_START, NAVIGATE_BING, PAGE_LOADED, RESULTS_FOUND, etc.
    """
    results: List[Dict[str, Any]] = []
    playwright = None
    browser = None
    context = None
    page = None

    # Debug directory
    debug_dir = Path(__file__).parent.parent / "data" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    def save_debug(prefix: str, content: str, suffix: str = "html") -> str:
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = debug_dir / f"{prefix}_{ts}.{suffix}"
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content[:500000] if len(content) > 500000 else content, encoding="utf-8")
            return str(path)
        except Exception as e:
            log_fn(f"[DEBUG] Save failed: {e}")
            return ""

    try:
        from playwright.async_api import async_playwright

        log_fn("[BROWSER_START] Starting Playwright Chromium...")
        playwright = await async_playwright().start()

        # Launch browser - headless=False to avoid anti-bot detection
        # Note: headless mode is blocked by Bing's bot detection
        browser = await playwright.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--disable-extensions",
            ]
        )
        log_fn("[BROWSER_START] Chromium launched!")

        # Create context with stealth settings
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        """)

        page = await context.new_page()

        # Navigate to Bing
        encoded_query = urllib.parse.quote_plus(query)
        target_url = f"https://www.bing.com/search?q={encoded_query}"
        log_fn(f"[NAVIGATE_BING] Navigating to: {target_url[:80]}...")

        await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        log_fn("[NAVIGATE_BING] Navigation complete!")

        # Wait for body to be visible first
        await page.wait_for_selector("body", timeout=10000)
        log_fn("[PAGE_LOADED] Page loaded successfully!")

        # Wait for dynamic content to load - Bing hides content until JS executes
        try:
            await page.wait_for_function("""
                () => {
                    const bContent = document.getElementById('b_content');
                    if (bContent) {
                        const style = window.getComputedStyle(bContent);
                        return style.visibility !== 'hidden' || document.querySelector('#b_results, li.b_algo');
                    }
                    return document.querySelector('#b_results, li.b_algo') !== null;
                }
            """, timeout=15000)
            log_fn("[PAGE_LOADED] Dynamic content rendered!")
        except Exception as e:
            log_fn(f"[PAGE_LOADED] Wait for dynamic content: {e}")

        # Additional wait for late-loading content
        await page.wait_for_timeout(3000)

        # Debug: get page title
        page_title = await page.title()
        log_fn(f"[DEBUG] Page title: {page_title}")

        # Debug: check what's in body
        body_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 200) : 'no body'")
        log_fn(f"[DEBUG] Body text preview: {body_text[:100]}...")

        # Check for CAPTCHA/challenge
        if "captcha" in body_text.lower() or "verify" in body_text.lower() or "challenge" in body_text.lower():
            log_fn("[WARNING] CAPTCHA/challenge detected - Bing is blocking the browser")
            save_debug("bing_captcha", body_text)
            # Fall through to DDG fallback

        # Get page content first for debugging
        page_content = await page.content()

        # Execute JavaScript to extract results directly from the DOM
        # This works better than page.content() because it gets the rendered DOM
        log_fn("[PARSING] Starting JavaScript extraction...")
        try:
            # First verify what's in the DOM
            count_check = await page.evaluate("() => document.querySelectorAll('li').length")
            log_fn(f"[PARSING] Total <li> elements: {count_check}")

            extracted_data = await page.evaluate("""
                () => {
                    const results = [];
                    const items = document.querySelectorAll('li.b_algo');
                    console.log('Found ' + items.length + ' b_algo items');
                    for (const item of items) {
                        const titleEl = item.querySelector('h2 a, h2');
                        const title = titleEl ? titleEl.textContent.trim() : '';
                        let url = '';
                        const linkEl = item.querySelector('h2 a');
                        if (linkEl) {
                            url = linkEl.href || linkEl.getAttribute('href') || '';
                        }
                        const snippetEl = item.querySelector('.b_caption p, .b_snippet');
                        const snippet = snippetEl ? snippetEl.textContent.trim() : '';
                        if (title) {
                            results.push({
                                business_name: title.substring(0, 200),
                                website: url,
                                snippet: snippet.substring(0, 500)
                            });
                        }
                    }
                    console.log('Returning ' + results.length + ' results');
                    return JSON.stringify(results);
                }
            """)
            log_fn(f"[PARSING] Raw extract result: {extracted_data[:200] if extracted_data else 'empty'}")
            if extracted_data and extracted_data.startswith('['):
                extracted_data = json.loads(extracted_data)
                log_fn(f"[PARSING_DONE] Extracted {len(extracted_data)} raw results via JavaScript")

                # Process and decode URLs
                for item in extracted_data:
                    url = item.get("website", "")
                    if url:
                        is_redirect = "/ck/a" in url or "bing.com/ck" in url
                        if is_redirect:
                            decoded = _decode_bing_redirect(url)
                            if decoded:
                                url = decoded
                                log_fn(f"[DEBUG] Decoded: {url[:60]}...")
                        # Also check if URL is still a redirect that starts with https://www.bing.com
                        elif url.startswith("https://www.bing.com"):
                            url = _decode_bing_redirect(url)

                    item["website"] = url
                    item["source"] = "bing"
                    results.append(item)
                log_fn(f"[RESULTS_FOUND] {len(results)} unique business leads extracted")
                if results:
                    save_debug("bing_success", json.dumps(results, indent=2), "json")
                else:
                    save_debug("bing_no_results", page_content[:50000])
                return results
        except Exception as e:
            log_fn(f"[PARSING] JavaScript extraction failed: {e}")
            # Fall back to page.content() approach
        log_fn(f"[DEBUG] Page content length: {len(page_content)} chars")

        # Check for results container
        has_b_results = await page.locator("#b_results").count()
        has_b_algo = await page.locator("li.b_algo").count()
        log_fn(f"[DEBUG] #b_results count: {has_b_results}, li.b_algo count: {has_b_algo}")

        if has_b_results > 0 or has_b_algo > 0:
            log_fn("[RESULTS_FOUND] Results container found!")
        else:
            log_fn("[RESULTS_FOUND] Results container not found, checking page...")

        # Debug: Save page content
        debug_path = save_debug("bing_page_content", page_content[:100000])
        log_fn(f"[DEBUG] Saved page content for analysis: {debug_path}")

        page_lower = page_content.lower()

        # More specific blocking phrases
        blocking = ["unusual traffic", "verify you are human", "captcha challenge", "we're sorry"]
        for b in blocking:
            if b in page_lower:
                log_fn(f"[WARNING] Anti-bot detected: found '{b}'")
                save_debug("bing_blocked", page_content[:50000])
                return results

        # More specific blocking phrases
        blocking = ["unusual traffic", "verify you are human", "captcha challenge", "we're sorry"]
        for b in blocking:
            if b in page_lower:
                log_fn(f"[WARNING] Anti-bot detected: found '{b}'")
                save_debug("bing_blocked", page_content[:50000])
                return results

        # Wait for results container (Bing results are in 'li.b_algo' or '#b_results')
        try:
            await page.wait_for_selector("#b_results, li.b_algo", timeout=8000)
            log_fn("[RESULTS_FOUND] Bing results container detected!")
        except Exception:
            log_fn("[RESULTS_FOUND] No standard container found, trying alternate selectors...")

        # Extract results using multiple selectors
        # Primary: li.b_algo (organic results)
        # Secondary: .b_entity (business cards)
        # Tertiary: .b_ans (answer box)

        extracted_data = []

        try:
            # Try main result items
            result_items = await page.query_selector_all("li.b_algo")
            log_fn(f"[PARSING_START] Found {len(result_items)} result items")

            for item in result_items[:max_results]:
                try:
                    # Extract title and URL
                    title_el = await item.query_selector("h2 a, h2")
                    title = await title_el.text_content() if title_el else ""

                    url_el = await item.query_selector("h2 a")
                    url = await url_el.get_attribute("href") if url_el else ""

                    # Extract snippet
                    snippet_el = await item.query_selector(".b_caption p, .b_snippet")
                    snippet = await snippet_el.text_content() if snippet_el else ""

                    if title and url:
                        log_fn(f"[DEBUG] Raw URL: {url[:80]}...")
                        # URL might be redirect - use JavaScript to get resolved URL
                        if url_el and ("/ck/a" in url or "bing.com/ck" in url):
                            try:
                                resolved = await url_el.evaluate("el => el.href")
                                if resolved and resolved.startswith("http"):
                                    url = resolved
                            except:
                                pass

                        if url.startswith("http"):
                            log_fn(f"[DEBUG] Extracted: {title[:30]} -> {url[:60]}")
                            extracted_data.append({
                            "business_name": title.strip()[:200],
                            "website": url.strip(),
                            "snippet": snippet.strip()[:500] if snippet else "",
                        })
                except Exception as e:
                    continue
        except Exception as e:
            log_fn(f"[PARSING] Error parsing main results: {e}")

        # Try business entity cards if no results yet
        if not extracted_data:
            try:
                entity_items = await page.query_selector_all(".b_entity, [class*='biz'], [class*='business']")
                log_fn(f"[PARSING] Found {len(entity_items)} business entities")

                for item in entity_items[:max_results]:
                    try:
                        title_el = await item.query_selector("h2, h3, [class*='title']")
                        title = await title_el.text_content() if title_el else ""

                        url_el = await item.query_selector("a[href]")
                        # Use evaluate to get full href
                        if url_el:
                            url = await url_el.evaluate("el => el.href")
                        else:
                            url = ""

                        # Decode Bing redirect URL if needed
                        if url:
                            is_redirect = "/ck/a" in url or "bing.com/ck" in url
                            if is_redirect:
                                url = _decode_bing_redirect(url)

                        if title and url:
                            extracted_data.append({
                                "business_name": title.strip()[:200],
                                "website": url.strip() if url.startswith("http") else "",
                                "snippet": "",
                            })
                    except:
                        continue
            except Exception as e:
                log_fn(f"[PARSING] Error parsing business entities: {e}")

        log_fn(f"[PARSING_DONE] Extracted {len(extracted_data)} raw results")

        # Deduplicate and filter
        seen = set()
        for item in extracted_data:
            if item["website"]:
                domain = urllib.parse.urlparse(item["website"]).netloc.lower().lstrip("www.")
                if domain and domain not in seen:
                    seen.add(domain)
                    item["source"] = "bing"
                    results.append(item)

        log_fn(f"[RESULTS_FOUND] {len(results)} unique business leads extracted")

        # Save debug info
        if results:
            save_debug("bing_success", json.dumps(results, indent=2), "json")
        else:
            save_debug("bing_no_results", page_content[:50000])

    except Exception as e:
        log_fn(f"[ERROR_CAPTURED] Browser search failed: {e}")
        import traceback
        tb = traceback.format_exc()
        log_fn(f"[ERROR_CAPTURED] {tb[:500]}")

        if page:
            try:
                html_path = save_debug("bing_error", await page.content()[:50000])
                log_fn(f"[DEBUG] Error HTML saved: {html_path}")
            except:
                pass

    finally:
        # Cleanup
        try:
            if page:
                await page.close()
            if context:
                await context.close()
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()
        except Exception as e:
            log_fn(f"[CLEANUP] Error: {e}")

    return results

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

# ── Debug output directory ─────────────────────────────────────────────────────
DEBUG_DIR = Path(__file__).parent.parent / "data" / "debug"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def _save_debug_file(prefix: str, content: str, suffix: str = "html") -> str:
    """Save debug content to file and return path."""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = DEBUG_DIR / f"{prefix}_{ts}.{suffix}"
        path.write_text(content, encoding="utf-8")
        return str(path)
    except Exception as e:
        logger.error(f"Failed to save debug file: {e}")
        return ""


# ── fake_useragent — graceful degradation ─────────────────────────────────────

try:
    from fake_useragent import UserAgent as _FakeUA
    _UA_GEN   = _FakeUA(
        fallback="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        browsers=["Chrome", "Firefox", "Edge"],
    )
    _FAKE_UA_OK = True
except Exception:
    _UA_GEN     = None
    _FAKE_UA_OK = False


# ── Constants ─────────────────────────────────────────────────────────────────

_FALLBACK_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

_CONTACT_PATHS: List[str] = [
    "/contact", "/contact-us", "/contacts",
    "/about",   "/about-us",
    "/team",    "/our-team",
    "/reach-us", "/get-in-touch",
]

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:\+?\d{1,3}[\s.\-]?)?"
    r"(?:\(?\d{2,4}\)?[\s.\-]?)?"
    r"\d{3,4}[\s.\-]\d{3,4}"
    r"(?:[\s.\-]\d{1,4})?"
    r"(?!\d)"
)


# ── UA / URL helpers ───────────────────────────────────────────────────────────

def _get_ua() -> str:
    if _FAKE_UA_OK and _UA_GEN:
        try:
            return _UA_GEN.random
        except Exception:
            pass
    return random.choice(_FALLBACK_UAS)


def _is_junk(url: str) -> bool:
    _JUNK = frozenset({
        "bing", "microsoft", "msn", "live", "hotmail", "outlook",
        "google", "youtube", "facebook", "twitter", "x.com", "instagram",
        "linkedin", "wikipedia", "yelp", "tripadvisor", "yellowpages",
        "bbb.org", "amazon", "apple", "yahoo", "pinterest", "reddit",
        "tiktok", "foursquare", "mapquest", "trustpilot", "indeed",
        "glassdoor", "bark.com", "houzz", "thumbtack", "homeadvisor",
        "angi.com", "angieslist", "checkatrade", "ratedpeople",
        "zomato", "justeat", "ubereats", "doordash", "airbnb",
        "booking.com", "expedia", "hotels.com",
    })
    try:
        domain = urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
        return any(junk in domain for junk in _JUNK)
    except Exception:
        return True


def _get_domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return url


def _base_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _build_headers(ua: str, referer: Optional[str] = None) -> Dict[str, str]:
    h = {
        "User-Agent":                ua,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "DNT":                       "1",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "cross-site" if referer else "none",
        "Sec-Fetch-User":            "?1",
        "Cache-Control":             "max-age=0",
    }
    if referer:
        h["Referer"] = referer
    return h


# ── Bing local-pack extraction (bonus — non-critical) ─────────────────────────

def _try_bing_local_pack(
    niche:   str,
    city:    str,
    session: requests.Session,
    log_fn:  Callable[[str], None],
) -> List[Dict[str, Any]]:
    """
    Attempt one Bing request to harvest local-pack cards.
    Returns [] on any block/error — treated as a bonus, not required.
    """
    ua    = _get_ua()
    q_enc = urllib.parse.quote_plus(f"{niche} {city}")
    url   = f"https://www.bing.com/search?q={q_enc}&first=1&count=10&form=HDRSC3&mkt=en-US"
    try:
        resp = session.get(
            url,
            headers=_build_headers(ua, referer="https://www.bing.com/"),
            timeout=12,
            verify=False,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        body = resp.text.lower()
        if "unusual traffic" in body or 'id="captcha"' in body or "verify you are human" in body:
            return []
    except Exception:
        return []

    soup  = BeautifulSoup(resp.text, "lxml")
    leads = []

    # JSON-LD local businesses
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            t = item.get("@type", "")
            if not any(k in t for k in ("LocalBusiness", "Organization", "Store",
                                         "MedicalBusiness", "LegalService")):
                continue
            name = (item.get("name") or "").strip()
            if not name:
                continue
            phone   = clean_phone(str(item.get("telephone") or ""))
            website = (item.get("url") or "").strip()
            addr    = item.get("address", {}) or {}
            address = (
                addr.get("streetAddress", "") + " " +
                addr.get("addressLocality", "") + " " +
                addr.get("addressRegion", "")
            ).strip() if isinstance(addr, dict) else str(addr)
            leads.append({
                "business_name": name,
                "phone":   phone   or None,
                "website": website or None,
                "email":   None,
                "address": address or None,
                "niche":   niche,
                "city":    city,
                "source":  "BING_SEARCH",
            })

    # li.b_lEntry HTML cards
    for entry in soup.select("li.b_lEntry, .b_loclist li"):
        name = ""
        for sel in ("h2.b_lTitle a", "h3.b_lTitle a", ".b_lTitle a", "h2 a", "h3 a"):
            el = entry.select_one(sel)
            if el:
                name = el.get_text(strip=True)
                break
        if not name:
            continue
        if any(l["business_name"].lower() == name.lower() for l in leads):
            continue

        phone = ""
        for sel in (".b_phone", "span.b_phone", "[class*='phone']"):
            el = entry.select_one(sel)
            if el:
                phone = clean_phone(el.get_text(strip=True)) or ""
                if phone:
                    break

        website = ""
        for sel in (".b_lSite a", ".b_website a", "a[class*='website']"):
            el = entry.select_one(sel)
            if el:
                href = el.get("href", "")
                if href.startswith("http") and not _is_junk(href):
                    website = href
                    break

        address = ""
        for sel in ("div.b_address", ".b_address", "[class*='address']"):
            el = entry.select_one(sel)
            if el:
                address = el.get_text(strip=True)
                break

        leads.append({
            "business_name": name,
            "phone":   phone   or None,
            "website": website or None,
            "email":   None,
            "address": address or None,
            "niche":   niche,
            "city":    city,
            "source":  "BING_SEARCH",
        })

    if leads:
        log_fn(f"  🗺️  Bing local pack: {len(leads)} business(es) extracted directly")
    return leads


# ── Per-website contact extractor ──────────────────────────────────────────────

def _site_get(
    url:     str,
    session: requests.Session,
    ua:      str,
    timeout: int = 8,
) -> Optional[requests.Response]:
    try:
        resp = session.get(
            url,
            headers=_build_headers(ua),
            timeout=timeout,
            verify=False,
            allow_redirects=True,
        )
        return resp if resp.status_code < 400 else None
    except requests.RequestException:
        return None


def _find_email(soup: BeautifulSoup) -> Optional[str]:
    for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
        raw   = a["href"].replace("mailto:", "").split("?")[0]
        email = clean_email(raw)
        if email:
            return email
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    for match in _EMAIL_RE.findall(text):
        email = clean_email(match)
        if email:
            return email
    return None


def _find_phone(soup: BeautifulSoup) -> Optional[str]:
    for a in soup.find_all("a", href=re.compile(r"^tel:", re.I)):
        raw   = a["href"].replace("tel:", "").strip()
        phone = clean_phone(raw)
        if phone:
            return phone
    for el in soup.find_all(attrs={"itemprop": "telephone"}):
        raw   = (el.get("content") or el.get_text(strip=True) or "")
        phone = clean_phone(raw)
        if phone:
            return phone
    text = soup.get_text(separator=" ")
    for match in _PHONE_RE.finditer(text):
        phone = clean_phone(match.group())
        if phone:
            return phone
    return None


def _extract_contact_info(
    url:     str,
    session: requests.Session,
    ua:      str,
) -> Dict[str, Any]:
    info: Dict[str, Any] = {"business_name": None, "email": None, "phone": None}
    base = _base_url(url)

    resp = _site_get(url, session, ua)
    if not resp:
        return info

    soup = BeautifulSoup(resp.text, "lxml")

    # JSON-LD first (most reliable)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data  = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                t = item.get("@type", "")
                if isinstance(t, list):
                    t = " ".join(t)
                biz_types = ("LocalBusiness", "Organization", "Store", "Restaurant",
                             "MedicalBusiness", "Service", "LegalService", "Establishment")
                if not any(k in t for k in biz_types):
                    continue
                if not info["business_name"]:
                    info["business_name"] = (item.get("name") or "").strip()[:200] or None
                if not info["phone"]:
                    info["phone"] = clean_phone(str(item.get("telephone") or "")) or None
                if not info["email"]:
                    info["email"] = clean_email(str(item.get("email") or "")) or None
        except Exception:
            pass

    # Name from h1 / title
    if not info["business_name"]:
        h1 = soup.find("h1")
        if h1:
            info["business_name"] = h1.get_text(strip=True)[:120]
        else:
            title = soup.find("title")
            if title:
                raw = title.get_text(strip=True)
                info["business_name"] = re.split(r"[\|–—]{1,2}|-{2,}", raw)[0].strip()[:120]

    if not info["email"]:
        info["email"] = _find_email(soup)
    if not info["phone"]:
        info["phone"] = _find_phone(soup)

    if info["email"] and info["phone"]:
        return info

    # Sub-page scan
    for path in _CONTACT_PATHS[:5]:
        if info["email"] and info["phone"]:
            break
        sub_resp = _site_get(base + path, session, ua, timeout=7)
        if not sub_resp:
            continue
        sub_soup = BeautifulSoup(sub_resp.text, "lxml")
        if not info["email"]:
            info["email"] = _find_email(sub_soup)
        if not info["phone"]:
            info["phone"] = _find_phone(sub_soup)
        time.sleep(random.uniform(0.5, 1.5))

    return info


# ── Core sync scraper ──────────────────────────────────────────────────────────

def scrape_sync(
    niche:     str,
    city:      str,
    country:   str,
    max_leads: int,
    log_fn:    Callable[[str], None],
) -> List[Dict[str, Any]]:
    """
    Phase 1: Browser-based Bing search (primary) with DDG fallback.
    Phase 2: Visit each URL to extract business_name, email, phone.
    """
    # Build queries using niche synonyms for broader coverage
    variants = expand_niche(niche)[:3]
    queries: List[str] = []
    for v in variants:
        queries.append(f"{v} {city}")
        queries.append(f"best {v} in {city}")
    if country:
        queries.append(f"{niche} {city} {country}")
    queries = queries[:4]  # Limit queries for browser-based search

    log_fn(f"🔵 Bing Browser: {len(queries)} queries for '{niche}' in '{city}'")

    url_pool:    Dict[str, str]       = {}
    browser_leads: List[Dict[str, Any]] = []

    # Phase 1: Try browser-based Bing search
    browser_success = False
    try:
        # Run browser search in event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            browser_results = loop.run_until_complete(
                _search_with_browser(queries[0], log_fn, max_results=max_leads)
            )
            if browser_results:
                browser_success = True
                for r in browser_results:
                    domain = _get_domain(r.get("website", ""))
                    if domain and domain not in url_pool:
                        url_pool[domain] = r["website"]
                        browser_leads.append(r)
                log_fn(f"[BROWSER] Got {len(browser_leads)} leads from browser search")
        finally:
            loop.close()
    except Exception as e:
        log_fn(f"[BROWSER] Browser search failed: {e}")
        log_fn("[BROWSER] Falling back to DDG...")

    # Fallback: DDG URL collection if browser didn't work
    if not browser_success or len(url_pool) < max_leads:
        ddg_sess = requests.Session()
        remaining_queries = queries[1:] if browser_success else queries

        for idx, query in enumerate(remaining_queries):
            if len(url_pool) >= max_leads * 2:
                break
            log_fn(f"  [{idx + 1}/{len(remaining_queries)}] DDG: \"{query}\"")
            urls = ddg_collect_urls(query, ddg_sess, log_fn, max_urls=20)
            new = 0
            for u in urls:
                d = _get_domain(u)
                if d and d not in url_pool:
                    url_pool[d] = u
                    new += 1
            log_fn(f"     → {len(urls)} URLs ({new} new domains)")
            if idx < len(remaining_queries) - 1:
                time.sleep(random.uniform(1.5, 3.0))

    log_fn(
        f"📊 SOURCE: BING_SEARCH | Pool: {len(url_pool)} domains | "
        f"Browser leads: {len(browser_leads)} | "
        f"Visiting top {min(max_leads, len(url_pool))} sites"
    )

    # Phase 2: contact extraction
    site_leads:  List[Dict[str, Any]] = []
    site_session = requests.Session()
    target_urls  = list(url_pool.values())[:max_leads]

    for i, url in enumerate(target_urls, 1):
        log_fn(f"🌐 [{i}/{len(target_urls)}] {url}")
        ua   = _get_ua()
        info = _extract_contact_info(url, site_session, ua)

        lead = {
            "business_name": info["business_name"] or _get_domain(url),
            "phone":   info["phone"],
            "website": url,
            "email":   info["email"],
            "address": None,
            "niche":   niche,
            "city":    city,
            "country": country or None,
            "source":  "BING_SEARCH",
        }
        site_leads.append(lead)
        log_fn(
            f"  ✅ {lead['business_name']}"
            + (f" | ✉️  {info['email']}" if info["email"] else "")
            + (f" | 📞 {info['phone']}"  if info["phone"] else "")
        )
        time.sleep(random.uniform(2.0, 4.5))

    all_leads = browser_leads + site_leads
    email_cnt = sum(1 for l in all_leads if l.get("email"))
    phone_cnt = sum(1 for l in all_leads if l.get("phone"))
    log_fn(
        f"🏁 Bing done | RAW LEADS: {len(all_leads)} | "
        f"VALID LEADS: {sum(1 for l in all_leads if l.get('email') or l.get('phone') or l.get('website'))} | "
        f"{email_cnt} emails | {phone_cnt} phones"
    )
    return all_leads


# ── Public async API ───────────────────────────────────────────────────────────

async def scrape_bing_search(
    niche:        str,
    city:         str,
    country:      str = "",
    max_leads:    int = 30,
    log_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass
    _log(f"🚀 Bing Search scraper: {niche} in {city} (max {max_leads})")
    return await asyncio.to_thread(scrape_sync, niche, city, country, max_leads, _log)


async def scrape(
    niche:        str,
    city:         str,
    max_results:  int  = 30,
    cfg:          Optional[Dict[str, Any]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    country:      str  = "",
) -> List[Dict[str, Any]]:
    return await scrape_bing_search(
        niche=niche, city=city, country=country,
        max_leads=max_results, log_callback=log_callback,
    )
