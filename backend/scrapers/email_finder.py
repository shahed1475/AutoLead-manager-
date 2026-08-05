"""
email_finder.py — Deep multi-strategy email & phone discovery for a business website.

Strategies applied (in order, to every fetched page):
  1. Mailto links    — <a href="mailto:..."> tags — highest confidence
  2. Footer scan     — <footer> element specifically (owner emails live here)
  3. Visible text    — regex over stripped page text after removing scripts
  4. JS source scan  — raw HTML source including deobfuscation:
                         • HTML entity decode  (&#64; → @, &#46; → .)
                         • String concatenation  ("a" + "@" + "b.com")
                         • Base64 / atob()  (atob("Y29udGFjdEBleGFtcGxlLmNvbQ=="))
                         • Unicode escapes  (con...)
  5. Sub-pages       — /contact, /contact-us, /about, /team … (separate fetches)
  6. WHOIS bonus     — python-whois registrant email (non-fatal if missing)

Email quality ranking:
  Tier 1 (preferred as primary)  : owner, founder, ceo, director, hello, contact, …
  Tier 2 (accepted as primary)   : admin, office, info, sales, …
  Tier 3 (saved to all_emails)   : noreply, support, newsletter, … (never primary)

Return value:
  {
    "primary_email":    str | None,
    "all_emails":       list[str],
    "phone_numbers":    list[str],
    "owner_name":       str | None,
    "contact_page_url": str | None,
  }

Threading model
---------------
All I/O is synchronous (requests).  The public async entry-points run inside
asyncio.to_thread() so FastAPI is never blocked.  log_callback is a plain sync
callable — the caller bridges it to async via run_coroutine_threadsafe when
needed.
"""

import asyncio
import base64
import html as _html_module
import logging
import random
import re
import time
import urllib.parse
import urllib3
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError

_WHOIS_TIMEOUT_SECONDS = 8
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup

from ..validators import clean_email, clean_phone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


# ── fake_useragent (graceful degradation) ────────────────────────────────────

try:
    from fake_useragent import UserAgent as _FakeUA

    _UA_GEN = _FakeUA(
        fallback=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        browsers=["Chrome", "Firefox", "Edge"],
    )
    _FAKE_UA_OK = True
except Exception:
    _UA_GEN     = None
    _FAKE_UA_OK = False

_FALLBACK_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


# ── Constants ─────────────────────────────────────────────────────────────────

# Sub-paths checked for contact info (ordered by likelihood of containing an email)
_CONTACT_PATHS: List[str] = [
    "/contact",
    "/contact-us",
    "/get-in-touch",
    "/reach-us",
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/staff",
]

# Tier 1: preferred as primary_email
_PREFERRED_PREFIXES: frozenset = frozenset({
    "owner", "founder", "ceo", "director", "president",
    "partner", "principal", "managing",
    "hello", "hi", "hey",
    "contact", "enquiry", "enquiries", "inquiry", "inquiries",
    "business", "commercial", "bizdev",
})

# Tier 2: acceptable as primary_email if nothing better found
_ACCEPTABLE_PREFIXES: frozenset = frozenset({
    "admin", "office", "info", "mail", "email",
    "sales", "marketing", "service", "services",
    "media", "pr", "press",
})

# Tier 3: save to all_emails but never use as primary
_DEPRIORITIZED_PREFIXES: frozenset = frozenset({
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "support", "help", "helpdesk",
    "newsletter", "notifications", "notification",
    "unsubscribe", "bounce", "mailer-daemon", "postmaster",
    "abuse", "spam", "privacy", "legal",
    "webmaster", "hostmaster", "root",
    "automated", "auto", "autoresponder",
})

# WHOIS emails that are privacy-proxy addresses (discard)
_WHOIS_PRIVACY_TOKENS: frozenset = frozenset({
    "whoisguard", "privacyprotect", "domainsbyproxy", "whoisprivacy",
    "contactprivacy", "withheldforprivacy", "registrant.eu",
    "networksolutions", "netsol", "redacted", "identity-protect",
    "domainprivacy", "privacydomain", "anonymize",
})

# Regex patterns
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:\+\d{1,3}[\s.\-]?)?"          # optional country code
    r"(?:\(?\d{2,4}\)?[\s.\-]?)?"      # optional area code
    r"\d{3,4}[\s.\-]\d{3,4}"           # main body (separator required)
    r"(?:[\s.\-]\d{1,4})?"             # optional extension
    r"(?!\d)"
)

# JS deobfuscation patterns
_JS_CONCAT_RE = re.compile(
    # "local" + "@" + "domain.tld"  or  "local" + "@domain.tld"
    r'"([a-zA-Z0-9._%+\-]{1,40})"\s*\+\s*"@"\s*\+\s*"([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})"'
    r'|'
    r'"([a-zA-Z0-9._%+\-]{1,40})"\s*\+\s*"(@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})"'
)
_ATOB_RE = re.compile(r"""atob\(["']([A-Za-z0-9+/=]{8,})["']\)""")
_UNICODE_ESC_RE = re.compile(r'(?:\\u[0-9a-fA-F]{4})+')
_HTML_ENTITY_EMAIL_HINT = re.compile(r"&#(?:64|x40);")   # quick check before full decode


# ── UA helper ─────────────────────────────────────────────────────────────────

def _get_ua() -> str:
    if _FAKE_UA_OK and _UA_GEN:
        try:
            return _UA_GEN.random
        except Exception:
            pass
    return random.choice(_FALLBACK_UAS)


# ── URL utilities ─────────────────────────────────────────────────────────────

def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _base_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _get_domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return url


def _build_headers(ua: str) -> Dict[str, str]:
    return {
        "User-Agent":                ua,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "DNT":                       "1",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _safe_get(
    url:     str,
    session: requests.Session,
    ua:      str,
    timeout: int = 10,
) -> Tuple[Optional[requests.Response], str]:
    """
    Fetch a URL with SSL tolerance and encoding detection.
    Returns (response, raw_html_text).  raw_html_text is "" on failure.
    """
    try:
        resp = session.get(
            url,
            headers=_build_headers(ua),
            timeout=timeout,
            verify=False,
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            return None, ""

        # Encoding: respect Content-Type charset, then apparent_encoding fallback
        encoding = resp.encoding or resp.apparent_encoding or "utf-8"
        try:
            text = resp.content.decode(encoding, errors="replace")
        except (LookupError, TypeError):
            text = resp.content.decode("utf-8", errors="replace")

        return resp, text

    except requests.exceptions.SSLError:
        # Retry without verify=False already set — shouldn't happen, but belt + braces
        return None, ""
    except requests.exceptions.Timeout:
        return None, ""
    except requests.RequestException:
        return None, ""


# ── JS deobfuscation ──────────────────────────────────────────────────────────

def _decode_unicode_escapes(text: str) -> str:
    """Decode \\uXXXX sequences in a JS string."""
    def _repl(m: re.Match) -> str:
        try:
            return m.group(0).encode("utf-8").decode("unicode_escape")
        except Exception:
            return m.group(0)

    return _UNICODE_ESC_RE.sub(_repl, text)


def _extract_js_emails(raw_html: str) -> List[str]:
    """
    Mine the raw HTML source (including <script> blocks) for obfuscated emails.

    Handles:
      a) HTML entity encoding  (&#64; → @)
      b) String concatenation  ("user" + "@" + "example.com")
      c) Base64 / atob()       (atob("..."))
      d) Unicode escapes       (\\u0063\\u006f\\u006e...)
      e) Plain emails in JS    (var email = "user@example.com")
    """
    found: List[str] = []

    # a) HTML entity decode — check for &#64; hint before full decode
    if _HTML_ENTITY_EMAIL_HINT.search(raw_html):
        decoded = _html_module.unescape(raw_html)
        for match in _EMAIL_RE.findall(decoded):
            e = clean_email(match)
            if e and e not in found:
                found.append(e)

    # b) String concatenation patterns
    for m in _JS_CONCAT_RE.finditer(raw_html):
        g = m.groups()
        if g[0] and g[1]:          # "local" + "@" + "domain"
            candidate = f"{g[0]}@{g[1]}"
        elif g[2] and g[3]:        # "local" + "@domain"
            at_domain = g[3] if g[3].startswith("@") else f"@{g[3]}"
            candidate = f"{g[2]}{at_domain}"
        else:
            continue
        e = clean_email(candidate)
        if e and e not in found:
            found.append(e)

    # c) Base64 / atob() patterns
    for m in _ATOB_RE.finditer(raw_html):
        try:
            padded  = m.group(1) + "=" * (-len(m.group(1)) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
            for match in _EMAIL_RE.findall(decoded):
                e = clean_email(match)
                if e and e not in found:
                    found.append(e)
        except Exception:
            continue

    # d) Unicode escape sequences (only scan <script> sections for speed)
    for script_match in re.finditer(r"<script[^>]*>(.*?)</script>", raw_html, re.DOTALL | re.I):
        js_text = script_match.group(1)
        if "\\u" in js_text:
            decoded_js = _decode_unicode_escapes(js_text)
            for match in _EMAIL_RE.findall(decoded_js):
                e = clean_email(match)
                if e and e not in found:
                    found.append(e)

    # e) Plain emails inside <script> tags (not yet caught by earlier strategies)
    for script_match in re.finditer(r"<script[^>]*>(.*?)</script>", raw_html, re.DOTALL | re.I):
        for match in _EMAIL_RE.findall(script_match.group(1)):
            e = clean_email(match)
            if e and e not in found:
                found.append(e)

    return found


# ── Per-page extraction ───────────────────────────────────────────────────────

def _extract_mailto_links(soup: BeautifulSoup) -> List[str]:
    """Strategy 1 — <a href="mailto:..."> tags (highest confidence)."""
    found: List[str] = []
    for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
        raw   = a["href"].replace("mailto:", "").split("?")[0]
        email = clean_email(raw)
        if email and email not in found:
            found.append(email)
    return found


def _extract_footer_emails(soup: BeautifulSoup) -> List[str]:
    """Strategy 2 — Footer element specifically (owner emails often live here)."""
    found: List[str] = []

    footer = (
        soup.find("footer")
        or soup.find(id=re.compile(r"footer", re.I))
        or soup.find(class_=re.compile(r"footer", re.I))
    )
    if not footer:
        return found

    # Check mailto links in footer first
    for a in footer.find_all("a", href=re.compile(r"^mailto:", re.I)):
        raw   = a["href"].replace("mailto:", "").split("?")[0]
        email = clean_email(raw)
        if email and email not in found:
            found.append(email)

    # Regex on footer visible text
    text = footer.get_text(separator=" ")
    for match in _EMAIL_RE.findall(text):
        e = clean_email(match)
        if e and e not in found:
            found.append(e)

    return found


def _extract_visible_emails(soup: BeautifulSoup) -> List[str]:
    """Strategy 3 — Visible text regex after stripping scripts/styles."""
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text  = soup.get_text(separator=" ")
    found: List[str] = []
    for match in _EMAIL_RE.findall(text):
        e = clean_email(match)
        if e and e not in found:
            found.append(e)
    return found


def _extract_phones(soup: BeautifulSoup) -> List[str]:
    """Extract all phone numbers from a page (tel: links first, then text regex)."""
    found: List[str] = []

    # tel: href links (most reliable)
    for a in soup.find_all("a", href=re.compile(r"^tel:", re.I)):
        raw   = a["href"].replace("tel:", "").strip()
        phone = clean_phone(raw)
        if phone and phone not in found:
            found.append(phone)

    # Regex over visible text
    text = soup.get_text(separator=" ")
    for m in _PHONE_RE.finditer(text):
        phone = clean_phone(m.group())
        if phone and phone not in found:
            found.append(phone)

    return found


# ── Owner name extraction ─────────────────────────────────────────────────────

def _looks_like_name(text: str) -> bool:
    """Heuristic: 2–4 capitalised words, 5–50 chars, not a generic heading."""
    _COMMON_HEADINGS = frozenset({
        "about us", "contact us", "our team", "meet the team",
        "get in touch", "our staff", "the team", "who we are",
        "our story", "about our company",
    })
    words = text.strip().split()
    if not (2 <= len(words) <= 4):
        return False
    if not (5 <= len(text) <= 50):
        return False
    if text.lower() in _COMMON_HEADINGS:
        return False
    return all(w[0].isupper() for w in words if w.isalpha())


def _extract_owner_name(soup: BeautifulSoup) -> Optional[str]:
    """
    Best-effort owner/founder name extraction.

    Tries (in order):
      1. Schema.org Person markup
      2. <meta name="author"> content
      3. itemprop="name" inside a team/person container
      4. Heading text near team / founder / CEO keywords
    """
    # 1. Schema.org Person
    for person_div in soup.find_all(
        attrs={"itemtype": re.compile(r"schema\.org/Person", re.I)}
    ):
        name_el = person_div.find(attrs={"itemprop": "name"})
        if name_el:
            name = name_el.get_text(strip=True)
            if _looks_like_name(name):
                return name

    # 2. Meta author tag
    meta_author = soup.find("meta", attrs={"name": re.compile(r"^author$", re.I)})
    if meta_author:
        content = (meta_author.get("content") or "").strip()
        if _looks_like_name(content):
            return content

    # 3. Team/person containers
    for sel in [
        ".team-member", ".staff-member", ".person",
        ".founder", ".ceo", ".owner",
        "[class*='team-member']", "[class*='person']",
    ]:
        for container in soup.select(sel)[:3]:
            for heading in container.find_all(["h2", "h3", "h4", "strong"]):
                text = heading.get_text(strip=True)
                if _looks_like_name(text):
                    return text

    # 4. Keyword-proximate headings (e.g. "Hi, I'm John Smith, founder of …")
    _name_context_re = re.compile(
        r"(?:i(?:'m| am)|founded by|owned by|meet|owner|founder|director|ceo)"
        r"\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
        re.I,
    )
    body_text = soup.get_text(separator=" ")
    for m in _name_context_re.finditer(body_text):
        candidate = m.group(1).strip()
        if _looks_like_name(candidate):
            return candidate

    return None


# ── Email quality ranking ─────────────────────────────────────────────────────

def _email_score(email: str, site_domain: str) -> int:
    """
    Score an email for quality.  Higher = better candidate for primary_email.
    Negative score = deprioritized (never use as primary).
    """
    local, _, domain = email.lower().partition("@")
    score = 0

    # Tier 0: hard disqualify spam prefixes
    if local in _DEPRIORITIZED_PREFIXES or any(
        local.startswith(p) for p in ("noreply", "no-reply", "donotreply", "bounce")
    ):
        return -1000

    # Site domain match bonus (email is from the same domain as the website)
    site_root = site_domain.split(":")[0]   # strip port if any
    if site_root and (site_root in domain or domain.split(".")[0] in site_root):
        score += 100

    # Preferred prefix bonus
    if local in _PREFERRED_PREFIXES:
        score += 80
    elif local in _ACCEPTABLE_PREFIXES:
        score += 40
    elif re.match(r"^[a-z][a-z0-9._\-]{2,19}$", local):
        # Looks like a real name / handle — personal email
        score += 60

    return score


def _pick_primary_email(emails: List[str], site_domain: str) -> Optional[str]:
    """
    Select the best primary email from the collected list.
    Never returns a deprioritized (spam) email unless it's the only one available.
    """
    if not emails:
        return None

    scored = [(e, _email_score(e, site_domain)) for e in emails]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Best non-negative score wins
    for email, score in scored:
        if score >= 0:
            return email

    # All emails are spam-prefix — return the least bad one as last resort
    return scored[0][0]


# ── WHOIS bonus ───────────────────────────────────────────────────────────────

def _whois_email(domain: str, log_fn: Callable[[str], None]) -> Optional[str]:
    """
    Attempt a WHOIS lookup for the registrant email.
    Non-fatal: returns None if python-whois is not installed or lookup fails.
    Filters out common privacy-proxy addresses.
    """
    try:
        import whois as _whois   # python-whois package  (pip install python-whois)
    except ImportError:
        return None

    try:
        log_fn(f"   🔍 WHOIS lookup for {domain} …")
        # whois.whois() has no built-in timeout and can hang on an
        # unresponsive WHOIS server — enforce a hard wall-clock timeout via a
        # worker thread. shutdown(wait=False): if it's still hanging past the
        # timeout, we move on rather than blocking this thread on its exit too.
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_whois.whois, domain)
        try:
            w = future.result(timeout=_WHOIS_TIMEOUT_SECONDS)
        except _FutureTimeoutError:
            log_fn(f"   ⏱️  WHOIS lookup for {domain} timed out after {_WHOIS_TIMEOUT_SECONDS}s")
            return None
        finally:
            pool.shutdown(wait=False)

        emails = w.emails if isinstance(w.emails, list) else ([w.emails] if w.emails else [])
        for raw_email in emails:
            e = clean_email(str(raw_email))
            if not e:
                continue
            # Filter privacy-proxy addresses
            e_lower = e.lower()
            if any(token in e_lower for token in _WHOIS_PRIVACY_TOKENS):
                continue
            log_fn(f"   ✅ WHOIS email: {e}")
            return e

    except Exception:
        pass

    return None


# ── Core page scanner ─────────────────────────────────────────────────────────

def _scan_page(
    url:      str,
    session:  requests.Session,
    ua:       str,
    log_fn:   Callable[[str], None],
) -> Tuple[List[str], List[str], Optional[str]]:
    """
    Fetch one page and apply all 4 extraction strategies.

    Returns (emails, phones, owner_name).
    """
    _, raw_html = _safe_get(url, session, ua)
    if not raw_html:
        return [], [], None

    soup = BeautifulSoup(raw_html, "lxml")

    # ── Emails — all 4 strategies on this single page ────────────────────────
    emails: List[str] = []
    seen_emails: Set[str] = set()

    def _add(e_list: List[str]) -> None:
        for e in e_list:
            if e and e not in seen_emails:
                seen_emails.add(e)
                emails.append(e)

    # Strategy 1: mailto links (before any soup decomposition)
    _add(_extract_mailto_links(soup))

    # Strategy 2: footer scan (before soup decomposition)
    _add(_extract_footer_emails(soup))

    # Strategy 3: visible text (decomposes script/style from soup — call last on soup)
    _add(_extract_visible_emails(soup))

    # Strategy 4: JS source scan (operates on raw HTML string — unaffected by soup state)
    _add(_extract_js_emails(raw_html))

    # ── Phones ────────────────────────────────────────────────────────────────
    # Re-parse lightweight for phones (soup was mutated by _extract_visible_emails)
    soup2 = BeautifulSoup(raw_html, "lxml")
    phones = _extract_phones(soup2)

    # ── Owner name ────────────────────────────────────────────────────────────
    soup3  = BeautifulSoup(raw_html, "lxml")
    owner  = _extract_owner_name(soup3)

    return emails, phones, owner


# ── Main sync orchestrator ────────────────────────────────────────────────────

def _find_emails_sync(
    website_url: str,
    log_fn:      Callable[[str], None],
) -> Dict[str, Any]:
    """
    Synchronous orchestrator — runs inside asyncio.to_thread().

    Applies all strategies and assembles the final result dict.
    """
    result: Dict[str, Any] = {
        "primary_email":    None,
        "all_emails":       [],
        "phone_numbers":    [],
        "owner_name":       None,
        "contact_page_url": None,
    }

    url        = _normalize_url(website_url)
    domain     = _get_domain(url)
    base       = _base_url(url)
    session    = requests.Session()
    ua         = _get_ua()

    all_emails:  List[str]       = []
    all_phones:  List[str]       = []
    owner_name:  Optional[str]   = None
    source_url:  Optional[str]   = None

    seen_emails: Set[str] = set()
    seen_phones: Set[str] = set()

    def _merge_emails(new_emails: List[str], page_url: str) -> None:
        """Deduplicate + track the first page that yielded a quality email."""
        nonlocal source_url
        for e in new_emails:
            if e not in seen_emails:
                seen_emails.add(e)
                all_emails.append(e)
                if source_url is None and _email_score(e, domain) >= 0:
                    source_url = page_url

    def _merge_phones(new_phones: List[str]) -> None:
        for p in new_phones:
            if p not in seen_phones:
                seen_phones.add(p)
                all_phones.append(p)

    def _has_quality_email() -> bool:
        """True if we already have at least one preferred/acceptable email."""
        return any(_email_score(e, domain) >= 60 for e in all_emails)

    # ── Homepage scan ─────────────────────────────────────────────────────────
    log_fn(f"📄 Homepage: {url}")
    hp_emails, hp_phones, hp_owner = _scan_page(url, session, ua, log_fn)
    _merge_emails(hp_emails, url)
    _merge_phones(hp_phones)
    if hp_owner:
        owner_name = hp_owner
    log_fn(
        f"   {'✉️  ' + str(len(hp_emails)) + ' email(s)' if hp_emails else '— no emails on homepage'}"
        + (f" | 📞 {len(hp_phones)}" if hp_phones else "")
    )

    # ── Sub-page scan ─────────────────────────────────────────────────────────
    for path in _CONTACT_PATHS:
        if _has_quality_email() and owner_name:
            break   # have a quality email + owner name — no need to keep fetching

        sub_url = base + path
        log_fn(f"   📑 Checking {path} …")

        _, raw_html = _safe_get(sub_url, session, ua)
        if not raw_html:
            # 2–4 s delay even on failure (politeness + avoids triggering rate limits)
            time.sleep(random.uniform(2.0, 4.0))
            continue

        soup = BeautifulSoup(raw_html, "lxml")

        # All 4 extraction strategies on this sub-page
        sub_emails: List[str] = []
        seen_sub: Set[str] = set()

        def _add_sub(e_list: List[str]) -> None:
            for e in e_list:
                if e and e not in seen_sub:
                    seen_sub.add(e)
                    sub_emails.append(e)

        _add_sub(_extract_mailto_links(soup))
        _add_sub(_extract_footer_emails(soup))
        _add_sub(_extract_visible_emails(soup))
        _add_sub(_extract_js_emails(raw_html))

        soup2 = BeautifulSoup(raw_html, "lxml")
        sub_phones = _extract_phones(soup2)

        if not owner_name:
            soup3      = BeautifulSoup(raw_html, "lxml")
            sub_owner  = _extract_owner_name(soup3)
            if sub_owner:
                owner_name = sub_owner

        _merge_emails(sub_emails, sub_url)
        _merge_phones(sub_phones)

        if sub_emails:
            log_fn(f"     ✉️  {len(sub_emails)} email(s) found")

        time.sleep(random.uniform(2.0, 4.0))   # polite between sub-page requests

    # ── WHOIS bonus ───────────────────────────────────────────────────────────
    whois_email = _whois_email(domain, log_fn)
    if whois_email and whois_email not in seen_emails:
        all_emails.append(whois_email)
        if source_url is None:
            source_url = f"whois:{domain}"

    # ── Compile result ────────────────────────────────────────────────────────
    primary = _pick_primary_email(all_emails, domain)

    result["primary_email"]    = primary
    result["all_emails"]       = all_emails
    result["phone_numbers"]    = all_phones
    result["owner_name"]       = owner_name
    result["contact_page_url"] = source_url

    total = len(all_emails)
    quality = sum(1 for e in all_emails if _email_score(e, domain) >= 0)
    log_fn(
        f"🏁 Done — {total} email(s) found"
        + (f" ({quality} quality)" if total else "")
        + (f" | primary: {primary}" if primary else " | no primary email")
        + (f" | owner: {owner_name}" if owner_name else "")
    )

    return result


# ── Public async API ──────────────────────────────────────────────────────────

async def find_emails_from_website(
    website_url:  str,
    log_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Deep email finder — async entry-point.

    Parameters
    ----------
    website_url  : any format accepted ("example.com", "https://example.com")
    log_callback : optional sync callable(msg) for SSE streaming

    Returns
    -------
    {
      "primary_email":    str | None,   # best non-spam email found
      "all_emails":       list[str],    # every valid email, deduplicated
      "phone_numbers":    list[str],    # every valid phone, deduplicated
      "owner_name":       str | None,   # person name if extractable
      "contact_page_url": str | None,   # URL where primary_email was found
    }
    """
    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    if not website_url:
        return {
            "primary_email": None, "all_emails": [],
            "phone_numbers": [], "owner_name": None, "contact_page_url": None,
        }

    _log(f"🔎 Deep email scan: {website_url}")
    return await asyncio.to_thread(_find_emails_sync, website_url, _log)


async def find_email_from_website(website_url: str) -> Optional[str]:
    """
    Backward-compatible shim — returns just the primary email string.
    Drop-in replacement for scraper.py's find_email_from_website().
    """
    result = await find_emails_from_website(website_url)
    return result.get("primary_email")
