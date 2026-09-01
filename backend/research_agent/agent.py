"""
agent.py — the Browser Research Agent orchestrator.

Two levels:
  - research_business(): the per-lead OBSERVE/DECIDE/ACT/VALIDATE loop
    (brief §1/§8) — LLM decides one action, Python executes it, result
    feeds back into the next decision, bounded by hard budgets.
  - run_research_session(): the session level — geographic expansion,
    discovery (if no seed businesses given), per-lead failure isolation
    (one lead's exception never kills the batch), session budgets.

The LLM is never trusted alone: every decide_next_action() call has a
deterministic rule-based fallback (_fallback_action) that guarantees the
loop keeps making real progress even if Ollama is unavailable or returns
garbage — matching this codebase's "AI enhances, heuristics are the floor"
pattern used throughout backend/intelligence/.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from . import evidence as evidence_mod
from . import extraction
from . import llm as llm_mod
from . import validation
from .actions import ActionResult, ActionValidationError, validate_action
from .browser import BrowserController
from .config import get_research_config
from .models import (
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_SECURE_WEB_FORM,
    STATUS_UNCONFIRMED,
    AgentAction,
    ResearchEvidence,
    ResearchLead,
    management_titles_for_niche,
)
from .planner import GeoTask, expand_geography
from ..validators import clean_email, clean_phone

logger = logging.getLogger(__name__)

_DIRECTORY_DOMAINS = frozenset({
    "yelp.com", "yellowpages.com", "facebook.com", "wikipedia.org",
    "tripadvisor.com", "google.com", "maps.google.com", "linkedin.com",
    "instagram.com", "youtube.com", "bbb.org", "indeed.com",
    # aggregators / lead-gen directories that outrank real businesses on
    # broad "<niche> in <city>" searches — a page here is a listing, not a
    # business to research.
    "healthgrades.com", "zocdoc.com", "webmd.com", "vitals.com", "ratemds.com",
    "opencare.com", "1800dentist.com", "yellow.place", "angi.com", "thumbtack.com",
    "expertise.com", "threebestrated.com", "nextdoor.com", "mapquest.com",
    "wellness.com", "sharecare.com", "findlocal.com", "superpages.com",
    "chamberofcommerce.com", "manta.com", "birdeye.com", "cylex.us.com",
})

# Result titles that describe a list/roundup/directory, not one business.
_AGGREGATOR_TITLE_RE = re.compile(
    r"(\bbest\b|\btop\s*\d*\b|\d+\s+best\b|\bnear\s+me\b|\bdirectory\b|\blistings?\b|"
    r"\breviews?\s+of\b|\bfind\s+(a\b|\w+\s+(dentists?|clinics?|doctors?|practices?|providers?))|"
    r"\b(dentists|clinics|doctors|practices|offices|providers)\s+in\b|"
    r"\bcompare\b|\bguide\s+to\b|\bplaces?\s+to\b)",
    re.I,
)


_ROLE_WORDS_ONLY = frozenset({
    "dr", "dr.", "doctor", "dentist", "dds", "dmd", "md", "the", "team", "staff",
    "our", "owner", "founder", "president", "manager", "director", "office",
    "practice", "principal", "lead", "partner", "attorney", "mr", "mrs", "ms",
})


def _is_role_word_only(value: str) -> bool:
    """True when an 'extracted name' is really just a title/role phrase —
    "Dentist Dr.", "The Team", "Owner" — not a person's name."""
    toks = [t.strip(".,").lower() for t in re.split(r"\s+", (value or "").strip()) if t.strip(".,")]
    return not toks or all(t in _ROLE_WORDS_ONLY for t in toks)


def _looks_like_aggregator(title: str, url: str) -> bool:
    dom = _domain(url)
    if dom and dom in _DIRECTORY_DOMAINS:
        return True
    return bool(_AGGREGATOR_TITLE_RE.search(title or ""))


def _domain(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def candidate_key(hint: Dict[str, Any]) -> str:
    """Stable identity for a discovery candidate — used to skip work already
    done on a resumed session. Website domain if present (most reliable),
    else lowercased business name + city."""
    website = (hint.get("website") or "").strip().lower()
    if website:
        dom = _domain(website)
        if dom:
            return f"site:{dom}"
    name = (hint.get("business_name") or "").strip().lower()
    city = (hint.get("city") or "").strip().lower()
    return f"name:{name}|{city}"


class ResearchAgent:
    """One instance per research session. Owns the BrowserController."""

    def __init__(self, browser: BrowserController, cfg: Dict[str, Any], niche: str) -> None:
        self.browser = browser
        self.cfg = cfg
        self.niche = niche

    # ── Discovery (used only when no seed businesses are supplied) ────────

    async def discover_candidates(self, geo: GeoTask, target_count: int) -> List[Dict[str, Any]]:
        location = geo.city or geo.raw_location
        query = f"{self.niche} in {location}"
        logger.info("[AGENT] Discovery search: %s", query)
        result = await self.browser.execute(AgentAction(action="google_search", params={"query": query}))
        logger.info("[BROWSER] google_search -> %s (%d raw results)", result.status, len(result.data.get("results", [])) if result.status == "success" else 0)

        candidates: List[Dict[str, Any]] = []
        seen_domains: set = set()
        if result.status == "success":
            for r in result.data.get("results", []):
                title = r.get("title") or ""
                url = r.get("url", "")
                domain = _domain(url)
                if not domain or domain in seen_domains:
                    continue
                if _looks_like_aggregator(title, url):
                    logger.debug("[AGENT] Discovery: skipping aggregator/listicle result %r", title)
                    continue
                seen_domains.add(domain)
                candidates.append({
                    "business_name": title.split(" - ")[0].split(" | ")[0].strip()[:200],
                    "website": url,
                    "city": geo.city, "state": geo.state, "country": geo.country,
                })
                if len(candidates) >= target_count:
                    break
        elif result.status == "blocked":
            logger.warning("[AGENT] Discovery search blocked (CAPTCHA/obstacle) for query: %s", query)
        elif result.error:
            logger.warning("[AGENT] Discovery search failed for '%s': %s", query, result.error)

        logger.info("[AGENT] Discovery found %d candidate business(es) for '%s'", len(candidates), location)
        return candidates

    # ── Per-lead research loop ─────────────────────────────────────────────

    async def research_business(
        self, hint: Dict[str, Any], on_action: Optional[Any] = None, is_cancelled: Optional[Any] = None,
    ) -> ResearchLead:
        lead = ResearchLead(
            city=hint.get("city"), state=hint.get("state"), country=hint.get("country"),
            business_name=hint.get("business_name"), business_phone=hint.get("phone"),
            business_website=hint.get("website"),
        )
        for field_name in ("business_name", "business_phone", "business_website"):
            if getattr(lead, field_name):
                evidence_mod.record_finding(lead, field_name, getattr(lead, field_name), "discovery_seed", confidence=0.6)

        max_actions = self.cfg["research_agent_max_actions_per_lead"]
        max_searches = self.cfg["research_agent_max_searches_per_lead"]
        max_pages = self.cfg["research_agent_max_pages_per_lead"]
        max_time = self.cfg["research_agent_max_time_per_lead_seconds"]
        max_failures = self.cfg["research_agent_max_consecutive_failures"]

        started = time.monotonic()
        management_search_attempted = False
        # Researcher-discipline state (brief §2/§16): what has been opened,
        # what has actually been READ, and which Contact/About/Team links are
        # known but not yet followed. Deterministic rules below use these so
        # the loop reads pages instead of only opening them.
        opened_urls: set[str] = set()
        pages_read: set[str] = set()
        relevant_links: List[str] = []
        last_opened_url: Optional[str] = lead.business_website or None
        just_opened = False

        while True:
            if is_cancelled and await is_cancelled():
                logger.info("[RESEARCH] Lead '%s': session cancelled", lead.business_name)
                break
            if lead.actions_taken >= max_actions:
                logger.info("[RESEARCH] Lead '%s': max_actions_per_lead reached", lead.business_name)
                break
            if time.monotonic() - started > max_time:
                logger.info("[RESEARCH] Lead '%s': max_time_per_lead reached", lead.business_name)
                break
            if lead.consecutive_failures >= max_failures:
                logger.info("[RESEARCH] Lead '%s': max_consecutive_failures reached", lead.business_name)
                break
            if validation.is_research_sufficient(lead) and lead.actions_taken > 0:
                current_page_read = bool(last_opened_url and last_opened_url in pages_read)
                paths_exhausted = lead.pages_visited >= 2 and not relevant_links and current_page_read
                if lead.management_contact_name or management_search_attempted or paths_exhausted:
                    break

            # Researcher-discipline: when the next step is obvious (open the
            # known website, read the page just opened, follow a Contact/Team
            # link), do it WITHOUT an LLM round-trip — otherwise a slow or
            # timing-out Ollama costs the full timeout per deterministic step
            # (brief §2/§14). The LLM is only consulted when there is a
            # genuine choice to make.
            action = self._forced_action(
                lead, just_opened=just_opened, last_opened_url=last_opened_url,
                pages_read=pages_read, relevant_links=relevant_links, max_pages=max_pages,
            )
            if action is None:
                current_page_read = bool(last_opened_url and last_opened_url in pages_read)
                state_summary = self._build_state_summary(lead, current_page_read=current_page_read)
                action = await llm_mod.decide_next_action(state_summary)
                if action.action == "_llm_failed":
                    logger.info("[LLM] decide_next_action failed (%s) — using rule-based fallback", action.reason)
                    action = self._fallback_action(lead, max_searches, max_pages)

            try:
                validate_action(action)
            except ActionValidationError as exc:
                logger.info("[AGENT] Invalid action (%s) — using fallback", exc)
                action = self._fallback_action(lead, max_searches, max_pages)
                try:
                    validate_action(action)
                except ActionValidationError:
                    action = AgentAction(action="finish_research", reason="no valid action available")

            if action.action == "finish_research":
                break
            if action.action == "google_search" and lead.searches_taken >= max_searches:
                action = self._fallback_action(lead, max_searches, max_pages, force_no_search=True)
                if action.action == "finish_research":
                    break
            if action.action in ("open_url", "open_new_tab") and lead.pages_visited >= max_pages:
                # Don't hard-stop and lose whatever the currently-open page
                # might still hold — extract it first; the loop's own budget
                # checks (max_actions/max_time) still bound how long this can
                # go on, and repeatedly re-extracting the same page wastes at
                # most a few of those actions, never an unbounded amount.
                action = AgentAction(action="extract_page_text", reason="max_pages_per_lead reached — extracting current page before finishing")

            # save_evidence is bookkeeping, not a browser action — it must
            # never reach BrowserController (which has no handler for it and
            # would report a spurious failure, silently discarding every
            # finding the LLM asks to record).
            if action.action == "save_evidence":
                logger.info("[LLM] Action: save_evidence — %s", action.reason or "")
                self._apply_save_evidence(lead, action)
                lead.actions_taken += 1
                lead.consecutive_failures = 0
                if on_action:
                    try:
                        await on_action(lead, action, ActionResult(action="save_evidence", status="success"))
                    except Exception:
                        logger.debug("on_action callback failed (non-fatal)", exc_info=True)
                continue

            logger.info("[LLM] Action: %s — %s", action.action, action.reason or "")
            result = await self.browser.execute(action)
            logger.info("[BROWSER] %s -> %s", action.action, result.status)
            if on_action:
                try:
                    await on_action(lead, action, result)
                except Exception:
                    logger.debug("on_action callback failed (non-fatal)", exc_info=True)

            lead.actions_taken += 1
            if action.action == "google_search":
                lead.searches_taken += 1
                if action.params.get("query", "").find(lead.business_name or "\0") != -1:
                    management_search_attempted = management_search_attempted or any(
                        t.lower() in action.params.get("query", "").lower()
                        for t in management_titles_for_niche(self.niche)
                    )
            if action.action in ("open_url", "open_new_tab"):
                lead.pages_visited += 1
                if result.status == "success":
                    last_opened_url = result.data.get("url") or action.params.get("url") or last_opened_url
                    opened_urls.add(last_opened_url)
                    just_opened = True
            elif action.action == "extract_page_text":
                if result.status == "success":
                    read_url = result.data.get("url") or last_opened_url
                    if read_url:
                        pages_read.add(read_url)
                just_opened = False
            else:
                just_opened = False

            lead.consecutive_failures = 0 if result.status == "success" else lead.consecutive_failures + 1

            await self._process_result(lead, action, result, opened_urls=opened_urls, relevant_links=relevant_links)

        self._record_not_found_after_search(lead)
        validation.finalize_status(lead)
        return lead

    def _record_not_found_after_search(self, lead: ResearchLead) -> None:
        """After the loop, every optional contact field that was actively
        researched (a page was visited or a search was run) but not found
        gets an explicit NOT_FOUND_AFTER_SEARCH evidence row (brief §1/§14) —
        so a null is auditable as "looked, not public", never a silent gap
        and never fabricated."""
        if lead.pages_visited == 0 and lead.searches_taken == 0:
            return
        reason = f"researched {lead.pages_visited} page(s), {lead.searches_taken} search(es)"
        for f in ("business_email", "management_contact_name", "management_title",
                  "management_phone", "management_email"):
            if getattr(lead, f, None):
                continue
            if any(e.field_name == f and e.status in (STATUS_FOUND, STATUS_SECURE_WEB_FORM)
                   for e in lead.evidence):
                continue
            status_field = f"{f}_status"
            if getattr(lead, status_field, "") == STATUS_SECURE_WEB_FORM:
                continue
            evidence_mod.record_not_found(lead, f, reason=reason, after_search=True)

    def _forced_action(
        self, lead: ResearchLead, *, just_opened: bool, last_opened_url: Optional[str],
        pages_read: set, relevant_links: List[str], max_pages: int,
    ) -> Optional[AgentAction]:
        """The next step when it is deterministic — returns None when the LLM
        genuinely needs to choose. Keeps the loop researching (reading pages,
        following Contact/Team links) instead of spinning on find_links, and
        does it without an LLM call."""
        # R1 — the business website is known but has never been opened.
        if lead.business_website and lead.pages_visited == 0:
            return AgentAction(action="open_url", params={"url": lead.business_website},
                               reason="deterministic: open the business website")

        # R2 — a page was just opened and not yet read: read it.
        if just_opened and last_opened_url and last_opened_url not in pages_read:
            return AgentAction(action="extract_page_text",
                               reason="deterministic: read the page just opened")

        # R3 — current page read, management / business email still missing,
        # and a Contact/About/Team page is known: follow it.
        needs_more = (not lead.management_contact_name) or (
            not lead.business_email and lead.business_email_status != STATUS_SECURE_WEB_FORM
        )
        if (needs_more and relevant_links and lead.pages_visited < max_pages
                and last_opened_url in pages_read):
            nxt = relevant_links.pop(0)
            return AgentAction(action="open_url", params={"url": nxt},
                               reason="deterministic: follow a Contact/About/Team link")

        return None

    def _fallback_action(
        self, lead: ResearchLead, max_searches: int, max_pages: int, force_no_search: bool = False,
    ) -> AgentAction:
        """Deterministic backup planner — guarantees real progress without the LLM."""
        if not lead.business_website and not force_no_search and lead.searches_taken < max_searches:
            query = f'"{lead.business_name}" {lead.city or ""} website'.strip()
            return AgentAction(action="google_search", params={"query": query}, reason="fallback: find website")
        if lead.business_website and lead.pages_visited == 0:
            return AgentAction(action="open_url", params={"url": lead.business_website}, reason="fallback: open business website")
        if lead.pages_visited > 0 and not lead.management_contact_name and not force_no_search and lead.searches_taken < max_searches:
            titles = management_titles_for_niche(self.niche)
            query = f'"{lead.business_name}" {titles[0]}'
            return AgentAction(action="google_search", params={"query": query}, reason="fallback: find management contact")
        return AgentAction(action="finish_research", reason="fallback: no further productive action")

    async def _process_result(self, lead: ResearchLead, action: AgentAction, result,
                              opened_urls: Optional[set] = None,
                              relevant_links: Optional[List[str]] = None) -> None:
        if result.status != "success":
            return
        data = result.data

        if action.action == "google_search":
            self._process_search_snippets(lead, data)

        elif action.action in ("open_url", "open_new_tab"):
            pass  # extraction happens on extract_page_text (deterministically forced by the loop)

        elif action.action == "find_links":
            self._collect_relevant_links(data.get("links", []), opened_urls, relevant_links)

        elif action.action == "extract_page_text":
            self._collect_relevant_links(data.get("links", []), opened_urls, relevant_links)
            await self._process_page_text(lead, data)

    @staticmethod
    def _collect_relevant_links(links: List[Dict[str, str]], opened_urls: Optional[set],
                                relevant_links: Optional[List[str]]) -> None:
        """Queue Contact/About/Team page URLs the loop hasn't opened yet, so
        R3 in _deterministic_override can follow one when a field is missing."""
        if relevant_links is None:
            return
        opened = opened_urls or set()
        for link in links or []:
            href = (link.get("href") or "").strip()
            text = link.get("text") or ""
            if not href.startswith("http"):
                continue
            if href in opened or href in relevant_links:
                continue
            if extraction.is_relevant_nav_link(text) or extraction.is_relevant_nav_link(href):
                relevant_links.append(href)
        # keep it bounded and prioritised (contact/team first)
        relevant_links.sort(key=lambda u: 0 if re.search(r"contact|team|about|staff|our-team", u, re.I) else 1)
        del relevant_links[6:]

    def _apply_save_evidence(self, lead: ResearchLead, action: AgentAction) -> None:
        """save_evidence is bookkeeping, never routed through the browser
        (dispatch guard in research_business()).

        An LLM assertion is NOT a verified finding — a local 8B will happily
        "record" a plausible-looking email or owner name it never actually
        saw. So save_evidence:
          - is confined to the recordable contact/identity fields;
          - runs email/phone through the same validators as the deterministic
            path (a malformed value is dropped);
          - is always recorded as UNCONFIRMED and NEVER sets the field value.
        The value only becomes a fact if deterministic extraction or the
        role-sentence extractor also finds it on a real page.
        """
        field_name = action.params.get("field_name")
        value = action.params.get("value")
        if not field_name or value in (None, "") or field_name not in evidence_mod._RECORDABLE_FIELDS:
            return
        value = str(value).strip()
        if field_name in ("business_email", "management_email"):
            value = clean_email(value)
        elif field_name in ("business_phone", "management_phone"):
            value = clean_phone(value)
        if not value:
            return
        current_url = self.browser._page.url if getattr(self.browser, "_page", None) else None
        lead.evidence.append(ResearchEvidence(
            field_name=field_name, source_type="llm_decision", source_url=current_url,
            snippet=(action.params.get("snippet") or f"LLM asserted {field_name}={value!r} (unverified)")[:200],
            confidence=0.3, status=STATUS_UNCONFIRMED,
        ))

    def _process_search_snippets(self, lead: ResearchLead, data: Dict[str, Any]) -> None:
        # Deliberately NOT mining email/phone from the SERP blob: a results
        # page mixes titles/snippets from many sources (aggregator tracking
        # numbers, other similarly-named businesses) — the first regex hit is
        # not reliably this business's number. Contact facts come from the
        # business's own pages only.
        results = data.get("results", [])

        if not lead.business_website:
            # Adopt a result as the business website only when its domain
            # plausibly belongs to this business (shares a name token) and it
            # is not an aggregator/directory — otherwise a stray article or a
            # profile page from a management-title search satisfies a REQUIRED
            # field with the wrong URL and the lead is wrongly COMPLETE.
            name_tokens = {
                t for t in re.split(r"[^a-z0-9]+", (lead.business_name or "").lower())
                if len(t) >= 4 and t not in ("dental", "clinic", "dentist", "family", "care", "group", "smile")
            }
            for r in results:
                url = r.get("url", "")
                domain = _domain(url)
                if not domain or domain in _DIRECTORY_DOMAINS or _looks_like_aggregator(r.get("title", ""), url):
                    continue
                dom_flat = re.sub(r"[^a-z0-9]", "", domain)
                if name_tokens and not any(tok in dom_flat for tok in name_tokens):
                    continue
                evidence_mod.record_finding(
                    lead, "business_website", url, source_type="google_search",
                    source_url=url, snippet=r.get("title"), confidence=0.5,
                )
                break

    async def _process_page_text(self, lead: ResearchLead, data: Dict[str, Any]) -> None:
        text = data.get("text", "")
        url = data.get("url")
        links = data.get("links", []) or []

        # Explicit mailto:/tel: hrefs are the strongest deterministic signal —
        # a site author put the address there on purpose.
        if not lead.business_email:
            for email in extraction.emails_from_contact_links(links):
                evidence_mod.record_finding(
                    lead, "business_email", email, source_type="website_mailto",
                    source_url=url, snippet=f"mailto: {email}", confidence=0.9,
                )
                break
        if not lead.business_phone:
            for phone in extraction.phones_from_contact_links(links):
                evidence_mod.record_finding(
                    lead, "business_phone", phone, source_type="website_tel",
                    source_url=url, snippet=f"tel: {phone}", confidence=0.85,
                )
                break

        self._apply_deterministic_extraction(lead, text, source_type="website", source_url=url)

        if extraction.has_secure_contact_form(text, links):
            if not lead.business_email:
                lead.business_email_status = STATUS_SECURE_WEB_FORM
            if not lead.management_email:
                lead.management_email_status = STATUS_SECURE_WEB_FORM

        missing = [f for f in ("management_contact_name", "management_title") if not getattr(lead, f)]
        if missing:
            role_sentences = extraction.find_role_sentences(text)
            if role_sentences:
                extracted = await llm_mod.extract_fields(
                    "\n".join(role_sentences), missing, business_name=lead.business_name,
                )
                # ONLY name/title from the LLM — genuinely language-dependent.
                # Email/phone are deterministic-regex-only (extraction.py): a
                # regex that already ran over this page is more trustworthy
                # than an 8B asked to "find" a contact, which invites a guess.
                name = extracted.get("management_contact_name")
                if name and _is_role_word_only(name):
                    # "Dentist Dr.", "The Team", "Owner" — a title/role, not a
                    # person. Recording it as a name is worse than leaving it
                    # NOT_FOUND (the title is still captured separately).
                    extracted.pop("management_contact_name", None)
                for field_name in ("management_contact_name", "management_title"):
                    if field_name in extracted and not getattr(lead, field_name):
                        evidence_mod.record_finding(
                            lead, field_name, extracted[field_name], source_type="ai_extraction",
                            source_url=url, snippet=role_sentences[0][:200], confidence=0.65,
                        )
                if lead.management_contact_name and not lead.management_phone and lead.business_phone:
                    lead.management_phone = lead.business_phone
                    lead.management_phone_type = "BUSINESS"
                    evidence_mod.record_finding(
                        lead, "management_phone", lead.business_phone, source_type="inference",
                        source_url=url, snippet="No direct line found; using the business's main phone.",
                        confidence=0.4, status=STATUS_FOUND,
                    )

    def _apply_deterministic_extraction(
        self, lead: ResearchLead, text: str, source_type: str, source_url: Optional[str],
    ) -> None:
        if not text:
            return
        if not lead.business_email:
            emails = extraction.extract_emails(text)
            if emails:
                evidence_mod.record_finding(
                    lead, "business_email", emails[0], source_type=source_type,
                    source_url=source_url, snippet=emails[0], confidence=0.8,
                )
        if not lead.business_phone:
            phones = extraction.extract_phones(text)
            if phones:
                evidence_mod.record_finding(
                    lead, "business_phone", phones[0], source_type=source_type,
                    source_url=source_url, snippet=phones[0], confidence=0.75,
                )

    def _build_state_summary(self, lead: ResearchLead, current_page_read: bool = False) -> str:
        missing = lead.missing_required_fields()
        optional_missing = [f for f in ("management_contact_name", "management_title", "business_email") if not getattr(lead, f)]
        return (
            f"Niche: {self.niche}\n"
            f"Business name: {lead.business_name or 'unknown'}\n"
            f"Known: website={lead.business_website or 'none'}, phone={lead.business_phone or 'none'}, "
            f"email={lead.business_email or 'none'}, management_contact={lead.management_contact_name or 'none'}\n"
            f"Missing required fields: {', '.join(missing) or 'none'}\n"
            f"Missing optional fields: {', '.join(optional_missing) or 'none'}\n"
            f"Current page read yet: {'yes' if current_page_read else 'no'}\n"
            f"Actions taken so far: {lead.actions_taken}, searches: {lead.searches_taken}, pages visited: {lead.pages_visited}\n"
        )


# ── Session-level orchestration ─────────────────────────────────────────────

async def run_research_session(
    niche: str,
    location: str,
    target_count: int,
    seed_businesses: Optional[List[Dict[str, Any]]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    on_lead_complete: Optional[Any] = None,
    on_action: Optional[Any] = None,
    on_progress: Optional[Any] = None,
    is_cancelled: Optional[Any] = None,
    already_processed: Optional[set] = None,
    discovery_fallback: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Public entry point. Runs discovery (unless seed_businesses given) then
    deep-researches each candidate, one BrowserController for the whole
    session. A single lead's exception is caught and marked FAILED — never
    aborts the batch (brief §22).

    `already_processed` is a set of candidate_key() values completed on a
    previous run — used to skip re-researching them on a resumed session
    (results already sit in lead_research_results). Discovery still re-runs;
    only per-business research is skipped.

    `discovery_fallback(niche, location, country, limit) -> list[hint]` is an
    optional async callable used only when the browser's own google_search
    discovery is blocked (Google consent/CAPTCHA wall) or returns nothing —
    it lets the caller supply seed candidates from the existing scraper
    pipeline so a blocked SERP never means a zero-result session. Wired by
    session.py; agent.py never imports the scrapers or the DB directly.

    `on_progress(updates: dict)` is called at phase/city boundaries and per
    business so the session row reflects live progress.

    Returns {"leads": ..., "failed_count": int, "skipped_count": int,
             "geo_tasks": ..., "processed_keys": List[str]}.
    """
    resolved_cfg = cfg or await get_research_config()
    max_total = resolved_cfg["research_agent_max_total_leads"]
    target_count = min(target_count, max_total)
    already_processed = set(already_processed or ())
    processed_keys: List[str] = list(already_processed)

    async def _progress(updates: Dict[str, Any]) -> None:
        if on_progress:
            try:
                await on_progress(updates)
            except Exception:
                logger.debug("on_progress callback failed (non-fatal)", exc_info=True)

    geo_tasks = await expand_geography(location, target_count, resolved_cfg["research_agent_max_geographic_units"])
    if not geo_tasks:
        return {"leads": [], "failed_count": 0, "skipped_count": 0, "geo_tasks": [], "processed_keys": processed_keys}

    leads: List[ResearchLead] = []
    failed_count = 0
    skipped_count = 0
    researched_count = 0

    async with BrowserController(
        headless=resolved_cfg["research_agent_headless"],
        page_timeout_ms=resolved_cfg["research_agent_page_timeout_ms"],
    ) as browser:
        agent = ResearchAgent(browser, resolved_cfg, niche)

        for geo in geo_tasks:
            if len(leads) >= target_count:
                break
            if is_cancelled and await is_cancelled():
                logger.info("[AGENT] Session cancelled — stopping before next geographic unit")
                break
            remaining = target_count - len(leads)
            geo_target = min(geo.target_count, remaining)
            geo_label = geo.city or geo.raw_location

            if seed_businesses:
                await _progress({"research_phase": "RESEARCH", "current_city": geo_label, "current_source": "seed"})
                candidates = [
                    b for b in seed_businesses
                    if (b.get("city") or "").lower() == (geo.city or "").lower() or not geo.city
                ][:geo_target] or seed_businesses[:geo_target]
            else:
                await _progress({"research_phase": "DISCOVERY", "current_city": geo_label, "current_source": "google_search"})
                try:
                    candidates = await agent.discover_candidates(geo, geo_target)
                except Exception as exc:
                    logger.warning("[AGENT] Discovery failed for %s: %s", geo.raw_location, exc)
                    candidates = []
                # Native browser discovery (Google, then DuckDuckGo) tends to
                # surface directory/aggregator pages on broad "<niche> in
                # <city>" queries; the scraper pipeline (Google Maps, Yellow
                # Pages) returns real businesses. Supplement — not just on a
                # zero result, but whenever native discovery came back thin —
                # then dedup by website domain / name.
                if len(candidates) < geo_target and discovery_fallback is not None:
                    logger.info(
                        "[AGENT] Native discovery returned %d/%d candidate(s) for '%s' — "
                        "supplementing from the scraper pipeline", len(candidates), geo_target, geo_label,
                    )
                    await _progress({"current_source": "scraper_fallback"})
                    try:
                        extra = list(await discovery_fallback(
                            niche, geo.city or geo.raw_location, geo.country or "", geo_target,
                        ) or [])
                    except Exception as exc:
                        logger.warning("[AGENT] Discovery fallback failed for %s: %s", geo_label, exc)
                        extra = []
                    seen = {
                        (_domain(c.get("website") or "") or (c.get("business_name") or "").lower())
                        for c in candidates
                    }
                    for hint in extra:
                        name = hint.get("business_name") or ""
                        if _looks_like_aggregator(name, hint.get("website") or ""):
                            continue
                        key = _domain(hint.get("website") or "") or name.lower()
                        if key and key not in seen:
                            seen.add(key)
                            candidates.append(hint)
                        if len(candidates) >= geo_target:
                            break
                    logger.info("[AGENT] Discovery for '%s': %d candidate(s) after supplement", geo_label, len(candidates))
                await _progress({"research_phase": "RESEARCH"})

            for hint in candidates:
                # Backfill the geographic unit onto the candidate so State /
                # City are populated even when the source (Google Maps) didn't
                # return them as discrete fields.
                hint["city"] = hint.get("city") or geo.city
                hint["state"] = hint.get("state") or geo.state
                hint["country"] = hint.get("country") or geo.country
                if len(leads) >= target_count:
                    break
                if is_cancelled and await is_cancelled():
                    logger.info("[AGENT] Session cancelled — stopping before next lead")
                    return {"leads": leads, "failed_count": failed_count, "skipped_count": skipped_count,
                            "geo_tasks": geo_tasks, "processed_keys": processed_keys}

                key = candidate_key(hint)
                if key in already_processed:
                    skipped_count += 1
                    await _progress({"businesses_skipped": skipped_count})
                    continue

                researched_count += 1
                await _progress({
                    "current_business": hint.get("business_name") or "",
                    "businesses_researched": researched_count,
                })
                try:
                    lead = await agent.research_business(hint, on_action=on_action, is_cancelled=is_cancelled)
                    # Persist BEFORE counting this lead as done — if
                    # on_lead_complete (the DB write) raises, this lead must
                    # land in failed_count, not be silently counted as
                    # successful while never actually being saved.
                    if on_lead_complete:
                        await on_lead_complete(lead)
                    leads.append(lead)
                    already_processed.add(key)
                    processed_keys.append(key)
                    await _progress({"processed_keys": processed_keys})
                except Exception as exc:
                    failed_count += 1
                    logger.error("[AGENT] Research failed for %s: %s", hint.get("business_name"), exc, exc_info=True)

    await _progress({"research_phase": "DONE"})
    return {"leads": leads, "failed_count": failed_count, "skipped_count": skipped_count,
            "geo_tasks": geo_tasks, "processed_keys": processed_keys}
