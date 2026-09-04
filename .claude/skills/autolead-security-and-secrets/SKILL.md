---
name: autolead-security-and-secrets
description: Use when handling API keys, passwords, tokens, authentication, rate limiting, input validation, or anything exported/logged/returned from an API in AutoLead.
---

# AutoLead Security & Secrets

## Purpose

Documents AutoLead's actual security model (single-user, local-first) so new code neither weakens it nor assumes capabilities (RBAC, multi-tenancy) that don't exist.

## When to Use

Adding an API key/credential, a new API endpoint, an export feature, a log statement that might include sensitive data, or touching `auth.py`/`secrets_crypto.py`/rate limiting.

## When NOT to Use

Pure UI styling or read-only analytics with no new data exposure surface.

## Project Context

- **Auth model: single-user, local app password (optional).** `backend/auth.py` + `routers/auth_router.py` — bcrypt (direct, not passlib), session tokens, applied via FastAPI router-level dependencies. **API is open when no password is set** (first-run friendly by design — not a bug). **There is no RBAC, no multi-tenancy, no org/team model in this codebase**, despite a 2026-08-06 planning memory describing a multi-tenant SaaS mission — that mission was never executed; every commit since has stayed single-tenant. Do not assume multi-tenant primitives exist.
- **Settings layer:** `.env`/`config.py` pydantic defaults, overridden at runtime by the `app_settings` DB table. Secrets in DB settings (SMTP/IMAP passwords, cloud LLM API keys) are encrypted at rest via `backend/secrets_crypto.py`, degrading gracefully until `cryptography` is installed (it is, per `requirements.txt`).
- **`GET /api/settings` redacts secrets** — this was a fixed production bug (plaintext leak), not the original design; any new settings-reading endpoint must follow the same redaction pattern.
- **Rate limiting:** `slowapi`, applied to campaign/start, campaign/send*, ai/generate*, auth/unlock.
- **Input validation:** `backend/validators.py` — format-only regex checks for email/phone (`is_valid_email`, `is_valid_phone`), normalization helpers. No MX/DNS/disposable-domain checks exist yet (see `autolead-lead-intelligence-and-scoring` for the planned contact-verification work).
- **No CORS/CSRF hardening beyond FastAPI defaults observed** — this is a local single-user app, not a public multi-tenant API; don't over-engineer auth for a threat model that doesn't apply, but don't regress the existing single-password gate either.

## Rules

1. **Never hardcode a credential, API key, or password.** Use environment variables (`.env`/`config.py`) with the existing DB-settings-override layer for anything user-configurable.
2. **Never log or return a secret in an API response.** Follow the existing settings-redaction pattern for any new endpoint that surfaces configuration.
3. **New secrets (a contact-verification provider key, a new cloud LLM key) go through `secrets_crypto.py`'s encryption-at-rest pattern**, not plaintext DB storage.
4. **Don't build authentication/authorization assuming multi-tenancy exists.** If multi-tenant/RBAC work is ever actually requested, that's a distinct, large initiative (see the SaaS-transformation project memory) — not something to bolt onto individual features.
5. **Exports (CSV/Excel) must never include secrets or internal credentials** — verify field lists explicitly, don't dump entire DB rows.
6. **Any new scraping/verification/LLM-provider integration must respect rate limits** — add `slowapi` limiting to new endpoints that trigger expensive/external work, matching existing campaign/AI endpoint patterns.
7. **Never bypass or weaken the DO_NOT_CONTACT/opt-out safety mechanism for "efficiency"** — this is a security-adjacent business rule; see `autolead-outreach-safety` for the full detail, but the principle belongs here too: an outreach system that can't be told "stop" is a real harm surface.

## Architecture Guidance

New sensitive config follows the existing three-layer pattern: `.env`/pydantic default → `app_settings` DB override (encrypted if secret) → redacted on any read-back endpoint.

## Implementation Guidance

Before adding a new endpoint that returns settings/config, grep for the existing redaction logic in `routers/settings_router.py` and mirror it, rather than writing a fresh serializer that might forget to redact a new field.

## Testing Requirements

- Any new secret-bearing settings field needs a test confirming it's redacted on read.
- Any new export feature needs a test confirming the exported columns don't include a secret/credential field.
- Rate-limited endpoints should have their limit verified, not just declared.

## Security Considerations

This section IS the skill — see Rules above. Additionally: WhatsApp/email sending automate real external communication channels; a security bug here (e.g., a secret leaking into a sent message body) has real-world consequences beyond this app.

## Performance Considerations

Encryption/decryption of secrets happens per-settings-read; if a new feature reads secrets in a hot loop, cache appropriately rather than decrypting repeatedly — check how existing config accessors (`_smtp_cfg`, `_wa_cfg`, `_ollama_cfg`) handle this before writing a new one.

## Failure Modes

| Mistake | Fix |
|---|---|
| New provider API key stored in plaintext in `app_settings` | Route through `secrets_crypto.py` |
| New settings endpoint returns the raw DB row | Redact secret fields explicitly, matching existing pattern |
| Export endpoint does `SELECT *` and serializes to CSV | Explicitly whitelist exported columns |
| New feature assumes a `tenant_id`/`org_id`/user-role exists | It doesn't — this is single-user; don't build on a false assumption |

## Verification Checklist

- [ ] No hardcoded credential in new code
- [ ] New secret fields encrypted at rest and redacted on read
- [ ] New export explicitly whitelists columns, no blanket row dump
- [ ] New expensive/external-facing endpoint has rate limiting
- [ ] No assumption of multi-tenant/RBAC primitives that don't exist

## Related Skills

`autolead-ai-llm-engineering`, `autolead-outreach-safety`, `autolead-backend-architecture`

---
Version: 1.0
Scope: AutoLead-manager
Last reviewed: 2026-08-25
