"""
ai_brain.py — Single-prompt JSON generation with automatic retry and repair.

Public API (all async):
  generate_messages(lead, company_dna)    → {whatsapp_message, email_subject, email_body, followup_message}
  test_generate(business_info)            → {whatsapp_message, email_subject, email_body, followup_message}
  generate_all_messages(lead)             → legacy keys {whatsapp, email_subject, email_body, followup}
  generate_messages_from_text(text)       → legacy keys
  generate_message(lead, message_type)    → str
  get_ollama_status()                     → {connected, model, available_models}
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
    """Remove <think>...</think> reasoning blocks produced by Qwen3 and similar models."""
    return _THINK_BLOCK_RE.sub("", text).strip()

# ── JSON schema ────────────────────────────────────────────────────────────────

_REQUIRED_KEYS = frozenset({"whatsapp_message", "email_subject", "email_body", "followup_message"})

_KEY_ALIASES: Dict[str, List[str]] = {
    "whatsapp_message": ["whatsapp", "whatsapp_msg", "wa_message", "wa_msg", "text_message", "sms"],
    "email_subject":    ["subject", "email_subject_line", "subject_line", "email_title", "title"],
    "email_body":       ["email", "email_content", "email_text", "body", "email_message", "content"],
    "followup_message": ["followup", "follow_up", "followup_msg", "follow_up_message", "follow_up_msg"],
}

# ── Config helpers ─────────────────────────────────────────────────────────────


async def _ollama_cfg() -> Dict[str, Any]:
    """Merge DB settings (live, per-request) with .env fallback."""
    stored = await db.get_all_settings()
    return {
        "base_url": stored.get("ollama_base_url") or _env.ollama_base_url,
        "model":    stored.get("ollama_model")    or _env.ollama_model,
        "timeout":  int(stored.get("ollama_timeout") or _env.ollama_timeout),
    }


def _load_company_dna() -> str:
    path = Path(_env.company_dna_path)
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── JSON extraction engine ─────────────────────────────────────────────────────


def _map_keys_flexible(data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Map LLM response keys to canonical schema — handles common variations."""
    result: Dict[str, str] = {}
    for canonical, aliases in _KEY_ALIASES.items():
        if canonical in data:
            result[canonical] = str(data[canonical]).strip()
        else:
            for alias in aliases:
                if alias in data:
                    result[canonical] = str(data[alias]).strip()
                    break
    return result if len(result) == 4 else None


def _extract_json(text: str) -> Optional[Dict[str, str]]:
    """
    Try multiple extraction strategies on raw LLM output.
    Returns canonical 4-key dict or None if all strategies fail.
    """
    candidates: List[str] = [text.strip()]

    # Extract from ```json ... ``` or ``` ... ``` fences
    for pattern in [
        r"```json\s*([\s\S]+?)\s*```",
        r"```\s*([\s\S]+?)\s*```",
    ]:
        for m in re.finditer(pattern, text, re.DOTALL):
            candidates.append(m.group(1).strip())

    # All { ... } spans in the text
    for m in re.finditer(r"\{[\s\S]+?\}", text, re.DOTALL):
        candidates.append(m.group(0))

    # Repair common issues: trailing commas, single-quoted strings
    repaired = re.sub(r",\s*([}\]])", r"\1", text).replace("\t", "  ")
    candidates.append(repaired.strip())

    seen: set = set()
    for candidate in candidates:
        dedup_key = candidate[:200]
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        try:
            data = json.loads(candidate)
            if not isinstance(data, dict):
                continue
            if _REQUIRED_KEYS.issubset(data):
                return {k: str(data[k]).strip() for k in _REQUIRED_KEYS}
            flexible = _map_keys_flexible(data)
            if flexible:
                return flexible
        except (json.JSONDecodeError, ValueError):
            continue

    return None


def _fallback_messages(reason: str = "") -> Dict[str, str]:
    note = f"[Generation failed{': ' + reason if reason else ''}. Please retry.]"
    return dict.fromkeys(_REQUIRED_KEYS, note)


# ── Prompt builders ────────────────────────────────────────────────────────────


def _build_lead_context(lead: Dict[str, Any]) -> str:
    parts = [f"- Business Name: {lead.get('business_name') or 'Unknown'}"]
    if lead.get("niche"):
        parts.append(f"- Industry/Niche: {lead['niche']}")
    if lead.get("city"):
        parts.append(f"- City: {lead['city']}")
    if lead.get("website"):
        parts.append(f"- Website: {lead['website']}")
    if lead.get("phone"):
        parts.append(f"- Phone: {lead['phone']}")
    return "\n".join(parts)


def _build_json_prompt(lead_context: str, company_dna: str) -> str:
    opening_style = random.choice(_OPENING_STYLES)
    return f"""You are an outreach copywriter. Generate 4 personalized marketing messages as JSON.

COMPANY:
{company_dna.strip()[:600]}

TARGET:
{lead_context}

STYLE: {opening_style}

OUTPUT — return ONLY this JSON, start with {{ end with }}:
{{
  "whatsapp_message": "casual warm text, under 100 words, plain text, specific CTA",
  "email_subject": "40-55 chars, personalized, curiosity-driven",
  "email_body": "120-200 words, professional, references their niche and city, soft CTA, plain text",
  "followup_message": "under 60 words, fresh angle, friendly, plain text"
}}

Rules: plain text only, no markdown, no asterisks, no "I hope this finds you well", no "I wanted to reach out".
Output ONLY the JSON. Start with {{."""


def _build_repair_prompt(bad_output: str, attempt: int) -> str:
    return f"""Your previous output (attempt {attempt}) was not valid JSON or was missing required keys.

Your output was:
---
{bad_output[:2000]}
---

Return ONLY this exact JSON structure with all four keys filled in.
Start with {{ and end with }}. No other text.

{{
  "whatsapp_message": "<casual WhatsApp outreach, plain text, under 160 words>",
  "email_subject": "<subject line, 40-60 characters>",
  "email_body": "<professional email, 150-250 words, plain text>",
  "followup_message": "<friendly follow-up, under 100 words, plain text>"
}}

NO markdown. NO code fences. NO text before or after. Start with {{."""


def _build_individual_prompt(lead: Dict[str, Any], message_type: str, dna: str) -> str:
    """Individual message prompt — used by generate_message() for single-type efficiency."""
    business = lead.get("business_name", "the business")
    niche    = lead.get("niche", "general")
    city     = lead.get("city", "")
    website  = lead.get("website", "")

    context = f"""You are an outreach copywriter.
Company: {dna[:300]}
Target: {business}, {niche} business in {city}. Website: {website or 'none'}.
Write ONLY the message — no preamble, no commentary, plain text, no asterisks."""

    instructions = {
        "whatsapp":      "Write a casual WhatsApp message (under 100 words). Specific to their niche. Clear CTA.",
        "email_subject": "Write ONLY a subject line (40-55 characters). Personalized, curiosity-driven.",
        "email_body":    "Write a cold email body (120-180 words). Professional, references niche and city. Soft CTA.",
        "followup":      "Write a follow-up message (under 60 words). Fresh angle, friendly, not desperate.",
    }

    if message_type not in instructions:
        raise ValueError(f"Unknown message_type: {message_type!r}. Valid: {sorted(instructions)}")

    return f"{context}\n\n{instructions[message_type]}"


# ── Ollama call layer ──────────────────────────────────────────────────────────


async def _call_ollama_raw(
    prompt: str,
    cfg: Dict[str, Any],
    temperature: Optional[float] = None,
    num_predict: int = 700,
) -> str:
    """
    Single LLM call. Prefers ollama Python library; falls back to httpx REST API.
    Always returns a stripped string.
    """
    temp    = temperature if temperature is not None else round(random.uniform(*_TEMPERATURE_RANGE), 2)
    model   = cfg["model"]
    timeout = cfg["timeout"]
    options = {"temperature": temp, "top_p": 0.92, "num_predict": num_predict}

    # Always use httpx REST API — the Python library doesn't reliably support
    # the `think: false` parameter needed to suppress Qwen3 reasoning tokens.
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{cfg['base_url']}/api/generate",
            json={
                "model":   model,
                "prompt":  prompt,
                "stream":  False,
                "think":   False,   # Ollama ≥0.6 suppresses <think> blocks
                "options": options,
            },
        )
        r.raise_for_status()
        raw = r.json().get("response", "")
        return _strip_thinking(raw)


# ── Core JSON generation engine ────────────────────────────────────────────────


async def _generate_json_messages(
    lead_context: str,
    company_dna: str,
    cfg: Dict[str, Any],
) -> Dict[str, str]:
    """
    Issue ONE Ollama call requesting all 4 messages as JSON.
    On malformed JSON, sends a targeted repair prompt and retries up to MAX_RETRIES times.
    """
    prompt      = _build_json_prompt(lead_context, company_dna)
    last_output = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if attempt == 1:
                active_prompt = prompt
                temp          = round(random.uniform(*_TEMPERATURE_RANGE), 2)
            else:
                logger.warning("ai_brain: attempt %d — sending JSON repair prompt", attempt)
                active_prompt = _build_repair_prompt(last_output, attempt)
                temp          = 0.25  # low temp for repair: we need precise JSON structure

            raw         = await _call_ollama_raw(active_prompt, cfg, temperature=temp)
            last_output = raw
            parsed      = _extract_json(raw)

            if parsed:
                if attempt > 1:
                    logger.info("ai_brain: JSON recovered on attempt %d", attempt)
                return parsed

            logger.warning(
                "ai_brain: attempt %d produced unparseable output (%d chars). Snippet: %s...",
                attempt, len(raw), raw[:120].replace("\n", " "),
            )

        except httpx.HTTPStatusError as exc:
            logger.error("ai_brain: HTTP %s on attempt %d: %s", exc.response.status_code, attempt, exc)
            if attempt == MAX_RETRIES:
                return _fallback_messages(f"HTTP {exc.response.status_code}")

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.error("ai_brain: connection/timeout on attempt %d: %s", attempt, exc)
            if attempt == MAX_RETRIES:
                return _fallback_messages("Ollama unreachable — is it running?")

        except Exception as exc:
            logger.error("ai_brain: unexpected error on attempt %d: %s", attempt, exc, exc_info=True)
            if attempt == MAX_RETRIES:
                return _fallback_messages(f"{type(exc).__name__}: {exc}")

    logger.error(
        "ai_brain: all %d attempts failed. Last output snippet:\n%s",
        MAX_RETRIES, last_output[:400],
    )
    return _fallback_messages("JSON parse failed after all retries")


# ══ PUBLIC API ══════════════════════════════════════════════════════════════════


async def generate_messages(lead: Dict[str, Any], company_dna: str) -> Dict[str, str]:
    """
    Generate all 4 outreach message types in a single Ollama call.

    Args:
        lead:        dict with keys: business_name, niche, city, website, phone
        company_dna: raw company DNA string (read from file or provided by caller)

    Returns:
        dict with keys: whatsapp_message, email_subject, email_body, followup_message
    """
    cfg          = await _ollama_cfg()
    lead_context = _build_lead_context(lead)
    return await _generate_json_messages(lead_context, company_dna, cfg)


async def test_generate(business_info: str) -> Dict[str, str]:
    """
    Generate all 4 outreach messages from a free-form business description.

    Args:
        business_info: any free-form text describing the target business

    Returns:
        dict with keys: whatsapp_message, email_subject, email_body, followup_message
    """
    business_info = (business_info or "").strip()
    if not business_info:
        return _fallback_messages("empty business_info")

    dna = _load_company_dna()
    cfg = await _ollama_cfg()
    return await _generate_json_messages(business_info, dna, cfg)


# ── Legacy API — backward-compatible with routers/ai.py ───────────────────────


async def generate_all_messages(lead: Dict[str, Any]) -> Dict[str, str]:
    """
    Generate all 4 message types via the single-prompt JSON engine.
    Returns legacy key names: whatsapp, email_subject, email_body, followup.
    """
    dna    = _load_company_dna()
    result = await generate_messages(lead, dna)
    return {
        "whatsapp":      result["whatsapp_message"],
        "email_subject": result["email_subject"],
        "email_body":    result["email_body"],
        "followup":      result["followup_message"],
    }


async def generate_messages_from_text(business_text: str) -> Dict[str, str]:
    """Generate all 4 message types from raw text; returns legacy key names."""
    result = await test_generate(business_text)
    return {
        "whatsapp":      result["whatsapp_message"],
        "email_subject": result["email_subject"],
        "email_body":    result["email_body"],
        "followup":      result["followup_message"],
    }


async def generate_message(lead: Dict[str, Any], message_type: str) -> str:
    """
    Generate a single message type via an individual focused prompt.
    More efficient than calling generate_all_messages when only one type is needed.

    Args:
        lead:         lead dict
        message_type: one of "whatsapp", "email_subject", "email_body", "followup"

    Returns:
        str — the generated message text
    """
    _VALID = {"whatsapp", "email_subject", "email_body", "followup"}
    if message_type not in _VALID:
        raise ValueError(f"Unknown message_type: {message_type!r}. Valid: {sorted(_VALID)}")

    dna    = _load_company_dna()
    cfg    = await _ollama_cfg()
    prompt = _build_individual_prompt(lead, message_type, dna)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await _call_ollama_raw(prompt, cfg, temperature=0.72, num_predict=400)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"Ollama unreachable after {MAX_RETRIES} attempts: {exc}") from exc
            await asyncio.sleep(1.5 * attempt)
        except Exception as exc:
            logger.error("generate_message attempt %d failed: %s", attempt, exc)
            if attempt == MAX_RETRIES:
                raise

    raise RuntimeError("generate_message: exhausted retries")  # unreachable but satisfies type checkers


# ── Status check ───────────────────────────────────────────────────────────────


async def get_ollama_status() -> Dict[str, Any]:
    """Ping Ollama — returns connected state, active model, and available models."""
    cfg = await _ollama_cfg()
    try:
        if _HAS_OLLAMA_LIB:
            client = _ollama_pkg.AsyncClient(host=cfg["base_url"], timeout=5)
            tags   = await client.list()
            if hasattr(tags, "models"):
                models: List[str] = [getattr(m, "model", str(m)) for m in tags.models]
            else:
                models = []
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
