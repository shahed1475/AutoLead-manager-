---
name: autolead-ai-llm-engineering
description: Use when calling an LLM, building a new AI agent, working with structured/JSON model output, or debugging hallucinated or malformed AI output anywhere in AutoLead.
---

# AutoLead AI/LLM Engineering

## Purpose

Governs how AutoLead talks to LLMs: provider dispatch, the structured-output-with-retry pattern, and the content-quality gates that catch hallucination/bad output.

## When to Use

Any new or changed LLM call, a new AI agent, or debugging output that's malformed, hallucinated, or inconsistent.

## When NOT to Use

Deterministic logic (see `autolead-lead-intelligence-and-scoring`'s rule: prefer code over LLM for anything programmatically checkable).

## Project Context

- **One entry point for every LLM call:** `backend/ai_brain.py::_call_llm_raw(prompt, cfg, temperature, num_predict)`. Every agent/module in the codebase (`ai_enricher.py`, `company_research_agent.py`, `pain_point_agent.py`, `opportunity_agent.py`, `marketing_agent.py`, `followup_agent.py`, `reply_intelligence_agent.py`) imports this by name — never a provider SDK directly.
- **Provider dispatch:** `_ollama_cfg()` reads DB settings, defaults to local Ollama (`ollama_base_url`, auto-detects best available model via `/api/tags` — exact → prefix → first-available fallback). Routes to OpenAI/Anthropic only if the user explicitly sets `llm_provider` + API key in Settings. `_call_llm_raw` dispatches on `cfg["provider"]`.
- **Structured output pattern:** `_generate_phase(prompt_fn, required, aliases, fallback_fn, cfg, num_predict, label, similarity_groups)` is the reusable retry-with-repair loop every generation function is built on. `_extract_json` tries raw text → fenced blocks → brace-span regex → trailing-comma repair, with alias-mapping (`_map_keys`) so e.g. `"subject"` still parses when `"email_subject"` was expected.
- **When `_extract_json`/`_map_keys` is the wrong tool:** both coerce every parsed value to `str(...)`. That's fine for message-generation (all-string fields) but corrupts a typed schema — a `confidence: float`, a `params: dict`, a list field. Two subsystems deliberately parse their own JSON for this reason and route only the model *call* through `_call_llm_raw`: `discovery/planner.py::_parse_planner_json` and `research_agent/llm.py::_parse_json_object`. Both keep the same multi-strategy resilience (raw → fenced → brace-span) minus the type coercion. If a new structured call has non-string fields, follow that precedent — don't force it through `_map_keys`.
- **Content-quality gate (code-enforced, not just prompted):** `_validate_message_content(text)` flags unfilled placeholders (`[Name]`), raw HTML tags, hashtags, >1 emoji. `_messages_too_similar(a, b)` (token-overlap ≥0.75) catches near-duplicate follow-ups. Violations trigger a retry with `_build_content_repair_prompt` naming the specific issue — treated exactly like a JSON-parse failure, not silently accepted.
- **Fact-invention prevention:** mostly prompt-engineering ("ABSOLUTE BANS" on inventing specifics) at the general `ai_brain.py` level, but the Phase-4 agents go further — they build factual content deterministically and only ask the LLM to rephrase/fill narrow fragments, with a forbidden-claim regex (`_FORBIDDEN_CLAIM_RE`: `$`, `%`, "guarantee", "ROI") and an other-service-name scan as defense in depth.
- **Never raises:** every public generation function falls back to a hardcoded generic message (`_fallback_wa`, `_fallback_email`, `_fallback_v2`, `_fallback_reply`) after `MAX_RETRIES=3`. The same "never raises, always degrades" contract holds in the newer subsystems: `discovery/planner.py` degrades to a general-search plan on any LLM failure; `research_agent/llm.py::decide_next_action` returns an `AgentAction(action="_llm_failed")` sentinel that triggers a deterministic backup planner in `agent.py`. An LLM being down slows these subsystems down; it never stops them.
- **Model defaults:** Ollama `llama3.1:8b`; cloud fallbacks `gpt-4o-mini` (OpenAI), `claude-3-5-haiku-20241022` (Anthropic) — only used if the user opts in.

## Rules

1. **Always call through `ai_brain._call_llm_raw`/`_ollama_cfg`** — never instantiate a provider client directly in new code, even for a "quick" new agent.
2. **Any new structured-output need should use `_generate_phase`/`_extract_json`**, not a bespoke JSON-parsing attempt — the alias-mapping and repair-retry logic already handles the common failure modes.
3. **New generation functions must never raise** — always have a deterministic fallback, matching the existing `_fallback_*` pattern.
4. **New agents that build factual claims from research must construct the facts in code and only ask the LLM to phrase/summarize** — don't ask an open-ended "tell me about this business's problems" prompt with no grounding; that's how facts get invented.
5. **Apply the existing content-quality gate (or extend it) to any new user-facing generated text** — don't ship a new message-generation path that skips placeholder/HTML/hashtag/emoji/similarity checks.

## Architecture Guidance

New AI-assisted logic belongs in the module that owns that domain (an `intelligence/` agent, `enrichment/ai_enricher.py`, or a new sibling package) — not directly in a router. Routers call domain functions; domain functions call `ai_brain`.

## Implementation Guidance

When adding a new structured LLM call: define `required` fields, `aliases` for likely model naming variance, and a `fallback_fn` before writing the prompt. Write the fallback first — it's the thing that guarantees the feature degrades gracefully rather than breaking when Ollama is down or a cloud key is missing.

## Testing Requirements

- Mock `_call_llm_raw` for all agent tests — never depend on a live LLM in the automated suite (matches existing `tests/intelligence/*` pattern).
- Test both a valid structured response and a malformed one (missing field, wrapped in markdown fences, trailing comma) to confirm the repair loop and fallback both work.
- Test the content-quality gate independently (`tests/test_ai_brain_validation.py` is the existing pattern to extend, not duplicate).

## Security Considerations

Cloud provider API keys are DB-settings-stored and encrypted at rest (`secrets_crypto.py`) — never hardcode a key, never log a prompt/response that might contain one, never expose a key in an API response (settings endpoint redacts these — follow that pattern for any new key).

## Performance Considerations

Ollama calls have a configurable timeout (`ollama_timeout`, default 120s) — respect it in new call sites rather than hardcoding a different value. Batch/concurrent LLM calls should respect the existing `Semaphore`-based concurrency patterns used elsewhere in the codebase (e.g., enrichment's `Semaphore(5)`).

## Failure Modes

| Mistake | Fix |
|---|---|
| New agent imports `openai`/`anthropic` SDK directly | Route through `ai_brain._call_llm_raw` |
| New generation function raises on LLM timeout | Wrap with the existing retry+fallback pattern, never let it propagate |
| Prompt asks the LLM to invent a case study/statistic for persuasiveness | Forbidden per the existing `_FORBIDDEN_CLAIM_RE` precedent — ground all claims in real data |
| New JSON parsing does its own regex instead of using `_extract_json` | Reuse `_generate_phase`/`_extract_json` — don't reinvent the repair loop |

## Verification Checklist

- [ ] All LLM calls go through `ai_brain._call_llm_raw`
- [ ] New generation function has a deterministic fallback and never raises
- [ ] Structured output tested with both valid and malformed model responses
- [ ] User-facing generated text passes the content-quality gate (or an equivalent extension of it)
- [ ] No provider API key hardcoded or logged

## Related Skills

`autolead-lead-intelligence-and-scoring`, `autolead-browser-research-agent`, `autolead-discovery-and-source-adapters`, `autolead-outreach-safety`, `autolead-security-and-secrets`

---
Version: 1.1
Scope: AutoLead-manager
Last reviewed: 2026-08-30 (Discovery Planner + Browser Research Agent additions)
