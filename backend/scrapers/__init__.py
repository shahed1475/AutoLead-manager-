"""
scrapers/__init__.py — Parallel bulk-scraping orchestrator.

Public API
----------
  run_bulk_scrape(campaign, log_callback) → List[dict]

Pipeline
--------
  1. Split lead budget proportionally across requested sources
     (Google Maps 60%, Google Search 40% when both selected)
  2. Run all scrapers concurrently via asyncio.gather()
  3. Merge raw results; deduplicate by email (exact), phone (normalised
     last-8 digits), and business_name+city (difflib fuzzy ≥ 0.85)
  4. Validate: clear invalid email/phone fields; reject only when BOTH
     are absent after clearing
  5. Enrich leads that have a website but no email via email_finder
     (max 5 concurrent, asyncio.Semaphore)
  6. Batch-save to SQLite via create_lead_deduped (max 10 concurrent)
  7. Log summary line; return saved lead dicts (each has 'id' from DB)

Threading model
---------------
All scrapers run inside asyncio.to_thread() (or are already async).
log_callback is a plain sync callable; the caller bridges it to async
DB writes via asyncio.run_coroutine_threadsafe (see campaigns.py).
"""

import asyncio
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import database as db
from ..validators import is_valid_email, is_valid_phone

logger = logging.getLogger(__name__)


# ── Source weights ────────────────────────────────────────────────────────────
# Determines percentage of max_leads each source receives when multiple chosen.
# Google Maps (Selenium) is the most reliable/highest-volume source, so it
# gets the largest share by default — but every source below runs its OWN
# real scraper (see _dispatch_source); none of them are silently rerouted.
# HTTP-based sources can still get rate-limited or blocked by anti-bot
# measures on a given run — when that happens the run falls back to Google
# Maps for that source's budget, and the live feed says so explicitly.

_SOURCE_WEIGHTS: Dict[str, float] = {
    "GOOGLE_MAPS":   0.35,
    "GOOGLE_SEARCH": 0.10,
    "YELP":          0.12,
    "YELLOW_PAGES":  0.12,
    "BING_SEARCH":   0.08,
    "HOTFROG":       0.06,
    "FOURSQUARE":    0.06,
    "TOP_LIST":      0.05,
    "GENERIC_DIR":   0.06,
}


def _split_budget(sources: List[str], max_leads: int) -> Dict[str, int]:
    """
    Divide max_leads proportionally across sources using _SOURCE_WEIGHTS.
    The last source in the sorted list always absorbs the integer remainder.
    """
    if not sources:
        return {"GOOGLE_MAPS": max_leads}

    weights   = {s: _SOURCE_WEIGHTS.get(s, 0.5) for s in sources}
    total_w   = sum(weights.values())
    budgets:  Dict[str, int] = {}
    allocated = 0

    for i, source in enumerate(sources):
        if i == len(sources) - 1:
            budgets[source] = max(1, max_leads - allocated)
        else:
            b = max(1, round(max_leads * weights[source] / total_w))
            budgets[source] = b
            allocated      += b

    return budgets


# ── Deduplication helpers ─────────────────────────────────────────────────────

def _norm_phone(phone: str) -> str:
    """Last 8 digits — removes country-code prefix variations."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-8:] if len(digits) >= 8 else digits


def _norm_email(email: str) -> str:
    return (email or "").lower().strip()


_NAME_STOP = frozenset({
    "the", "a", "an", "and", "&", "of", "for", "at",
    "ltd", "llc", "inc", "co", "corp", "company",
    "restaurant", "cafe", "clinic", "center", "centre",
})


def _norm_name(name: str, city: str) -> str:
    """Lowercase, strip punctuation + stop words for fuzzy comparison."""
    raw = f"{name} {city}".lower()
    raw = re.sub(r"[^\w\s]", " ", raw)
    return " ".join(w for w in raw.split() if w not in _NAME_STOP)


def _is_fuzzy_dup(candidate: str, seen: List[str], threshold: float = 0.85) -> bool:
    for s in seen:
        if SequenceMatcher(None, candidate, s).ratio() >= threshold:
            return True
    return False


def _deduplicate(
    leads:  List[dict],
    log_fn: Callable[[str], None],
) -> Tuple[List[dict], int]:
    """
    Three-stage deduplication (runs in O(n log n) for email/phone, O(n²) fuzzy).
    For 500 leads the fuzzy step takes < 200 ms.
    """
    seen_emails: set        = set()
    seen_phones: set        = set()
    seen_names:  List[str]  = []
    deduped:     List[dict] = []
    removed                 = 0

    for lead in leads:
        email_key = _norm_email(lead.get("email", ""))
        phone_key = _norm_phone(lead.get("phone", ""))
        name_key  = _norm_name(
            lead.get("business_name", ""), lead.get("city", "")
        )

        if email_key and email_key in seen_emails:
            removed += 1
            continue
        if phone_key and phone_key in seen_phones:
            removed += 1
            continue
        if name_key and _is_fuzzy_dup(name_key, seen_names):
            removed += 1
            continue

        if email_key: seen_emails.add(email_key)
        if phone_key: seen_phones.add(phone_key)
        if name_key:  seen_names.append(name_key)
        deduped.append(lead)

    if removed:
        log_fn(f"   ♻️  {removed} duplicate(s) removed")
    return deduped, removed


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_and_clean(
    leads:  List[dict],
    log_fn: Callable[[str], None],
) -> Tuple[List[dict], List[dict]]:
    """
    Clear invalid email/phone fields individually; reject only when BOTH
    are absent after clearing.  Never rejects a lead solely for bad email.
    """
    valid:    List[dict] = []
    rejected: List[dict] = []

    for lead in leads:
        if not (lead.get("business_name") or "").strip():
            rejected.append({**lead, "_reject_reason": "missing business_name"})
            continue

        if lead.get("email") and not is_valid_email(lead["email"]):
            lead = {**lead, "email": None}

        if lead.get("phone") and not is_valid_phone(lead["phone"]):
            lead = {**lead, "phone": None}

        if not lead.get("email") and not lead.get("phone") and not lead.get("website"):
            rejected.append({**lead, "_reject_reason": "no email and no phone"})
            continue

        valid.append(lead)

    if rejected:
        log_fn(f"   ⚠️  {len(rejected)} lead(s) rejected (no contact info)")
    return valid, rejected


# ── Scraper dispatch (one per source) ─────────────────────────────────────────

async def _run_with_maps_fallback(
    source:  str,
    label:   str,
    coro_fn: Callable[[], Any],
    niche:   str,
    city:    str,
    budget:  int,
    cfg:     Dict[str, Any],
    log_fn:  Callable[[str], None],
    country: str = "",
) -> List[dict]:
    """
    Await `coro_fn()` — that source's REAL scraper. Falls back to Google Maps
    only if it genuinely raises or returns zero results, and always logs the
    fallback explicitly so the live feed never silently claims a source ran
    when it was actually replaced.
    """
    from .google_maps import scrape as _gm_scrape

    leads: List[dict] = []
    try:
        leads = await coro_fn()
    except Exception as exc:
        log_fn(f"   ⚠️  {label} failed ({exc}) — falling back to Google Maps")
        logger.warning("%s scraper raised: %s", source, exc, exc_info=True)
        await _log_source_event(source, "FAILED", str(exc))
        leads = []

    if not leads:
        log_fn(f"   ⚠️  {label} returned 0 results — falling back to Google Maps")
        await _log_source_event(source, "FALLBACK_TO_MAPS", "returned 0 results")
        leads = await _gm_scrape(
            niche=niche, city=city,
            max_results=budget, cfg=cfg, log_callback=log_fn, country=country,
        )
    return leads


async def _log_source_event(source: str, action: str, detail: str) -> None:
    """
    Persist a scraper failure/fallback to campaign_log (lead_id=NULL) so it's
    visible in campaign history later — the live SSE feed is not the only
    record of what happened during a run.
    """
    try:
        await db.log_campaign_action(None, "SCRAPER", f"{source}:{action}", False, detail[:500])
    except Exception:
        logger.debug("Failed to persist source event %s:%s", source, action, exc_info=True)


async def _dispatch_source(
    source:  str,
    niche:   str,
    city:    str,
    country: str,
    budget:  int,
    cfg:     Dict[str, Any],
    log_fn:  Callable[[str], None],
) -> List[dict]:
    """Run one source's scraper and return its raw lead list (never raises).

    Every selected source calls its OWN real scraper first — none of them
    are silently rerouted. A source only falls back to Google Maps if its
    real scraper genuinely raises or comes back empty (e.g. blocked by
    anti-bot measures on that particular run), and that fallback is always
    logged explicitly to the live feed.
    """
    from .google_maps      import scrape               as _gm_scrape
    from .google_search    import scrape_google_search as _gs_scrape
    from .yelp             import scrape               as _yelp_scrape
    from .yellow_pages     import scrape               as _yp_scrape
    from .bing_search      import scrape               as _bing_scrape
    from .hotfrog          import scrape               as _hotfrog_scrape
    from .foursquare       import scrape               as _fsq_scrape
    from .top_list         import scrape               as _toplist_scrape
    from .generic_directory import scrape              as _gendir_scrape

    # Sources sharing the uniform "orchestrator-compatible" scrape() signature:
    # (niche, city, max_results, cfg, log_callback, country) -> List[dict]
    _UNIFORM_SOURCES: Dict[str, Tuple[str, str, Callable]] = {
        "YELP":         ("⭐", "Yelp",               _yelp_scrape),
        "YELLOW_PAGES": ("📒", "Yellow Pages",       _yp_scrape),
        "BING_SEARCH":  ("🔎", "Bing Search",        _bing_scrape),
        "HOTFROG":      ("🔥", "Hotfrog",            _hotfrog_scrape),
        "FOURSQUARE":   ("📍", "Foursquare",         _fsq_scrape),
        "TOP_LIST":     ("📰", "Top List",           _toplist_scrape),
        "GENERIC_DIR":  ("📂", "Business Directory", _gendir_scrape),
    }

    try:
        if source == "GOOGLE_MAPS":
            log_fn(f"🗺  Google Maps        → {budget} leads")
            leads = await _gm_scrape(
                niche=niche, city=city,
                max_results=budget, cfg=cfg, log_callback=log_fn, country=country,
            )

        elif source == "GOOGLE_SEARCH":
            log_fn(f"🔍 Google Search      → {budget} leads")
            leads = await _run_with_maps_fallback(
                source, "Google Search",
                lambda: _gs_scrape(
                    niche=niche, city=city, country=country,
                    max_leads=budget, log_callback=log_fn,
                ),
                niche, city, budget, cfg, log_fn, country=country,
            )

        elif source in _UNIFORM_SOURCES:
            icon, label, scrape_fn = _UNIFORM_SOURCES[source]
            log_fn(f"{icon} {label:<18} → {budget} leads")
            leads = await _run_with_maps_fallback(
                source, label,
                lambda scrape_fn=scrape_fn: scrape_fn(
                    niche=niche, city=city, country=country,
                    max_results=budget, cfg=cfg, log_callback=log_fn,
                ),
                niche, city, budget, cfg, log_fn, country=country,
            )

        else:
            log_fn(f"⚠️  Unknown source '{source}' — using Google Maps as fallback")
            leads = await _gm_scrape(
                niche=niche, city=city,
                max_results=budget, cfg=cfg, log_callback=log_fn, country=country,
            )

        # Tag every lead with source + campaign fields. setdefault() is
        # deliberate: fallback leads already carry source="GOOGLE_MAPS" set
        # by google_maps.py itself, so this never mislabels a Maps-sourced
        # lead as the originally-requested source.
        for lead in leads:
            lead.setdefault("source",  source)
            lead.setdefault("niche",   niche)
            lead.setdefault("city",    city)
            if country:
                lead.setdefault("country", country)

        log_fn(f"   ✅ {source}: {len(leads)} raw leads")
        return leads

    except Exception as exc:
        log_fn(f"   ❌ {source} scraper failed: {exc}")
        logger.error("Scraper error (%s): %s", source, exc, exc_info=True)
        await _log_source_event(source, "DISPATCH_FAILED", str(exc))
        return []


# ── Email enrichment ──────────────────────────────────────────────────────────

async def _enrich_parallel(
    leads:          List[dict],
    log_fn:         Callable[[str], None],
    max_concurrent: int = 5,
) -> List[dict]:
    """
    For leads that have a website URL but no email, run the deep email finder
    with at most max_concurrent concurrent requests (asyncio.Semaphore).
    Mutates lead dicts in-place; always returns the same list.
    """
    try:
        from .email_finder import find_emails_from_website
    except Exception as exc:
        log_fn(f"⚠️  Email finder unavailable: {exc}")
        return leads

    candidates = [l for l in leads if l.get("website") and not l.get("email")]
    if not candidates:
        return leads

    log_fn(f"📧 Enriching {len(candidates)} lead(s) with email finder …")
    sem = asyncio.Semaphore(max_concurrent)

    async def _one(lead: dict) -> None:
        async with sem:
            try:
                result = await find_emails_from_website(lead["website"])
                if result.get("primary_email") and not lead.get("email"):
                    lead["email"] = result["primary_email"]
                if result.get("phone_numbers") and not lead.get("phone"):
                    lead["phone"] = result["phone_numbers"][0]
            except Exception as exc:
                logger.debug(
                    "Enrichment failed for %s: %s",
                    lead.get("business_name"), exc,
                )

    await asyncio.gather(*[_one(l) for l in candidates])
    return leads  # mutated in-place


# ── Database batch save ───────────────────────────────────────────────────────

_DB_FIELDS = frozenset({
    "business_name", "phone", "email", "website", "address",
    "niche", "city", "country", "source",
    "rating", "reviews_count", "review_count",
    "raw_url",
})


async def _batch_save(
    leads:  List[dict],
    log_fn: Callable[[str], None],
) -> Tuple[List[dict], int]:
    """
    Sequential SQLite saves (semaphore=1).
    SQLite WAL mode allows only ONE concurrent writer — using Semaphore(1) prevents
    "database is locked" errors that would silently discard all leads.
    Returns (list_of_saved_dicts_with_id, fail_count).
    Each returned dict has '_is_new': bool to distinguish inserts vs skipped dupes.
    """
    sem     = asyncio.Semaphore(1)          # SQLite: serialise writes
    results: List[Optional[dict]] = [None] * len(leads)
    fails   = 0

    async def _save(idx: int, lead: dict) -> None:
        nonlocal fails
        async with sem:
            try:
                # Strip internal tracking keys and None values before insert
                data = {
                    k: v for k, v in lead.items()
                    if k in _DB_FIELDS and v is not None
                }
                lead_id, is_new = await db.create_lead_deduped_with_log(data)
                results[idx] = {**lead, "id": lead_id, "_is_new": is_new}
            except Exception as exc:
                fails += 1
                # Surface error to SSE terminal so it's visible to the user
                log_fn(
                    f"   ❌ DB save failed [{lead.get('business_name', '?')}]: {exc}"
                )
                logger.error(
                    "DB save failed for '%s': %s",
                    lead.get("business_name"), exc, exc_info=True,
                )

    await asyncio.gather(*[_save(i, l) for i, l in enumerate(leads)])
    saved = [r for r in results if r is not None]
    if fails:
        log_fn(
            f"   ⚠️  {fails}/{len(leads)} lead(s) failed to save — "
            f"check DB write permissions or disk space"
        )
    return saved, fails


def _clean_lead(lead: dict) -> dict:
    """Strip internal orchestrator-only keys before returning to callers."""
    return {k: v for k, v in lead.items() if not k.startswith("_")}


# ── Public entry-point ────────────────────────────────────────────────────────

async def run_bulk_scrape(
    campaign:       Dict[str, Any],
    log_callback:   Optional[Callable[[str], None]] = None,
    stage_callback: Optional[Callable[[str], Any]]  = None,
    pause_callback: Optional[Callable[[], Any]]     = None,
) -> List[Dict[str, Any]]:
    """
    Parallel bulk-scraping orchestrator.

    Parameters
    ----------
    campaign : dict with keys:
        niche     : str   — e.g. "dental clinic"
        city      : str   — e.g. "Dubai"
        country   : str   — e.g. "UAE"  (optional — for Search TLD targeting)
        sources   : list  — e.g. ["GOOGLE_MAPS", "GOOGLE_SEARCH"]
        max_leads : int   — total target lead count
        headless  : bool  — Chrome headless mode (default True)
    log_callback : sync callable(msg: str)
        Called from both the async context and from worker threads.
        Caller should bridge it to the DB via asyncio.run_coroutine_threadsafe
        so the SSE stream picks it up (see campaigns.py).
    stage_callback : optional async callable(stage: str)
        Awaited at real phase boundaries ("SCRAPING", "ENRICHING") so the
        caller can persist the campaign's live pipeline stage. Never raises.
    pause_callback : optional async callable()
        Awaited at phase boundaries (before scraping, before enrichment,
        before the DB save) — blocks until the campaign is unpaused. Without
        this, clicking Pause during SCRAPING/ENRICHING was previously a
        no-op until that whole phase finished.

    Returns
    -------
    List of NEW lead dicts saved to PostgreSQL; each has 'id' from the DB.
    Leads that were already in the DB (duplicate on email/phone) are excluded.
    """
    async def _stage(name: str) -> None:
        if stage_callback:
            try:
                await stage_callback(name)
            except Exception:
                pass

    async def _pause() -> None:
        if pause_callback:
            try:
                await pause_callback()
            except Exception:
                pass

    niche     = str(campaign.get("niche",     "")).strip()
    city      = str(campaign.get("city",      "")).strip()
    country   = str(campaign.get("country",   "")).strip()
    sources   = [s.upper() for s in campaign.get("sources", ["GOOGLE_MAPS"])]
    max_leads = int(campaign.get("max_leads", 50))
    headless  = bool(campaign.get("headless", True))

    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    if not niche or not city:
        _log("❌ run_bulk_scrape: niche and city are required")
        return []

    _log(
        f"🚀 Bulk scrape — {niche} | {city}"
        + (f" | {country}" if country else "")
        + f" | sources: {sources} | target: {max_leads}"
    )
    t0 = time.monotonic()

    # ── 1. Budget split ───────────────────────────────────────────────────────
    budgets = _split_budget(sources, max_leads)
    _log(f"💰 Budget: { {s: b for s, b in budgets.items()} }")

    # ── 2. Scraper runtime config (reads delay/headless from DB settings) ─────
    from .. import scraper as _parent_scraper
    cfg          = await _parent_scraper._scraper_cfg()
    cfg["headless"] = headless

    # ── 3. Parallel scrape ────────────────────────────────────────────────────
    await _stage("SCRAPING")
    await _pause()
    _log(f"⚡ Launching {len(sources)} scraper(s) in parallel …")
    raw_batches = await asyncio.gather(
        *[
            _dispatch_source(src, niche, city, country, budget, cfg, _log)
            for src, budget in budgets.items()
        ],
        return_exceptions=True,
    )

    all_raw: List[dict] = []
    for batch in raw_batches:
        if isinstance(batch, Exception):
            _log(f"⚠️  Scraper exception: {batch}")
        elif isinstance(batch, list):
            all_raw.extend(batch)

    _log(f"📦 Raw leads collected: {len(all_raw)}")
    if not all_raw:
        _log("⚠️  No leads found — check niche/city or scraper settings")
        return []

    # ── 4. Deduplication ──────────────────────────────────────────────────────
    _log("♻️  Deduplicating (email, phone, fuzzy name) …")
    deduped, n_removed = _deduplicate(all_raw, _log)
    _log(f"   {len(deduped)} unique after dedup")

    # ── 5. Validation ─────────────────────────────────────────────────────────
    _log("✔️  Validating …")
    valid, rejected = _validate_and_clean(deduped, _log)
    _log(f"   Valid: {len(valid)} | Rejected: {len(rejected)}")

    if not valid:
        _log("⚠️  No valid leads after validation")
        return []

    # ── 6. Email enrichment (parallel, semaphore-limited) ─────────────────────
    await _stage("ENRICHING")
    await _pause()
    try:
        valid = await _enrich_parallel(valid, _log, max_concurrent=5)
    except Exception as exc:
        _log(f"⚠️  Email enrichment error: {exc} — saving leads without enrichment")

    # Re-validate: enrichment may have filled in missing fields
    valid, rejected2 = _validate_and_clean(valid, _log)
    rejected.extend(rejected2)

    # ── 7. Batch SQLite save ──────────────────────────────────────────────────
    await _pause()
    _log(f"💾 Saving {len(valid)} lead(s) to database …")
    saved, n_failed = await _batch_save(valid, _log)

    elapsed   = round(time.monotonic() - t0, 1)
    new_count = sum(1 for l in saved if l.get("_is_new"))
    dup_db    = len(saved) - new_count

    # ── 8. Post-save DB verification ─────────────────────────────────────────
    try:
        async with db.get_db() as _conn:
            db_total = await _conn.fetchval("SELECT COUNT(*) FROM leads") or 0
        _log(f"   📊 DB now contains {db_total} total lead(s)")
    except Exception as _ve:
        _log(f"   ⚠️  DB count check failed: {_ve}")

    # ── 9. Summary ────────────────────────────────────────────────────────────
    _log(
        f"✅ Scraped: {len(all_raw)} "
        f"| ❌ Duplicates removed: {n_removed + dup_db} "
        f"| ⚠️  Invalid: {len(rejected)} "
        f"| 💾 Saved: {new_count} new "
        f"| ⏱️  {elapsed}s"
    )
    if not saved:
        _log(
            "⚠️  No leads persisted — if this is unexpected, run "
            "POST /api/campaign/test-pipeline to diagnose DB write issues"
        )

    return [_clean_lead(l) for l in saved]


# ── Test pipeline ──────────────────────────────────────────────────────────────

async def run_test_pipeline(
    log_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Inserts 5 dummy leads directly into the DB and reads them back.
    Confirms the full write → read path works end-to-end without any scraping.
    """
    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    _log("🧪 Test pipeline: inserting 5 dummy leads to verify DB write …")

    dummies = [
        {
            "business_name": f"Test Business {i + 1}",
            "phone":         f"+97150001{1000 + i}",
            "email":         f"test{i + 1}@testbiz{i + 1}.com",
            "website":       f"https://testbiz{i + 1}.example.com",
            "niche":         "_test_",
            "city":          "TestCity",
            "source":        "TEST",
        }
        for i in range(5)
    ]

    saved_ids: List[int] = []
    errors:    List[str] = []

    for d in dummies:
        try:
            lead_id, is_new = await db.create_lead_deduped(d)
            saved_ids.append(lead_id)
            _log(
                f"   {'✅ Created' if is_new else '🔄 Already exists'}: "
                f"{d['business_name']} (id={lead_id})"
            )
        except Exception as exc:
            errors.append(str(exc))
            _log(f"   ❌ Failed to save {d['business_name']}: {exc}")

    # Read back
    db_total = 0
    test_total = 0
    try:
        async with db.get_db() as conn:
            db_total   = await conn.fetchval("SELECT COUNT(*) FROM leads") or 0
            test_total = await conn.fetchval(
                "SELECT COUNT(*) FROM leads WHERE source = 'TEST'"
            ) or 0
    except Exception as exc:
        _log(f"   ❌ DB read-back failed: {exc}")
        errors.append(str(exc))

    _log(
        f"🧪 Done — {len(saved_ids)}/5 saved | "
        f"{test_total} TEST leads in DB | {db_total} total leads"
    )

    if errors:
        _log(f"   ⚠️  {len(errors)} error(s): {errors[0]}")

    return {
        "saved":        len(saved_ids),
        "ids":          saved_ids,
        "total_leads":  db_total,
        "test_leads":   test_total,
        "errors":       errors,
        "ok":           len(errors) == 0 and len(saved_ids) == 5,
    }
