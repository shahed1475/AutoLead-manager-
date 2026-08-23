"""
reply_intelligence_agent.py — Reply Intelligence Agent (Phase 4).

Runs ALONGSIDE reply_detector.py's existing 5-category classifier, not
instead of it — that classifier keeps driving the existing REPLIED/SKIPPED
lead-status transition and get_reply_summary()'s counts unchanged. This
agent adds a richer 12-category taxonomy + confidence + a deterministic
recommended action + (for judgment-requiring intents) a grounded reply
draft, staged through the *existing* draft-and-approve pipeline
(database.set_reply_draft / routers/replies.py) — nothing new is built
for approval, it's reused as-is.

Two guarantees are structural, not LLM-hoped-for, mirroring every other
agent in this codebase:

1. Opt-out is detected by a deterministic phrase scan BEFORE any LLM call.
   If it matches, the result is OPT_OUT with confidence 1.0 and the LLM is
   never even invoked — this is the one classification where "usually
   right" isn't an acceptable bar, since it gates whether AutoLead ever
   contacts this person again.
2. recommended_action is read from a fixed intent -> action lookup table,
   never the model's own words — orchestration code (reply_detector.py,
   followup_engine.py) branches on a small fixed action-code enum.

draft_response is grounded in the same pain-point/solution facts
marketing_agent.py uses (via `original_message`, typically a
generated_messages row) and passes through the same content-quality gate
(ai_brain._validate_message_content) plus marketing_agent's forbidden-claim
scan (imported, not duplicated) — never a fabricated price, date, or
guarantee. Heuristic fallback (deterministic keyword rules) is always
computed first for classification and used whenever the LLM is
unavailable, empty, or unparseable.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..ai_brain import _call_llm_raw, _ollama_cfg, _validate_message_content
from .base import AgentResult
from .marketing_agent import _has_unsupported_claim

logger = logging.getLogger(__name__)

# ── 12-category taxonomy ────────────────────────────────────────────────────

INTENTS = frozenset({
    "INTERESTED", "VERY_INTERESTED", "QUESTION", "PRICING", "MEETING_REQUEST",
    "DEMO_REQUEST", "NEEDS_INFORMATION", "MAYBE_LATER", "NOT_INTERESTED",
    "WRONG_CONTACT", "OPT_OUT", "OTHER",
})

# Deterministic next-best-action per intent — never LLM-invented (see module docstring).
_ACTION_BY_INTENT: Dict[str, str] = {
    "INTERESTED":         "SCHEDULE_MEETING",
    "VERY_INTERESTED":    "SCHEDULE_MEETING",
    "QUESTION":           "ANSWER_QUESTION",
    "PRICING":            "REQUEST_REQUIREMENTS",
    "MEETING_REQUEST":    "SCHEDULE_MEETING",
    "DEMO_REQUEST":       "SCHEDULE_MEETING",
    "NEEDS_INFORMATION":  "PROVIDE_INFORMATION",
    "MAYBE_LATER":        "SCHEDULE_FOLLOWUP",
    "NOT_INTERESTED":     "STOP_CAMPAIGN",
    "WRONG_CONTACT":      "STOP_CAMPAIGN",
    "OPT_OUT":            "SUPPRESS_OUTREACH",
    "OTHER":              "HUMAN_REVIEW",
}

# Only these intents get a human-reviewable draft — nothing to send for a
# closed-out or opted-out conversation.
_DRAFT_WORTHY_INTENTS = frozenset({
    "INTERESTED", "VERY_INTERESTED", "QUESTION", "PRICING",
    "MEETING_REQUEST", "DEMO_REQUEST", "NEEDS_INFORMATION",
})

# ── Deterministic opt-out gate (checked before anything else, no LLM) ──────

_OPT_OUT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\bstop\b",
        r"\bunsubscribe\b",
        r"don'?t\s+contact\s+me",
        r"\bremove\s+me\b",
        r"not\s+interested\s+in\s+(any\s+)?(further|future|more)\s+(messages|emails|contact|outreach)",
        r"\bopt[\s-]?out\b",
        r"take\s+me\s+off\s+(your|this)\s+list",
        r"stop\s+(emailing|texting|messaging|contacting)\s+me",
    )
]


def is_opt_out_phrase(text: str) -> bool:
    return any(p.search(text) for p in _OPT_OUT_PATTERNS)


# ── Heuristic fallback classifier (deterministic, always available) ────────

def _heuristic_classify(text: str) -> str:
    lowered = text.lower()

    if any(kw in lowered for kw in ("wrong person", "wrong contact", "not the right person", "no longer works here")):
        return "WRONG_CONTACT"
    if any(kw in lowered for kw in ("not interested", "no thanks", "not for us", "we're all set", "already have")):
        return "NOT_INTERESTED"
    if any(kw in lowered for kw in ("maybe later", "check back", "not right now", "reach out later", "in a few months", "circle back")):
        return "MAYBE_LATER"
    if any(kw in lowered for kw in ("how much", "price", "pricing", "cost", "quote", "budget")):
        return "PRICING"
    if any(kw in lowered for kw in ("demo", "demonstration", "show me", "see it in action")):
        return "DEMO_REQUEST"
    if any(kw in lowered for kw in ("meeting", "schedule a call", "book a call", "hop on a call", "set up a call")):
        return "MEETING_REQUEST"
    if any(kw in lowered for kw in ("send me more", "more information", "more details", "more info", "learn more")):
        return "NEEDS_INFORMATION"
    if any(kw in lowered for kw in ("definitely", "absolutely", "love this", "very interested", "extremely interested")):
        return "VERY_INTERESTED"
    if any(kw in lowered for kw in ("interested", "sounds good", "tell me more", "sounds interesting")):
        return "INTERESTED"
    if "?" in text:
        return "QUESTION"
    return "OTHER"


# ── LLM-assisted classification + draft (optional, strictly grounded) ──────

def _build_classification_prompt(
    reply_text: str, original_message: Optional[Dict[str, Any]], previous_replies: List[Dict[str, Any]],
) -> str:
    context_lines = []
    if original_message:
        if original_message.get("pain_point"):
            context_lines.append(f"Original pain point raised: {original_message['pain_point']}")
        if original_message.get("solution"):
            context_lines.append(f"Solution offered: {original_message['solution']}")
    for prev in (previous_replies or [])[:3]:
        snippet = (prev.get("reply_text") or "")[:200]
        if snippet:
            context_lines.append(f"Earlier reply from this lead: {snippet}")
    context = "\n".join(context_lines) or "No prior context available."

    categories = "\n".join(f"- {i}" for i in sorted(INTENTS))

    return f"""Classify this email reply into EXACTLY ONE of these categories:
{categories}

CONTEXT:
{context}

REPLY TO CLASSIFY:
{reply_text[:1500]}

Return ONLY this JSON, no markdown:
{{
  "intent": "ONE_OF_THE_CATEGORIES_ABOVE",
  "confidence": 0.0-1.0,
  "draft_response": "a short, human, non-pushy reply using ONLY the context facts above — no prices, no dates, no guarantees, no invented facts. Use null if this reply does not need a response (e.g. not interested, opt-out, wrong contact, maybe later)."
}}"""


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    match = re.search(r"\{[\s\S]+\}", cleaned)
    if not match:
        return None
    json_str = match.group(0)
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _classify_and_draft_llm(
    reply_text: str, original_message: Optional[Dict[str, Any]], previous_replies: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    try:
        cfg = await _ollama_cfg()
        prompt = _build_classification_prompt(reply_text, original_message, previous_replies)
        raw = await _call_llm_raw(prompt, cfg, temperature=0.2, num_predict=350)
    except Exception as exc:
        logger.debug("reply_intelligence_agent: LLM classification unavailable: %s", exc)
        return None

    parsed = _parse_json_object(raw)
    if not parsed:
        return None

    intent = str(parsed.get("intent") or "").strip().upper()
    if intent not in INTENTS:
        return None

    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.5

    draft = parsed.get("draft_response")
    draft = str(draft).strip() if draft and str(draft).strip().lower() != "null" else None
    if draft:
        if _validate_message_content(draft):
            draft = None
        elif _has_unsupported_claim(draft):
            draft = None

    return {"intent": intent, "confidence": confidence, "draft_response": draft}


def _heuristic_draft_response(original_message: Optional[Dict[str, Any]], intent: str) -> Optional[str]:
    """Minimal, safe, non-fabricating fallback draft — used only when the LLM
    path is unavailable and this intent is draft-worthy."""
    if intent not in _DRAFT_WORTHY_INTENTS:
        return None
    if original_message and original_message.get("solution"):
        return (
            f"Thanks for getting back to me. To make sure I give you accurate details about "
            f"{original_message['solution']}, could you share a bit more about what you're looking for? "
            f"Happy to set up a short call as well, whichever is easier for you."
        )
    return (
        "Thanks for getting back to me — happy to share more details or set up a short call, "
        "whichever is easier for you."
    )


# ── Agent ─────────────────────────────────────────────────────────────────────

class ReplyIntelligenceAgent:
    name = "reply_intelligence"

    async def run(
        self,
        reply_text: str,
        lead: Dict[str, Any],
        original_message: Optional[Dict[str, Any]] = None,
        previous_replies: Optional[List[Dict[str, Any]]] = None,
        campaign: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        text = (reply_text or "").strip()

        # Deterministic gate first — never depends on the LLM being right.
        if is_opt_out_phrase(text):
            return AgentResult(
                status="ok",
                data={
                    "intent": "OPT_OUT", "confidence": 1.0,
                    "recommended_action": _ACTION_BY_INTENT["OPT_OUT"],
                    "draft_response": None,
                },
                evidence=[], confidence=1.0,
            )

        if not text:
            return AgentResult(
                status="ok",
                data={"intent": "OTHER", "confidence": 0.0,
                      "recommended_action": _ACTION_BY_INTENT["OTHER"], "draft_response": None},
                evidence=[], confidence=0.0,
            )

        llm_result = await _classify_and_draft_llm(text, original_message, previous_replies or [])
        if llm_result:
            intent = llm_result["intent"]
            confidence = llm_result["confidence"]
            draft = llm_result["draft_response"] if intent in _DRAFT_WORTHY_INTENTS else None
        else:
            intent = _heuristic_classify(text)
            confidence = 0.5
            draft = _heuristic_draft_response(original_message, intent)

        return AgentResult(
            status="ok",
            data={
                "intent": intent,
                "confidence": confidence,
                "recommended_action": _ACTION_BY_INTENT.get(intent, "HUMAN_REVIEW"),
                "draft_response": draft,
            },
            evidence=[], confidence=confidence,
        )
