"""
website_analyzer.py — Fetch and analyze a business website.

Extracts clean readable text, metadata, and structural signals
(contact form, social links) for use by the AI enricher and lead scorer.

All network calls use httpx with redirect following and SSL verification
disabled to handle the many small-business sites with invalid certs.
"""
import logging
import re
from typing import Any, Dict

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_MAX_TEXT_CHARS  = 3000
_SOCIAL_DOMAINS  = re.compile(
    r"(facebook|twitter|instagram|linkedin|youtube|tiktok|pinterest)\.com", re.I
)


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _extract_text(soup: BeautifulSoup) -> str:
    """Remove boilerplate then extract clean readable text."""
    for tag in soup(["script", "style", "nav", "header", "footer",
                     "noscript", "iframe", "svg", "form"]):
        tag.decompose()

    body = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"content|main|hero|about", re.I))
        or soup.find("body")
        or soup
    )

    lines = [t.strip() for t in body.stripped_strings if len(t.strip()) > 20]
    return " ".join(lines)[:_MAX_TEXT_CHARS]


async def analyze_website(url: str, timeout: int = 12) -> Dict[str, Any]:
    """
    Fetch a business website and return extracted intelligence.

    Returns
    -------
    dict with keys:
      url, page_text, title, description,
      has_contact_form, has_social_links, error (None on success)
    """
    result: Dict[str, Any] = {
        "url":              url,
        "page_text":        "",
        "title":            "",
        "description":      "",
        "has_contact_form": False,
        "has_social_links": False,
        "error":            None,
    }

    norm = _normalize_url(url)

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=False,
            headers=_HEADERS,
        ) as client:
            resp = await client.get(norm)
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPStatusError as exc:
        result["error"] = f"HTTP {exc.response.status_code}"
        return result
    except httpx.TimeoutException:
        result["error"] = "timeout"
        return result
    except Exception as exc:
        result["error"] = str(exc)[:80]
        return result

    soup = BeautifulSoup(html, "lxml")

    # Title
    title_tag = soup.find("title")
    result["title"] = title_tag.get_text(strip=True)[:120] if title_tag else ""

    # Meta description
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    result["description"] = (meta.get("content", "")[:200] if meta else "")

    # Contact form signal
    forms = soup.find_all("form")
    for form in forms:
        if form.find(["input", "textarea"], attrs={"type": re.compile(r"text|email", re.I)}):
            result["has_contact_form"] = True
            break

    # Social links
    result["has_social_links"] = bool(soup.find("a", href=_SOCIAL_DOMAINS))

    result["page_text"] = _extract_text(soup)
    return result
