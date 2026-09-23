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
import time
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import database as db
from . import outreach_domain
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

# ── JSON schema — reply drafts ─────────────────────────────────────────────────

_REPLY_KEYS = frozenset({"reply_body"})
_REPLY_ALIASES: Dict[str, List[str]] = {
    "reply_body": ["body", "reply", "message", "reply_message", "response", "response_body"],
}


# ── Config helpers ─────────────────────────────────────────────────────────────

# General-purpose instruct families, best first. Used only when the configured
# model is missing — avoids falling back to a coder/embedding model or a narrow
# fine-tune that happens to sort first in /api/tags.
_GENERAL_MODEL_FAMILIES = ("llama3", "qwen2.5", "qwen3", "mistral", "gemma", "phi", "llama")
_NON_CHAT_MARKERS      = ("coder", "code", "embed", "vision", "llava", "whisper")


def _pick_general_model(available: List[str]) -> str:
    chat = [m for m in available if not any(k in m.lower() for k in _NON_CHAT_MARKERS)]
    for family in _GENERAL_MODEL_FAMILIES:
        match = next((m for m in chat if m.lower().startswith(family)), None)
        if match:
            return match
    return chat[0] if chat else available[0]


# When the GPU is out of memory (e.g. another process holds VRAM), Ollama
# returns HTTP 500 "cudaMalloc failed: out of memory" on every call. We then
# retry on CPU (num_gpu=0) and stay on CPU for a cool-down before re-trying GPU.
_CPU_FALLBACK_SECONDS = 600
_cpu_fallback_until: float = 0.0
_OOM_MARKERS = ("out of memory", "cudamalloc", "failed to allocate", "unable to allocate")


def _is_gpu_oom(resp: httpx.Response) -> bool:
    if resp.status_code != 500:
        return False
    try:
        body = (resp.json().get("error") or "").lower()
    except Exception:
        body = resp.text.lower()
    return any(m in body for m in _OOM_MARKERS)


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
            fallback = _pick_general_model(available)
            logger.warning(
                "ai_brain: model '%s' not found → '%s' (best available). Available: %s",
                preferred, fallback, available,
            )
            return fallback
    except Exception as exc:
        logger.debug("ai_brain: model detection failed (%s) — keeping '%s'", exc, preferred)
        return preferred


_CLOUD_DEFAULT_MODELS = {
    "openai":    "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-20241022",
}


async def _ollama_cfg() -> Dict[str, Any]:
    """
    Build runtime LLM config from DB settings with .env fallback.

    Defaults to local Ollama (free, private, fully offline). Routes to a
    cloud provider instead only when the user has explicitly set
    llm_provider + a matching API key in Settings — see _call_llm_raw().
    """
    stored   = await db.get_all_settings()
    base_url = stored.get("ollama_base_url") or _env.ollama_base_url
    timeout  = int(stored.get("ollama_timeout") or _env.ollama_timeout)

    provider = (stored.get("llm_provider") or "ollama").lower()
    if provider in ("openai", "anthropic"):
        api_key = stored.get(f"{provider}_api_key")
        if api_key:
            model = stored.get(f"{provider}_model") or _CLOUD_DEFAULT_MODELS[provider]
            return {
                "provider": provider,
                "api_key":  api_key,
                "model":    model,
                "base_url": base_url,  # unused by cloud providers; kept so callers that log cfg['base_url'] don't break
                "timeout":  timeout,
            }
        logger.warning(
            "ai_brain: llm_provider=%s but no %s_api_key set — falling back to Ollama",
            provider, provider,
        )

    model = stored.get("ollama_model") or _env.ollama_model
    model = await _detect_available_model(base_url, model)
    return {"provider": "ollama", "base_url": base_url, "model": model, "timeout": timeout}


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


# ── Content quality gate ────────────────────────────────────────────────────────
#
# Root cause this defends against: a weak/small model can produce syntactically
# valid JSON that is still bad *content* — unfilled bracket placeholders, raw
# HTML/markup, hashtag spam, or near-identical "follow-up" messages that violate
# the prompt's own instructions. _extract_json only checks structure. This checks
# substance, and treats a violation the same way as a JSON-parse failure: retry
# with a repair prompt that names the specific problem.

_BRACKET_PLACEHOLDER_RE = re.compile(r"\[[A-Za-z][^\[\]\n]{1,60}\]")
_HTML_TAG_RE            = re.compile(r"<[a-zA-Z/][^<>]{0,120}>")
_HASHTAG_RE             = re.compile(r"#\w+")
_EMOJI_RE               = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF✀-➿]"
)
_MAX_EMOJI_PER_MESSAGE  = 1
_SIMILARITY_THRESHOLD   = 0.75


def _validate_message_content(text: str) -> List[str]:
    """Return a list of content-quality violation descriptions for one message field."""
    if not isinstance(text, str) or not text.strip():
        return []
    violations: List[str] = []
    if _BRACKET_PLACEHOLDER_RE.search(text):
        violations.append("contains an unfilled bracket placeholder like [Name] or [Link] — never use these, omit the detail instead")
    if _HTML_TAG_RE.search(text):
        violations.append("contains raw HTML/markup tags — plain text only")
    if _HASHTAG_RE.search(text):
        violations.append("contains hashtags — never use hashtags in direct outreach")
    emoji_count = len(_EMOJI_RE.findall(text))
    if emoji_count > _MAX_EMOJI_PER_MESSAGE:
        violations.append(f"contains {emoji_count} emoji (max {_MAX_EMOJI_PER_MESSAGE}) — keep it professional, not spammy")
    return violations


def _messages_too_similar(a: str, b: str) -> bool:
    """Cheap token-overlap similarity — catches near-duplicate follow-ups without a new dependency."""
    a_tokens = set(re.findall(r"\w+", a.lower()))
    b_tokens = set(re.findall(r"\w+", b.lower()))
    if not a_tokens or not b_tokens:
        return False
    overlap = len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))
    return overlap >= _SIMILARITY_THRESHOLD


def _validate_message_dict(
    parsed:           Dict[str, str],
    similarity_groups: Optional[List[List[str]]] = None,
) -> List[str]:
    """Validate every field in a generated message dict, plus cross-field near-duplicate checks."""
    violations: List[str] = []
    for key, value in parsed.items():
        for v in _validate_message_content(value):
            violations.append(f"{key}: {v}")

    for group in (similarity_groups or []):
        present = [(k, parsed[k]) for k in group if parsed.get(k)]
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                key_i, text_i = present[i]
                key_j, text_j = present[j]
                if _messages_too_similar(text_i, text_j):
                    violations.append(f"{key_i} and {key_j} are near-duplicate messages — each must use a different angle")

    return violations


def _build_content_repair_prompt(bad_output: str, violations: List[str], attempt: int) -> str:
    violations_text = "\n".join(f"- {v}" for v in violations)
    return f"""Your previous output (attempt {attempt}) was valid JSON but violated these content rules:
{violations_text}

Your previous output was:
---
{bad_output[:1500]}
---

Rewrite it completely, fixing every violation above. Same JSON keys as before.
Do not use bracket placeholders, HTML tags, or hashtags. Keep emoji to at most one per message.
Every message must be genuinely different in angle from the others — not a reworded repeat.

Return ONLY the corrected JSON. No text before or after. Start with {{."""


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

    domain_ctx = outreach_domain.build_domain_context(niche)

    return f"""You are an AI sales agent working for PopupGenix.
Generate 4 personalized WhatsApp outreach messages for this lead.
ALWAYS return a real message. NEVER leave any field empty or use placeholder text.

COMPANY CONTEXT:
{company_dna.strip()[:400]}

{domain_ctx}

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
- Pick exactly 1 pain point from the domain expertise above (or from the lead's own
  website analysis/gaps if provided) — pick ONE, do not list several
- Offer 1 clear solution we provide, matched to that pain point
- End with a soft CTA (question or invitation, not a hard sell)
- Each follow-up MUST use a different angle or value point from the others
- Do NOT repeat the same line, sentence, or angle across messages — each of the 4
  messages must be substantively different, not a reworded copy of another
- Do NOT say "I hope this finds you well" or "I wanted to reach out"

ABSOLUTE BANS (any violation makes the message unusable — do not do these):
- NEVER use a bracket placeholder like [Name], [Link], [Website], [Insert X] — if you
  don't have a real specific detail, write around it in plain language instead
- NEVER invent a specific fact, platform reference, case study, testimonial, or
  statistic that was not given to you above — if you don't have enough real detail
  to be specific, write a shorter, more genuinely curious message instead of making
  something up. A vague-but-honest message beats a specific-but-fabricated one.
- NEVER use HTML tags, markdown links, or raw URLs formatted as a link
- NEVER use hashtags
- NEVER use more than 1 emoji per message, and only if it fits a warm, professional tone
- NEVER use informal @handles (e.g. @businessname) unless one was explicitly given above
- NEVER use hype words like "revolutionize," "unlock," "game-changing," or excessive
  exclamation points — write like a credible person, not an ad

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
    domain_ctx = outreach_domain.build_domain_context(niche)

    return f"""You are a cold email copywriter for PopupGenix.
{company_dna.strip()[:300]}

{domain_ctx}

Write a cold email for this lead:
Business: {biz}
Industry: {niche}
Location: {city}

RULES: professional tone, references niche and city, plain text, 120-180 word body, soft CTA.
Pick exactly 1 pain point and 1 value angle from the domain expertise above — do not list
several, and do not invent a specific fact, case study, or statistic not given to you.
Subject: 40-55 characters, personalized, curiosity-driven, no clickbait/hype words.

ABSOLUTE BANS: no bracket placeholders like [Name] or [Link], no HTML tags, no markdown
links, no hashtags, at most 1 emoji, no hype words ("revolutionize," "unlock," "game-changing").

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

    domain_ctx = outreach_domain.build_domain_context(niche)

    return f"""You are a sales expert writing outreach messages. Use the research below to write \
HIGHLY PERSONALIZED messages. Do not be generic. Reference specific things about their business.

ABOUT US (the sender):
{company_dna.strip()[:400]}

{domain_ctx}

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
- Follow-up Day 3: Different angle from the cold email, reference it briefly, 3 sentences max.
- Follow-up Day 7: Final touch, different angle again from Day 3, create mild urgency, 2 sentences only.
- The two follow-ups MUST differ from each other and from the cold email — not a reworded repeat.
- Use the LEAD RESEARCH above first; if a field says "not specified"/"not analyzed", fall back
  to ONE pain point/value angle from the domain expertise above — never invent a specific fact,
  platform reference, case study, or statistic that isn't given to you anywhere in this prompt.
- NEVER start with "I hope this email finds you well"
- NEVER be generic — if you cannot be specific, write shorter and more genuinely curious instead
  of inventing detail to sound personalized
- Plain text only — no asterisks, no markdown, no bullet points in message bodies

ABSOLUTE BANS: no bracket placeholders like [Name] or [Link], no HTML tags, no markdown
links, no hashtags, at most 1 emoji per message, no hype words ("revolutionize," "unlock,"
"game-changing," excessive exclamation points).

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


def _build_reply_prompt(lead: Dict[str, Any], reply_text: str, company_dna: str) -> str:
    """
    Auto-reply draft: a warm, direct response to a lead who already replied
    positively — different register from cold outreach (§_build_master_prompt).
    They've engaged; the job now is to answer naturally and move to a concrete
    next step, not to re-pitch from scratch.
    """
    biz   = lead.get("business_name") or "the business"
    niche = lead.get("niche")         or "their industry"
    domain_ctx = outreach_domain.build_domain_context(niche)

    return f"""You are replying, as a real person from PopupGenix, to a lead who already
responded positively to an earlier outreach message. This is NOT cold outreach — they
engaged. Write a warm, direct, human reply that responds to what they actually said and
proposes one concrete next step (e.g. a quick call, or a specific clarifying question).

COMPANY CONTEXT:
{company_dna.strip()[:400]}

{domain_ctx}

LEAD: {biz} ({niche})

WHAT THEY REPLIED:
{reply_text.strip()[:1200]}

RULES:
- 2-5 sentences. Plain text. Sound like a real person replying to an email, not a script.
- Directly acknowledge or respond to something specific they said — do not ignore their
  message and repeat the original pitch.
- End with ONE clear, concrete next step (propose a call/time, or ask one specific question
  that moves the conversation forward) — not a vague "let me know if interested."
- Never fabricate a fact, case study, statistic, or platform reference not given to you above.

ABSOLUTE BANS: no bracket placeholders like [Name] or [Link], no HTML tags, no markdown
links, no hashtags, at most 1 emoji, no hype words ("revolutionize," "unlock," "game-changing").

OUTPUT — ONLY this JSON, start with {{ end with }}:
{{
  "reply_body": "the reply message here"
}}"""


def _fallback_reply(reason: str = "") -> Dict[str, str]:
    logger.warning("ai_brain: reply-draft fallback used — %s", reason or "no model response")
    return {
        "reply_body": (
            "Thanks so much for getting back to me! I'd love to learn a bit more about "
            "what you're looking for — would you have 10-15 minutes this week for a quick call?"
        ),
    }


def _build_individual_prompt(lead: Dict[str, Any], message_type: str, dna: str) -> str:
    biz   = lead.get("business_name", "the business")
    niche = lead.get("niche", "general")
    city  = lead.get("city", "")
    web   = lead.get("website", "")
    domain_ctx = outreach_domain.build_domain_context(niche)

    context = (
        f"You are an outreach copywriter for PopupGenix.\n"
        f"Company: {dna[:300]}\n"
        f"{domain_ctx}\n"
        f"Target: {biz}, {niche} business{' in ' + city if city else ''}. "
        f"Website: {web or 'none'}.\n"
        f"Write ONLY the message — no preamble, no commentary, plain text, no asterisks.\n"
        f"Pick exactly 1 pain point/value angle from the domain expertise above — never invent "
        f"a specific fact, case study, or statistic not given to you.\n"
        f"Bans: no bracket placeholders like [Name], no HTML tags, no hashtags, at most 1 emoji, "
        f"no hype words (\"revolutionize,\" \"unlock,\" \"game-changing\")."
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


# ── Ollama concurrency gate ────────────────────────────────────────────────────
# A local Ollama serves one generation at a time on small GPUs; firing several
# requests at once (enrichment runs 3 leads concurrently, the job queue has 4
# workers) just queues them inside Ollama, where the client timeout keeps
# ticking — queued calls time out and Ollama still burns GPU time on the
# abandoned work. Gate here so the timeout covers only the actual generation.
# OLLAMA_MAX_PARALLEL (env / Settings) raises the limit on bigger hardware.
_ollama_gates: Dict[int, asyncio.Semaphore] = {}   # one per event loop (tests run many loops)


def ollama_slot() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    gate = _ollama_gates.get(loop_id)
    if gate is None:
        try:
            limit = max(1, int(getattr(get_settings(), "ollama_max_parallel", 1)))
        except Exception:
            limit = 1
        if len(_ollama_gates) > 32:          # drop gates of finished loops
            _ollama_gates.clear()
        gate = _ollama_gates[loop_id] = asyncio.Semaphore(limit)
    return gate


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

    global _cpu_fallback_until
    options: Dict[str, Any] = {"temperature": temp, "top_p": 0.92, "num_predict": num_predict}
    if time.monotonic() < _cpu_fallback_until:
        options["num_gpu"] = 0

    async with ollama_slot(), httpx.AsyncClient(timeout=cfg["timeout"]) as client:
        payload = {"model": model, "prompt": prompt, "stream": False, "think": False, "options": options}
        r = await client.post(f"{cfg['base_url']}/api/generate", json=payload)

        if "num_gpu" not in options and _is_gpu_oom(r):
            logger.warning("ai_brain: GPU out of memory — retrying on CPU for the next %ds", _CPU_FALLBACK_SECONDS)
            _cpu_fallback_until = time.monotonic() + _CPU_FALLBACK_SECONDS
            options["num_gpu"] = 0
            r = await client.post(f"{cfg['base_url']}/api/generate", json=payload)

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


# ── Optional cloud LLM call layer ───────────────────────────────────────────────
# Only reached when the user configures llm_provider + an API key in Settings.
# Ollama stays the default so the app works fully offline out of the box.

async def _call_openai_raw(
    prompt:      str,
    cfg:         Dict[str, Any],
    temperature: Optional[float] = None,
    num_predict: int = 800,
) -> str:
    temp = temperature if temperature is not None else round(random.uniform(*_TEMPERATURE_RANGE), 2)
    async with httpx.AsyncClient(timeout=cfg["timeout"]) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json={
                "model":       cfg["model"],
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": temp,
                "max_tokens":  num_predict,
            },
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"]
        return _strip_thinking(_fix_mojibake(raw))


async def _call_anthropic_raw(
    prompt:      str,
    cfg:         Dict[str, Any],
    temperature: Optional[float] = None,
    num_predict: int = 800,
) -> str:
    temp = temperature if temperature is not None else round(random.uniform(*_TEMPERATURE_RANGE), 2)
    async with httpx.AsyncClient(timeout=cfg["timeout"]) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         cfg["api_key"],
                "anthropic-version": "2023-06-01",
            },
            json={
                "model":       cfg["model"],
                "max_tokens":  num_predict,
                "temperature": temp,
                "messages":    [{"role": "user", "content": prompt}],
            },
        )
        r.raise_for_status()
        raw = "".join(block.get("text", "") for block in r.json().get("content", []))
        return _strip_thinking(_fix_mojibake(raw))


async def _call_llm_raw(
    prompt:      str,
    cfg:         Dict[str, Any],
    temperature: Optional[float] = None,
    num_predict: int = 800,
) -> str:
    """Dispatches to whichever provider `cfg` (from _ollama_cfg()) resolved to."""
    provider = cfg.get("provider", "ollama")
    if provider == "openai":
        return await _call_openai_raw(prompt, cfg, temperature, num_predict)
    if provider == "anthropic":
        return await _call_anthropic_raw(prompt, cfg, temperature, num_predict)
    return await _call_ollama_raw(prompt, cfg, temperature, num_predict)


# ── Generic JSON generation loop ───────────────────────────────────────────────

async def _generate_phase(
    prompt_fn,
    required:    frozenset,
    aliases:     Dict[str, List[str]],
    fallback_fn,
    cfg:         Dict[str, Any],
    num_predict: int,
    label:       str,
    similarity_groups: Optional[List[List[str]]] = None,
) -> Dict[str, str]:
    """
    Retry-with-repair loop for one JSON generation phase.
    Auto-heals Ollama 404 (wrong model name) on first attempt.
    Also gates on content quality (see "Content quality gate" above) — a
    structurally-valid JSON response with a bracket placeholder, raw HTML, a
    hashtag, emoji spam, or near-duplicate follow-ups is treated as a failure
    and retried with a targeted repair prompt, same as a JSON parse failure.
    """
    prompt          = prompt_fn()
    last_output      = ""
    last_violations: List[str] = []

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if attempt == 1:
                active_prompt = prompt
            elif last_violations:
                active_prompt = _build_content_repair_prompt(last_output, last_violations, attempt)
            else:
                active_prompt = _build_repair_prompt(last_output, required, attempt)
            temp = round(random.uniform(*_TEMPERATURE_RANGE), 2) if attempt == 1 else 0.25

            raw         = await _call_llm_raw(active_prompt, cfg, temperature=temp, num_predict=num_predict)
            last_output = raw
            parsed      = _extract_json(raw, required, aliases)

            if parsed:
                violations = _validate_message_dict(parsed, similarity_groups)
                if not violations:
                    if attempt > 1:
                        logger.info("ai_brain [%s]: clean output recovered on attempt %d", label, attempt)
                    return parsed

                last_violations = violations
                logger.warning(
                    "ai_brain [%s]: attempt %d content violations: %s",
                    label, attempt, "; ".join(violations),
                )
                continue

            last_violations = []
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
    if last_violations:
        return fallback_fn("content quality failed after all retries: " + "; ".join(last_violations))
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
        similarity_groups = [["followup_day3_body", "followup_day7_body"]],
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
        similarity_groups = [["first_message", "follow_up_1", "follow_up_2", "follow_up_3"]],
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
        similarity_groups = [["first_message", "follow_up_1", "follow_up_2", "follow_up_3"]],
    )


async def test_generate(business_info: str) -> Dict[str, str]:
    """Generate all messages from free-form business description text."""
    business_info = (business_info or "").strip()
    if not business_info:
        return _fallback_messages("empty business_info")
    fake_lead = {"business_name": business_info, "niche": "", "city": "", "website": ""}
    dna       = _load_company_dna()
    return await generate_messages(fake_lead, dna)


async def generate_reply_draft(lead: Dict[str, Any], reply_text: str) -> str:
    """
    Draft a reply to a lead who already responded positively. Goes through the
    same content-quality gate as cold outreach (bracket placeholders, HTML,
    hashtags, emoji spam, fabricated specifics) — a reply to someone who
    already engaged is a higher-trust moment than cold outreach, not a lower one.

    Returns the reply body text. Never raises — falls back to a safe generic
    reply on any failure, matching the never-raises contract the rest of this
    module's generation functions follow.
    """
    dna = _load_company_dna()
    cfg = await _ollama_cfg()
    result = await _generate_phase(
        prompt_fn   = lambda: _build_reply_prompt(lead, reply_text, dna),
        required    = _REPLY_KEYS,
        aliases     = _REPLY_ALIASES,
        fallback_fn = _fallback_reply,
        cfg         = cfg,
        num_predict = 300,
        label       = "reply_draft",
    )
    return result["reply_body"]


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
            similarity_groups = [["followup_day3_body", "followup_day7_body"]],
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
            text       = await _call_llm_raw(prompt, cfg, temperature=0.72, num_predict=400)
            violations = _validate_message_content(text)
            if not violations:
                return text
            logger.warning(
                "generate_message: attempt %d content violations for %s: %s",
                attempt, message_type, "; ".join(violations),
            )
            if attempt == MAX_RETRIES:
                return text  # best available — caller sees imperfect but not-empty output
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404 and cfg.get("provider", "ollama") == "ollama":
                healed = await _detect_available_model(cfg["base_url"], cfg["model"])
                if healed != cfg["model"]:
                    cfg = {**cfg, "model": healed}
                    continue
            if attempt == MAX_RETRIES:
                raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"LLM provider unreachable after {MAX_RETRIES} attempts: {exc}") from exc
            await asyncio.sleep(1.5 * attempt)
        except Exception as exc:
            logger.error("generate_message attempt %d failed: %s", attempt, exc)
            if attempt == MAX_RETRIES:
                raise

    raise RuntimeError("generate_message: exhausted retries")


# ── Status check ───────────────────────────────────────────────────────────────

async def get_ollama_status() -> Dict[str, Any]:
    """Ping Ollama — returns connected state, active model, and available models.
    When a cloud provider is configured (Settings → LLM provider), reports that
    provider as connected without pinging Ollama, since Ollama isn't in use."""
    cfg = await _ollama_cfg()

    if cfg.get("provider") in ("openai", "anthropic"):
        return {
            "connected":        True,
            "model":            cfg["model"],
            "provider":         cfg["provider"],
            "available_models": [cfg["model"]],
        }

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

        return {"connected": True, "model": cfg["model"], "provider": "ollama", "available_models": models}

    except Exception as exc:
        return {
            "connected":        False,
            "model":            cfg["model"],
            "provider":         "ollama",
            "available_models": [],
            "error":            str(exc),
        }
