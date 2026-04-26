"""
ai_brain.py — PopupGenix AI outreach engine.

Generates 6 messages per lead in two parallel Ollama calls:
  Phase 1 (WhatsApp sequence):  first_message, follow_up_1, follow_up_2, follow_up_3
  Phase 2 (Email):              email_subject, email_body

Auto-heals on HTTP 404 by detecting available models from Ollama.
Retries up to MAX_RETRIES times with JSON repair prompts.
Never returns empty messages — always produces fallback text.

Public API (all async):
  generate_messages(lead, company_dna)     → full 6-key dict
  generate_followup_sequence(lead)         → {follow_up_1, follow_up_2, follow_up_3}
  generate_all_messages(lead)              → legacy 7-key dict
  generate_messages_from_text(text)        → legacy keys
  generate_message(lead, message_type)     → str
  get_ollama_status()                      → {connected, model, available_models}
"""

import asyncio
import json
import logging
import random
import re
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


def _strip_thinking(text: str) -> str:
    return _THINK_BLOCK_RE.sub("", text).strip()


def _fix_mojibake(text: str) -> str:
    """Fix Windows-1252 mojibake in Ollama responses (â€™ → ', etc.)."""
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


# ── JSON schemas ───────────────────────────────────────────────────────────────

# Phase 1: WhatsApp sequence
_WA_KEYS = frozenset({"first_message", "follow_up_1", "follow_up_2", "follow_up_3"})
_WA_ALIASES: Dict[str, List[str]] = {
    "first_message": ["whatsapp_message", "whatsapp", "message", "initial_message",
                      "outreach", "first_outreach", "wa_message"],
    "follow_up_1":   ["followup_1", "follow_up1", "fu1", "followup_message",
                      "followup", "follow_up", "follow_up_message"],
    "follow_up_2":   ["followup_2", "follow_up2", "fu2", "second_followup"],
    "follow_up_3":   ["followup_3", "follow_up3", "fu3", "final_message", "final_followup"],
}

# Phase 2: Email
_EMAIL_KEYS = frozenset({"email_subject", "email_body"})
_EMAIL_ALIASES: Dict[str, List[str]] = {
    "email_subject": ["subject", "subject_line", "email_subject_line", "email_title", "title"],
    "email_body":    ["body", "email", "email_content", "email_text", "content", "email_message"],
}

# ── Config helpers ─────────────────────────────────────────────────────────────


async def _detect_available_model(base_url: str, preferred: str) -> str:
    """
    Query Ollama /api/tags and return the best available model.
    Priority: exact match → prefix match → any available model → keep preferred.
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{base_url}/api/tags")
            if r.status_code != 200:
                return preferred
            available: List[str] = [m["name"] for m in r.json().get("models", [])]
            if not available:
                return preferred
            # Exact match
            if preferred in available:
                return preferred
            # Prefix match (e.g. "qwen2.5" matches "qwen2.5:1.5b")
            prefix = preferred.split(":")[0]
            match = next((m for m in available if m.startswith(prefix)), None)
            if match:
                logger.warning(
                    "ai_brain: model '%s' not found → using '%s' (prefix match)", preferred, match
                )
                return match
            # Use first available
            logger.warning(
                "ai_brain: model '%s' not found → using '%s' (first available). "
                "Available: %s", preferred, available[0], available
            )
            return available[0]
    except Exception as exc:
        logger.debug("ai_brain: model detection failed (%s) — keeping '%s'", exc, preferred)
        return preferred


async def _ollama_cfg() -> Dict[str, Any]:
    """
    Build runtime Ollama config from DB settings (live, per-request) with .env fallback.
    Auto-heals the model name if the stored value no longer exists in Ollama.
    """
    stored   = await db.get_all_settings()
    base_url = stored.get("ollama_base_url") or _env.ollama_base_url
    model    = stored.get("ollama_model")    or _env.ollama_model
    timeout  = int(stored.get("ollama_timeout") or _env.ollama_timeout)

    # Auto-heal: verify the configured model is actually installed
    model = await _detect_available_model(base_url, model)

    return {"base_url": base_url, "model": model, "timeout": timeout}


def _load_company_dna() -> str:
    path = Path(_env.company_dna_path)
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── JSON extraction engine ─────────────────────────────────────────────────────


def _map_keys(data: Dict[str, Any], required: frozenset, aliases: Dict[str, List[str]]) -> Optional[Dict[str, str]]:
    """Map raw LLM response keys to canonical schema via aliases."""
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


def _extract_json(text: str, required: frozenset, aliases: Dict[str, List[str]]) -> Optional[Dict[str, str]]:
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
    note = f"[Generation failed{': ' + reason if reason else ''}. Please retry.]"
    return {"first_message": note, "follow_up_1": note, "follow_up_2": note, "follow_up_3": note}


def _fallback_email(reason: str = "") -> Dict[str, str]:
    note = f"[Generation failed{': ' + reason if reason else ''}. Please retry.]"
    return {"email_subject": note, "email_body": note}


def _fallback_messages(reason: str = "") -> Dict[str, str]:
    return {**_fallback_wa(reason), **_fallback_email(reason)}


# ── Prompt builders ────────────────────────────────────────────────────────────


def _build_lead_context(lead: Dict[str, Any]) -> str:
    parts = [f"Business: {lead.get('business_name') or 'Unknown'}"]
    if lead.get("niche"):   parts.append(f"Niche: {lead['niche']}")
    if lead.get("city"):    parts.append(f"Location: {lead['city']}")
    if lead.get("website"): parts.append(f"Website: {lead['website']}")
    if lead.get("phone"):   parts.append(f"Phone: {lead['phone']}")
    if lead.get("rating"):  parts.append(f"Rating: {lead['rating']}/5")
    if lead.get("review_count"): parts.append(f"Reviews: {lead['review_count']}")
    # Enrichment context — dramatically improves personalization quality
    if lead.get("website_summary"):
        parts.append(f"Website Summary: {lead['website_summary']}")
    if lead.get("business_gaps"):
        parts.append(f"Identified Gaps: {lead['business_gaps']}")
    if lead.get("personalization_hook"):
        parts.append(f"Personalization Detail: {lead['personalization_hook']}")
    return "\n".join(parts)


def _build_master_prompt(lead: Dict[str, Any], company_dna: str) -> str:
    """
    PopupGenix master prompt: generates 4 WhatsApp-style messages.
    first_message (initial outreach) + 3 follow-ups (Day 3 / Day 10 / Day 17).
    """
    biz     = lead.get("business_name") or "the business"
    niche   = lead.get("niche")         or "their industry"
    city    = lead.get("city")          or ""
    website = lead.get("website")       or "no website listed"
    style   = random.choice(_OPENING_STYLES)

    # Build enrichment section if available
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
    """Generates cold email subject line + body."""
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
    prompt: str,
    cfg: Dict[str, Any],
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


# ── Per-phase JSON generation ──────────────────────────────────────────────────


async def _generate_phase(
    prompt_fn,           # callable that returns prompt string
    required: frozenset,
    aliases: Dict[str, List[str]],
    fallback_fn,         # callable(reason) → dict
    cfg: Dict[str, Any],
    num_predict: int,
    label: str,
) -> Dict[str, str]:
    """
    Generic retry-with-repair loop for one JSON generation phase.
    Automatically detects and heals a 404 (wrong model name) on first attempt.
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
                # Model not found — try to heal automatically
                healed = await _detect_available_model(cfg["base_url"], cfg["model"])
                if healed != cfg["model"]:
                    logger.info("ai_brain [%s]: 404 healed → switching model to '%s'", label, healed)
                    cfg = {**cfg, "model": healed}
                    continue   # retry with the healed model immediately

            if attempt == MAX_RETRIES:
                return fallback_fn(f"HTTP {sc}")

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.error("ai_brain [%s]: connection/timeout on attempt %d: %s", label, attempt, exc)
            if attempt == MAX_RETRIES:
                return fallback_fn("Ollama unreachable — is it running?")

        except Exception as exc:
            logger.error("ai_brain [%s]: unexpected error attempt %d: %s", label, attempt, exc, exc_info=True)
            if attempt == MAX_RETRIES:
                return fallback_fn(f"{type(exc).__name__}: {exc}")

    logger.error("ai_brain [%s]: all %d attempts failed. Last snippet:\n%s", label, MAX_RETRIES, last_output[:400])
    return fallback_fn("JSON parse failed after all retries")


# ══ PUBLIC API ══════════════════════════════════════════════════════════════════


async def generate_messages(lead: Dict[str, Any], company_dna: str) -> Dict[str, str]:
    """
    Generate all 6 outreach messages in two parallel Ollama calls.
    Phase 1: first_message + follow_up_1/2/3 (WhatsApp sequence)
    Phase 2: email_subject + email_body

    Returns dict with keys:
      first_message, follow_up_1, follow_up_2, follow_up_3,
      email_subject, email_body,
      whatsapp_message (alias for first_message),
      followup_message (alias for follow_up_1)
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
        # Backward-compat aliases
        "whatsapp_message":  wa_result["first_message"],
        "followup_message":  wa_result["follow_up_1"],
    }


async def generate_followup_sequence(lead: Dict[str, Any]) -> Dict[str, str]:
    """
    Generate (or regenerate) only the 3 follow-up messages for an existing lead.
    Used when a lead already has a first message but needs fresh follow-ups.
    """
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


# ── Legacy API — backward-compatible with routers/ai.py and scheduler.py ──────


async def generate_all_messages(lead: Dict[str, Any]) -> Dict[str, str]:
    """
    Generate all messages. Returns both new and legacy key names.
    New:    first_message, follow_up_1, follow_up_2, follow_up_3, email_subject, email_body
    Legacy: whatsapp, email_subject, email_body, followup
    """
    dna    = _load_company_dna()
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
