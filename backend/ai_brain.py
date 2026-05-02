"""
ai_brain.py — PopupGenix AI outreach engine.

v2 (Upgrade 4): Single-prompt message generation powered by enrichment +
scoring context. Generates 7 highly personalised messages in one Ollama call.

Message flow:
  Step 1  — Cold email        (send immediately)
  Step 2  — WhatsApp          (send immediately)
  Step 3  — Follow-up Day 3   (email, scheduled)
  Step 4  — Follow-up Day 7   (email, scheduled, final touch)

Public API (all async):
  generate_messages_v2(lead, enriched, scores, company_dna)  → 7-key dict
  generate_messages(lead, company_dna)                        → 6-key dict (v1)
  generate_all_messages(lead)                                 → legacy keys
  generate_followup_sequence(lead)                            → 4-key dict
  generate_message(lead, message_type)                        → str
  test_generate(business_info)                                → dict
  get_ollama_status()                                         → status dict
"""

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import database as db
from .config import get_settings

try:
    import ollama as _ollama_pkg
    _HAS_OLLAMA_LIB = True
except ImportError:
    _HAS_OLLAMA_LIB = False

_env   = get_settings()
logger = logging.getLogger(__name__)

MAX_RETRIES: int = 3

# Statuses that should never be regressed by message generation
_NO_MSG_STATUS_CHANGE = frozenset({"SENT", "REPLIED", "SKIPPED"})

# ── Opening style variety pool ─────────────────────────────────────────────────

_OPENING_STYLES: List[str] = [
    "Open with a genuine observation about a challenge businesses in their niche commonly face.",
    "Start with a compelling question that highlights a pain point they experience daily.",
    "Begin with a surprising insight about a trend currently affecting their industry.",
    "Open by referencing the competitive pressure in their specific city and how to stand out.",
    "Start with a result-angle — something a similar business achieved working with your company.",
    "Begin with a direct, confident value statement tailored to their exact industry type.",
    "Open by painting a scenario of the specific problem they are facing right now.",
    "Start by naming something businesses in their niche always struggle with but rarely discuss.",
    "Open with a counterintuitive insight about what actually drives growth in their market.",
    "Begin with empathy — acknowledge how the market has shifted, then pivot to clear value.",
    "Start with a specific metric or outcome that would genuinely matter to their business.",
    "Open with a short, punchy hook that immediately signals you understand their world.",
]

_TEMPERATURE_RANGE: Tuple[float, float] = (0.65, 0.88)

_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>", re.DOTALL)


# ── Text helpers ───────────────────────────────────────────────────────────────

def _strip_thinking(text: str) -> str:
    return _THINK_BLOCK_RE.sub("", text).strip()


def _fix_mojibake(text: str) -> str:
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _coerce_list(val: Any) -> List[str]:
    """Normalise a DB TEXT[] column or delimited string into a list."""
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [s.strip() for s in re.split(r"[;\n]+", val) if s.strip()]
    return []


# ── JSON schemas — v1 ─────────────────────────────────────────────────────────

_WA_KEYS = frozenset({"first_message", "follow_up_1", "follow_up_2", "follow_up_3"})
_WA_ALIASES: Dict[str, List[str]] = {
    "first_message": ["whatsapp_message", "whatsapp", "message", "initial_message",
                      "outreach", "first_outreach", "wa_message"],
    "follow_up_1":   ["followup_1", "follow_up1", "fu1", "followup_message",
                      "followup", "follow_up", "follow_up_message"],
    "follow_up_2":   ["followup_2", "follow_up2", "fu2", "second_followup"],
    "follow_up_3":   ["followup_3", "follow_up3", "fu3", "final_message", "final_followup"],
}

_EMAIL_KEYS = frozenset({"email_subject", "email_body"})
_EMAIL_ALIASES: Dict[str, List[str]] = {
    "email_subject": ["subject", "subject_line", "email_subject_line", "email_title", "title"],
    "email_body":    ["body", "email", "email_content", "email_text", "content", "email_message"],
}

# ── JSON schemas — v2 ─────────────────────────────────────────────────────────

_V2_KEYS = frozenset({
    "email_subject", "email_body", "whatsapp_message",
    "followup_day3_subject", "followup_day3_body",
    "followup_day7_subject", "followup_day7_body",
})

_V2_ALIASES: Dict[str, List[str]] = {
    "email_subject":         ["subject", "cold_email_subject", "email_sub", "email_title"],
    "email_body":            ["body", "cold_email", "email_content", "email_text", "email"],
    "whatsapp_message":      ["whatsapp", "wa_message", "sms", "text", "first_message"],
    "followup_day3_subject": ["day3_subject", "followup_3_subject", "fu3_subject",
                              "follow_up_day3_subject", "day_3_subject"],
    "followup_day3_body":    ["day3_body", "followup_3", "fu_day3", "follow_up_day3",
                              "followup_3_body", "day_3_body"],
    "followup_day7_subject": ["day7_subject", "followup_7_subject", "fu7_subject",
                              "follow_up_day7_subject", "day_7_subject"],
    "followup_day7_body":    ["day7_body", "followup_7", "fu_day7", "follow_up_day7",
                              "followup_7_body", "day_7_body"],
}


# ── Config helpers ─────────────────────────────────────────────────────────────

async def _detect_available_model(base_url: str, preferred: str) -> str:
    """
    Query Ollama /api/tags and return the best available model.
    Priority: exact match → prefix match → any available → keep preferred.
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{base_url}/api/tags")
            if r.status_code != 200:
                return preferred
            available: List[str] = [m["name"] for m in r.json().get("models", [])]
            if not available:
                return preferred
            if preferred in available:
                return preferred
            prefix = preferred.split(":")[0]
            match  = next((m for m in available if m.startswith(prefix)), None)
            if match:
                logger.warning("ai_brain: model '%s' → '%s' (prefix match)", preferred, match)
                return match
            logger.warning(
                "ai_brain: model '%s' not found → '%s' (first available). Available: %s",
                preferred, available[0], available,
            )
            return available[0]
    except Exception as exc:
        logger.debug("ai_brain: model detection failed (%s) — keeping '%s'", exc, preferred)
        return preferred


async def _ollama_cfg() -> Dict[str, Any]:
    """Build runtime Ollama config from DB settings with .env fallback."""
    stored   = await db.get_all_settings()
    base_url = stored.get("ollama_base_url") or _env.ollama_base_url
    model    = stored.get("ollama_model")    or _env.ollama_model
    timeout  = int(stored.get("ollama_timeout") or _env.ollama_timeout)
    model    = await _detect_available_model(base_url, model)
    return {"base_url": base_url, "model": model, "timeout": timeout}


def _load_company_dna() -> str:
    path = Path(_env.company_dna_path)
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── JSON extraction engine ─────────────────────────────────────────────────────

def _map_keys(
    data:     Dict[str, Any],
    required: frozenset,
    aliases:  Dict[str, List[str]],
) -> Optional[Dict[str, str]]:
    result: Dict[str, str] = {}
    for canonical, alias_list in aliases.items():
        if canonical in data:
            result[canonical] = str(data[canonical]).strip()
        else:
            for alias in alias_list:
                if alias in data:
                    result[canonical] = str(data[alias]).strip()
                    break
    return result if required.issubset(result) else None


def _extract_json(
    text:     str,
    required: frozenset,
    aliases:  Dict[str, List[str]],
) -> Optional[Dict[str, str]]:
    """
    Multi-strategy JSON extraction from raw LLM output.
    Tries: raw text → fenced blocks → brace spans → repaired JSON.
    """
    candidates: List[str] = [text.strip()]

    for pattern in [r"```json\s*([\s\S]+?)\s*```", r"```\s*([\s\S]+?)\s*```"]:
        for m in re.finditer(pattern, text, re.DOTALL):
            candidates.append(m.group(1).strip())

    for m in re.finditer(r"\{[\s\S]+?\}", text, re.DOTALL):
        candidates.append(m.group(0))

    repaired = re.sub(r",\s*([}\]])", r"\1", text).replace("\t", "  ")
    candidates.append(repaired.strip())

    seen: set = set()
    for candidate in candidates:
        key = candidate[:200]
        if key in seen:
            continue
        seen.add(key)
        try:
            data = json.loads(candidate)
            if not isinstance(data, dict):
                continue
            if required.issubset(data):
                return {k: str(data[k]).strip() for k in required}
            flexible = _map_keys(data, required, aliases)
            if flexible:
                return flexible
        except (json.JSONDecodeError, ValueError):
            continue

    return None


# ── Fallback messages ──────────────────────────────────────────────────────────

def _fallback_wa(reason: str = "") -> Dict[str, str]:
    logger.warning("ai_brain: WhatsApp fallback used — %s", reason or "no model response")
    return {
        "first_message":  "Hi! I came across your business and wanted to reach out. We help local businesses grow with digital marketing. Would you be open to a quick chat?",
        "follow_up_1":    "Just following up on my earlier message. We've helped many businesses in your area attract more customers. Happy to share how — interested?",
        "follow_up_2":    "One more note — we specialise in helping businesses get more visible online and convert more leads. Let me know if this sounds useful!",
        "follow_up_3":    "No pressure at all. If you ever want to explore growth options for your business, feel free to reach out anytime. Wishing you continued success!",
    }


def _fallback_email(reason: str = "") -> Dict[str, str]:
    logger.warning("ai_brain: email fallback used — %s", reason or "no model response")
    return {
        "email_subject": "Quick question about your business",
        "email_body":    (
            "Hi,\n\n"
            "I came across your business and wanted to reach out about a growth opportunity "
            "that might interest you.\n\n"
            "We help local businesses attract more customers through targeted digital marketing "
            "— typically seeing results within the first 30 days.\n\n"
            "Would you be open to a quick 10-minute call to see if we'd be a good fit?\n\n"
            "Best regards"
        ),
    }


def _fallback_messages(reason: str = "") -> Dict[str, str]:
    return {**_fallback_wa(reason), **_fallback_email(reason)}


def _fallback_v2(reason: str = "") -> Dict[str, str]:
    logger.warning("ai_brain: v2 fallback used — %s", reason or "no model response")
    return {
        "email_subject":         "Quick question about your business",
        "email_body":            (
            "Hi,\n\n"
            "I came across your business and wanted to reach out about a growth opportunity.\n\n"
            "We help local businesses attract more customers through targeted digital marketing.\n\n"
            "Would you be open to a quick call to see if we'd be a good fit?\n\n"
            "Best regards"
        ),
        "whatsapp_message":      "Hi! I found your business and wanted to reach out. We help businesses like yours grow with digital marketing. Would you be open to a quick chat?",
        "followup_day3_subject": "Following up — growth opportunity for your business",
        "followup_day3_body":    (
            "Hi,\n\nJust following up on my previous email. "
            "We've helped businesses in your area see measurable growth within weeks.\n\n"
            "Happy to share a quick example — interested?\n\nBest regards"
        ),
        "followup_day7_subject": "Last note — digital growth for your business",
        "followup_day7_body":    (
            "Hi,\n\nI know you're busy, so I'll keep this brief. "
            "If you ever want to explore how we can help your business grow online, "
            "just reply and we'll set up a quick call.\n\nBest regards"
        ),
    }


# ── Prompt builders ────────────────────────────────────────────────────────────

def _build_lead_context(lead: Dict[str, Any]) -> str:
    parts = [f"Business: {lead.get('business_name') or 'Unknown'}"]
    if lead.get("niche"):               parts.append(f"Niche: {lead['niche']}")
    if lead.get("city"):                parts.append(f"Location: {lead['city']}")
    if lead.get("website"):             parts.append(f"Website: {lead['website']}")
    if lead.get("phone"):               parts.append(f"Phone: {lead['phone']}")
    if lead.get("rating"):              parts.append(f"Rating: {lead['rating']}/5")
    if lead.get("review_count"):        parts.append(f"Reviews: {lead['review_count']}")
    if lead.get("website_summary"):     parts.append(f"Website Summary: {lead['website_summary']}")
    if lead.get("business_gaps"):       parts.append(f"Identified Gaps: {lead['business_gaps']}")
    if lead.get("personalization_hook"): parts.append(f"Personalization Detail: {lead['personalization_hook']}")
    return "\n".join(parts)


def _build_master_prompt(lead: Dict[str, Any], company_dna: str) -> str:
    """v1 WhatsApp sequence: first_message + 3 follow-ups."""
    biz     = lead.get("business_name") or "the business"
    niche   = lead.get("niche")         or "their industry"
    city    = lead.get("city")          or ""
    website = lead.get("website")       or "no website listed"
    style   = random.choice(_OPENING_STYLES)

    enrichment_ctx = ""
    if lead.get("website_summary"):
        enrichment_ctx += f"\nWebsite Analysis: {lead['website_summary']}"
    if lead.get("business_gaps"):
        enrichment_ctx += f"\nIdentified Gaps: {lead['business_gaps']}"
    if lead.get("personalization_hook"):
        enrichment_ctx += f"\nPersonalization Hook: {lead['personalization_hook']}"
    if lead.get("rating"):
        enrichment_ctx += f"\nRating: {lead['rating']}/5 ({lead.get('review_count', 0)} reviews)"

    return f"""You are an AI sales agent working for PopupGenix.
Generate 4 personalized WhatsApp outreach messages for this lead.
ALWAYS return a real message. NEVER leave any field empty or use placeholder text.

COMPANY CONTEXT:
{company_dna.strip()[:400]}

LEAD DATA:
Business: {biz}
Industry: {niche}
Location: {city}
Website: {website}{enrichment_ctx}

OPENING STYLE FOR FIRST MESSAGE: {style}

STRICT RULES:
- 50-120 words per message, plain text, NO asterisks, NO markdown
- Sound human — NOT like a bot or a template
- Mention the business name or industry specifically
- Identify exactly 1 realistic pain point they likely face
- Offer 1 clear solution we provide
- End with a soft CTA (question or invitation, not a hard sell)
- Each follow-up MUST use a different angle or value point
- Do NOT repeat the same line across messages
- Do NOT say "I hope this finds you well" or "I wanted to reach out"

MESSAGE TIMING:
- first_message: initial cold outreach (Day 0)
- follow_up_1:   gentle follow-up 3 days later — different hook, no pressure
- follow_up_2:   10 days after first — add new value or case study angle
- follow_up_3:   17 days after first — brief, graceful exit with open door

OUTPUT — return ONLY this JSON object, starting with {{ and ending with }}:
{{
  "first_message": "initial WhatsApp outreach message here",
  "follow_up_1": "3-day follow-up message here",
  "follow_up_2": "10-day follow-up message here",
  "follow_up_3": "17-day final message here"
}}

NO text before or after the JSON. Start with {{."""


def _build_email_prompt(lead: Dict[str, Any], company_dna: str) -> str:
    """v1 cold email: subject + body."""
    biz   = lead.get("business_name") or "the business"
    niche = lead.get("niche")         or "general"
    city  = lead.get("city")          or ""

    return f"""You are a cold email copywriter for PopupGenix.
{company_dna.strip()[:300]}

Write a cold email for this lead:
Business: {biz}
Industry: {niche}
Location: {city}

RULES: professional tone, references niche and city, plain text, 120-180 word body, soft CTA.
Subject: 40-55 characters, personalized, curiosity-driven.

OUTPUT — ONLY this JSON, start with {{ end with }}:
{{
  "email_subject": "subject line here",
  "email_body": "email body here"
}}"""


def _build_v2_prompt(
    lead:        Dict[str, Any],
    enriched:    Dict[str, Any],
    scores:      Dict[str, Any],
    company_dna: str,
) -> str:
    """
    v2 prompt: ONE call → 7 personalised messages using full enrichment context.

    Merges lead + enriched + scores into a single lookup dict so every
    signal is available regardless of which dict it came from.
    """
    m = {**lead, **enriched, **scores}

    biz   = m.get("business_name") or "this business"
    niche = m.get("niche")         or "their industry"
    city  = m.get("city")          or ""
    web   = m.get("website")       or "no website"
    loc   = f" in {city}" if city else ""

    # Enrichment signals — prefer dedicated fields, fall back to legacy columns
    biz_summary  = (m.get("business_summary") or m.get("website_summary") or "").strip()[:300]
    gaps_raw     = m.get("marketing_gaps") or m.get("business_gaps") or []
    gaps         = _coerce_list(gaps_raw)
    gaps_text    = " | ".join(gaps[:3]) if gaps else "no specific gaps identified"
    hook         = (m.get("personalization_hook") or "no hook available").strip()[:200]
    best_pitch   = (m.get("best_pitch_strategy") or m.get("pain_points") or "").strip()[:250]

    # Scoring context — enriches pitch angle and urgency framing
    key_problems = _coerce_list(m.get("key_problems") or [])
    problems_txt = " | ".join(key_problems[:3]) if key_problems else "not analyzed"
    opp_summary  = (m.get("opportunity_summary") or "").strip()[:200]
    pitch_angle  = (m.get("pitch_angle") or best_pitch or "digital presence improvement").strip()[:200]
    category     = (m.get("category") or m.get("score_label") or "WARM").upper()

    if not biz_summary:
        biz_summary = f"a {niche.lower()} business{loc}"

    return f"""You are a sales expert writing outreach messages. Use the research below to write \
HIGHLY PERSONALIZED messages. Do not be generic. Reference specific things about their business.

ABOUT US (the sender):
{company_dna.strip()[:400]}

LEAD RESEARCH:
Business: {biz} — {niche}{loc}
Website: {web}
What they do: {biz_summary}
Their marketing gaps: {gaps_text}
Personalization hook: {hook}
Best pitch approach: {best_pitch or "not specified"}
Key problems: {problems_txt}
Opportunity context: {opp_summary or "good outreach target"}
Lead priority: {category}

RULES:
- Cold Email: 4 sentences max. Structure: [Specific Observation about THEM] → \
[Problem they have] → [Value we offer] → [Soft CTA]
- WhatsApp: 2 sentences only. Casual tone. Mention 1 specific thing about their business.
- Follow-up Day 3: Different angle, reference the first email, 3 sentences max.
- Follow-up Day 7: Final touch, create mild urgency, 2 sentences only.
- NEVER start with "I hope this email finds you well"
- NEVER be generic — if you cannot be specific, write "I NEED MORE INFO" instead
- Plain text only — no asterisks, no markdown, no bullet points in message bodies

Return ONLY valid JSON with exactly these 7 keys, no other text, no markdown:
{{
  "email_subject": "personalized subject line (max 55 chars)",
  "email_body": "4-sentence cold email body",
  "whatsapp_message": "2-sentence casual WhatsApp message",
  "followup_day3_subject": "Day 3 follow-up subject line",
  "followup_day3_body": "3-sentence Day 3 follow-up email body",
  "followup_day7_subject": "Day 7 final subject line",
  "followup_day7_body": "2-sentence Day 7 final email body"
}}"""


def _build_repair_prompt(bad_output: str, required: frozenset, attempt: int) -> str:
    keys_example = {k: f"<{k} text here>" for k in required}
    return f"""Your previous output (attempt {attempt}) was invalid JSON or missing required keys.

Required keys: {sorted(required)}

Your bad output was:
---
{bad_output[:1500]}
---

Return ONLY valid JSON with ALL required keys filled in. No other text. Start with {{:

{json.dumps(keys_example, indent=2)}"""


def _build_individual_prompt(lead: Dict[str, Any], message_type: str, dna: str) -> str:
    biz   = lead.get("business_name", "the business")
    niche = lead.get("niche", "general")
    city  = lead.get("city", "")
    web   = lead.get("website", "")

    context = (
        f"You are an outreach copywriter for PopupGenix.\n"
        f"Company: {dna[:300]}\n"
        f"Target: {biz}, {niche} business{' in ' + city if city else ''}. "
        f"Website: {web or 'none'}.\n"
        f"Write ONLY the message — no preamble, no commentary, plain text, no asterisks."
    )

    instructions = {
        "whatsapp":      "Write a casual WhatsApp message (under 100 words). Specific to their niche. Clear CTA.",
        "email_subject": "Write ONLY a subject line (40-55 characters). Personalized, curiosity-driven.",
        "email_body":    "Write a cold email body (120-180 words). Professional, references niche and city. Soft CTA.",
        "followup":      "Write a follow-up WhatsApp message (under 70 words). Fresh angle, friendly, not desperate.",
        "follow_up_1":   "Write follow-up #1 (after 3 days, under 80 words). Different hook from initial. Friendly.",
        "follow_up_2":   "Write follow-up #2 (after 10 days, under 80 words). New value angle or benefit.",
        "follow_up_3":   "Write follow-up #3 (after 17 days, under 70 words). Brief graceful close, open door.",
    }

    if message_type not in instructions:
        raise ValueError(f"Unknown message_type: {message_type!r}. Valid: {sorted(instructions)}")

    return f"{context}\n\n{instructions[message_type]}"


# ── Ollama call layer ──────────────────────────────────────────────────────────

async def _call_ollama_raw(
    prompt:      str,
    cfg:         Dict[str, Any],
    temperature: Optional[float] = None,
    num_predict: int = 800,
) -> str:
    temp  = temperature if temperature is not None else round(random.uniform(*_TEMPERATURE_RANGE), 2)
    model = cfg["model"]

    logger.debug(
        "ai_brain: → %s/api/generate | model=%s | tokens=%d | temp=%.2f",
        cfg["base_url"], model, num_predict, temp,
    )

    async with httpx.AsyncClient(timeout=cfg["timeout"]) as client:
        r = await client.post(
            f"{cfg['base_url']}/api/generate",
            json={
                "model":   model,
                "prompt":  prompt,
                "stream":  False,
                "think":   False,
                "options": {"temperature": temp, "top_p": 0.92, "num_predict": num_predict},
            },
        )

        if r.status_code == 404:
            body = ""
            try:
                body = r.json().get("error", "")
            except Exception:
                pass
            logger.error(
                "ai_brain: 404 from Ollama | model=%s | endpoint=%s/api/generate | %s",
                model, cfg["base_url"], body or "model not found",
            )

        r.raise_for_status()
        raw = json.loads(r.content.decode("utf-8")).get("response", "")
        return _strip_thinking(_fix_mojibake(raw))


# ── Generic JSON generation loop ───────────────────────────────────────────────

async def _generate_phase(
    prompt_fn,
    required:    frozenset,
    aliases:     Dict[str, List[str]],
    fallback_fn,
    cfg:         Dict[str, Any],
    num_predict: int,
    label:       str,
) -> Dict[str, str]:
    """
    Retry-with-repair loop for one JSON generation phase.
    Auto-heals Ollama 404 (wrong model name) on first attempt.
    """
    prompt      = prompt_fn()
    last_output = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            active_prompt = prompt if attempt == 1 else _build_repair_prompt(last_output, required, attempt)
            temp          = round(random.uniform(*_TEMPERATURE_RANGE), 2) if attempt == 1 else 0.25

            raw         = await _call_ollama_raw(active_prompt, cfg, temperature=temp, num_predict=num_predict)
            last_output = raw
            parsed      = _extract_json(raw, required, aliases)

            if parsed:
                if attempt > 1:
                    logger.info("ai_brain [%s]: JSON recovered on attempt %d", label, attempt)
                return parsed

            logger.warning(
                "ai_brain [%s]: attempt %d unparseable (%d chars): %s…",
                label, attempt, len(raw), raw[:120].replace("\n", " "),
            )

        except httpx.HTTPStatusError as exc:
            sc = exc.response.status_code
            logger.error("ai_brain [%s]: HTTP %s on attempt %d | model=%s", label, sc, attempt, cfg["model"])

            if sc == 404:
                healed = await _detect_available_model(cfg["base_url"], cfg["model"])
                if healed != cfg["model"]:
                    logger.info("ai_brain [%s]: 404 healed → switching to '%s'", label, healed)
                    cfg = {**cfg, "model": healed}
                    continue

            if attempt == MAX_RETRIES:
                return fallback_fn(f"HTTP {sc}")

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.error("ai_brain [%s]: connection/timeout attempt %d: %s", label, attempt, exc)
            if attempt == MAX_RETRIES:
                return fallback_fn("Ollama unreachable — is it running?")

        except Exception as exc:
            logger.error("ai_brain [%s]: unexpected error attempt %d: %s", label, attempt, exc, exc_info=True)
            if attempt == MAX_RETRIES:
                return fallback_fn(f"{type(exc).__name__}: {exc}")

    logger.error("ai_brain [%s]: all %d attempts failed. Snippet:\n%s", label, MAX_RETRIES, last_output[:400])
    return fallback_fn("JSON parse failed after all retries")


# ── v2 persistence helpers ─────────────────────────────────────────────────────

async def _persist_v2_messages(lead_id: int, msgs: Dict[str, str]) -> None:
    """
    Replace all messages for a lead with the 4-step v2 sequence:
      Step 1 — cold email        (send immediately)
      Step 2 — WhatsApp          (send immediately)
      Step 3 — email follow-up   (scheduled: now + 3 days)
      Step 4 — email final touch (scheduled: now + 7 days)
    """
    now = datetime.now(timezone.utc)

    # Atomically replace — delete old rows first so regeneration stays clean
    await db.delete_lead_messages(lead_id)

    rows = [
        {
            "lead_id":       lead_id,
            "sequence_step": 1,
            "message_type":  "email",
            "subject":       msgs["email_subject"],
            "body":          msgs["email_body"],
            "status":        "PENDING",
        },
        {
            "lead_id":       lead_id,
            "sequence_step": 2,
            "message_type":  "whatsapp",
            "body":          msgs["whatsapp_message"],
            "status":        "PENDING",
        },
        {
            "lead_id":        lead_id,
            "sequence_step":  3,
            "message_type":   "email",
            "subject":        msgs["followup_day3_subject"],
            "body":           msgs["followup_day3_body"],
            "status":         "PENDING",
            "scheduled_for":  now + timedelta(days=3),
        },
        {
            "lead_id":        lead_id,
            "sequence_step":  4,
            "message_type":   "email",
            "subject":        msgs["followup_day7_subject"],
            "body":           msgs["followup_day7_body"],
            "status":         "PENDING",
            "scheduled_for":  now + timedelta(days=7),
        },
    ]

    for row in rows:
        try:
            await db.create_message(row)
        except Exception as exc:
            logger.error(
                "_persist_v2_messages: step %d insert failed for lead %d: %s",
                row["sequence_step"], lead_id, exc,
            )


def _has_enrichment(lead: Dict[str, Any]) -> bool:
    """True when the lead dict carries any enrichment-stage signals."""
    return bool(
        lead.get("website_summary") or
        lead.get("personalization_hook") or
        lead.get("business_gaps")
    )


def _extract_enrichment_from_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a minimal enriched dict from the legacy lead columns so
    generate_all_messages() can use the v2 prompt without a DB round-trip.
    """
    return {
        "business_summary":    lead.get("website_summary") or "",
        "marketing_gaps":      lead.get("business_gaps")   or "",
        "personalization_hook": lead.get("personalization_hook") or "",
        "best_pitch_strategy": lead.get("pain_points")     or "",
    }


# ══ PUBLIC API ══════════════════════════════════════════════════════════════════


async def generate_messages_v2(
    lead:        Dict[str, Any],
    enriched:    Optional[Dict[str, Any]],
    scores:      Optional[Dict[str, Any]],
    company_dna: str,
) -> Dict[str, str]:
    """
    Generate 7 highly personalised messages in ONE Ollama call.

    Uses enrichment + scoring context for maximum specificity.
    Persists the full 4-step message sequence to the messages table
    and updates the legacy lead columns for UI backward-compat.

    Parameters
    ----------
    lead         : lead dict from DB
    enriched     : output of enrich_lead_with_ai() / get_enriched_data()
    scores       : output of score_lead() / get_score()
    company_dna  : contents of company_dna.txt

    Returns
    -------
    7-key dict: email_subject, email_body, whatsapp_message,
                followup_day3_subject, followup_day3_body,
                followup_day7_subject, followup_day7_body
    """
    lead_id = lead.get("id")
    e = enriched or {}
    s = scores   or {}

    cfg  = await _ollama_cfg()
    msgs = await _generate_phase(
        prompt_fn   = lambda: _build_v2_prompt(lead, e, s, company_dna),
        required    = _V2_KEYS,
        aliases     = _V2_ALIASES,
        fallback_fn = _fallback_v2,
        cfg         = cfg,
        num_predict = 1000,
        label       = "messages_v2",
    )

    if lead_id:
        # Persist 4-step sequence to messages table (replaces any previous messages)
        await _persist_v2_messages(lead_id, msgs)

        # Keep legacy lead columns in sync for UI and scheduler compatibility
        lead_update: Dict[str, Any] = {
            "ai_whatsapp_msg":  msgs["whatsapp_message"],
            "ai_email_subject": msgs["email_subject"],
            "ai_email_body":    msgs["email_body"],
            "ai_followup_msg":  msgs["followup_day3_body"],
            "ai_follow_up_1":   msgs["followup_day3_body"],
            "ai_follow_up_2":   msgs["followup_day7_body"],
            "ai_follow_up_3":   msgs["followup_day7_body"],
        }
        current_status = (lead.get("status") or "").upper()
        if current_status not in _NO_MSG_STATUS_CHANGE:
            lead_update["status"] = "MESSAGES_READY"

        try:
            await db.update_lead(lead_id, lead_update)
        except Exception as exc:
            logger.error("generate_messages_v2: lead update failed for %d: %s", lead_id, exc)

    logger.info(
        "generate_messages_v2: lead %d → 7 messages generated (category=%s)",
        lead_id or 0, (s or {}).get("category", "?"),
    )

    return msgs


async def generate_messages(lead: Dict[str, Any], company_dna: str) -> Dict[str, str]:
    """
    v1: Generate 6 messages in two parallel Ollama calls.
    Phase 1: first_message + follow_up_1/2/3  (WhatsApp sequence)
    Phase 2: email_subject + email_body
    """
    cfg = await _ollama_cfg()

    wa_task = _generate_phase(
        prompt_fn   = lambda: _build_master_prompt(lead, company_dna),
        required    = _WA_KEYS,
        aliases     = _WA_ALIASES,
        fallback_fn = _fallback_wa,
        cfg         = cfg,
        num_predict = 900,
        label       = "wa_sequence",
    )
    email_task = _generate_phase(
        prompt_fn   = lambda: _build_email_prompt(lead, company_dna),
        required    = _EMAIL_KEYS,
        aliases     = _EMAIL_ALIASES,
        fallback_fn = _fallback_email,
        cfg         = cfg,
        num_predict = 500,
        label       = "email",
    )

    wa_result, email_result = await asyncio.gather(wa_task, email_task)

    return {
        **wa_result,
        **email_result,
        "whatsapp_message": wa_result["first_message"],
        "followup_message": wa_result["follow_up_1"],
    }


async def generate_followup_sequence(lead: Dict[str, Any]) -> Dict[str, str]:
    """Generate (or regenerate) the 3 follow-up messages for an existing lead."""
    dna = _load_company_dna()
    cfg = await _ollama_cfg()
    return await _generate_phase(
        prompt_fn   = lambda: _build_master_prompt(lead, dna),
        required    = _WA_KEYS,
        aliases     = _WA_ALIASES,
        fallback_fn = _fallback_wa,
        cfg         = cfg,
        num_predict = 900,
        label       = "followup_sequence",
    )


async def test_generate(business_info: str) -> Dict[str, str]:
    """Generate all messages from free-form business description text."""
    business_info = (business_info or "").strip()
    if not business_info:
        return _fallback_messages("empty business_info")
    fake_lead = {"business_name": business_info, "niche": "", "city": "", "website": ""}
    dna       = _load_company_dna()
    return await generate_messages(fake_lead, dna)


# ── Legacy API — backward-compatible with routers/leads.py + scheduler.py ─────

async def generate_all_messages(lead: Dict[str, Any]) -> Dict[str, str]:
    """
    Generate all messages. Automatically uses the v2 enrichment-aware prompt
    when the lead dict carries enrichment signals; falls back to two-phase v1
    otherwise. Never persists to DB (caller owns that responsibility).

    Returns both new and legacy key names for maximum compatibility.
    """
    dna = _load_company_dna()

    if _has_enrichment(lead):
        cfg      = await _ollama_cfg()
        enriched = _extract_enrichment_from_lead(lead)
        msgs     = await _generate_phase(
            prompt_fn   = lambda: _build_v2_prompt(lead, enriched, {}, dna),
            required    = _V2_KEYS,
            aliases     = _V2_ALIASES,
            fallback_fn = _fallback_v2,
            cfg         = cfg,
            num_predict = 1000,
            label       = "messages_v2_auto",
        )
        return {
            **msgs,
            # Legacy keys expected by scheduler + regenerate endpoint
            "first_message":  msgs["whatsapp_message"],
            "whatsapp":       msgs["whatsapp_message"],
            "follow_up_1":    msgs["followup_day3_body"],
            "follow_up_2":    msgs["followup_day7_body"],
            "follow_up_3":    msgs["followup_day7_body"],
            "followup":       msgs["followup_day3_body"],
        }
    else:
        result = await generate_messages(lead, dna)
        return {
            **result,
            "whatsapp":      result["first_message"],
            "email_subject": result["email_subject"],
            "email_body":    result["email_body"],
            "followup":      result["follow_up_1"],
        }


async def generate_messages_from_text(business_text: str) -> Dict[str, str]:
    """Generate all messages from raw text; returns legacy + new keys."""
    result = await test_generate(business_text)
    return {
        **result,
        "whatsapp":      result.get("first_message", ""),
        "email_subject": result.get("email_subject", ""),
        "email_body":    result.get("email_body", ""),
        "followup":      result.get("follow_up_1", ""),
    }


async def generate_message(lead: Dict[str, Any], message_type: str) -> str:
    """
    Generate a single message type via a focused individual prompt.
    Supports: whatsapp, email_subject, email_body, followup,
              follow_up_1, follow_up_2, follow_up_3
    """
    _VALID = {"whatsapp", "email_subject", "email_body", "followup",
              "follow_up_1", "follow_up_2", "follow_up_3"}
    if message_type not in _VALID:
        raise ValueError(f"Unknown message_type: {message_type!r}. Valid: {sorted(_VALID)}")

    dna    = _load_company_dna()
    cfg    = await _ollama_cfg()
    prompt = _build_individual_prompt(lead, message_type, dna)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await _call_ollama_raw(prompt, cfg, temperature=0.72, num_predict=400)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                healed = await _detect_available_model(cfg["base_url"], cfg["model"])
                if healed != cfg["model"]:
                    cfg = {**cfg, "model": healed}
                    continue
            if attempt == MAX_RETRIES:
                raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"Ollama unreachable after {MAX_RETRIES} attempts: {exc}") from exc
            await asyncio.sleep(1.5 * attempt)
        except Exception as exc:
            logger.error("generate_message attempt %d failed: %s", attempt, exc)
            if attempt == MAX_RETRIES:
                raise

    raise RuntimeError("generate_message: exhausted retries")


# ── Status check ───────────────────────────────────────────────────────────────

async def get_ollama_status() -> Dict[str, Any]:
    """Ping Ollama — returns connected state, active model, and available models."""
    cfg = await _ollama_cfg()
    try:
        if _HAS_OLLAMA_LIB:
            client = _ollama_pkg.AsyncClient(host=cfg["base_url"], timeout=5)
            tags   = await client.list()
            models: List[str] = (
                [getattr(m, "model", str(m)) for m in tags.models]
                if hasattr(tags, "models") else []
            )
        else:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{cfg['base_url']}/api/tags")
                r.raise_for_status()
                models = [m["name"] for m in r.json().get("models", [])]

        return {"connected": True, "model": cfg["model"], "available_models": models}

    except Exception as exc:
        return {
            "connected":        False,
            "model":            cfg["model"],
            "available_models": [],
            "error":            str(exc),
        }
