# AutoLead Project Skill Router

What to consult for a given task. Load the listed skills; don't load the whole set for every change.

| If the task involves | Use these skills |
|---|---|
| Lead Search / Quick Search (quick mode) | `autolead-lead-generation-architecture` → `autolead-discovery-and-source-adapters` → `autolead-reliability-and-background-jobs` (JobQueue) → `autolead-verification-before-completion` |
| Lead Search Campaign (advanced mode) | `autolead-lead-generation-architecture` → `autolead-discovery-and-source-adapters` → `autolead-reliability-and-background-jobs` → `autolead-lead-intelligence-and-scoring` → `autolead-verification-before-completion` |
| Browser Research Agent (`backend/research_agent/`, `/api/research-agent`, deep per-business research) | `autolead-lead-generation-architecture` → `autolead-browser-research-agent` → `autolead-browser-automation` + `autolead-ai-llm-engineering` (loop internals) → `autolead-reliability-and-background-jobs` (JobQueue/cancellation) → `autolead-verification-before-completion` |
| A new or changed scraper source | `autolead-discovery-and-source-adapters` → `autolead-browser-automation` (only if it needs a browser) → `autolead-verification-before-completion` |
| Website research / enrichment | `autolead-lead-intelligence-and-scoring` → `autolead-browser-automation` (only if JS rendering needed) → `autolead-ai-llm-engineering` |
| Evidence / provenance / no-fabrication rules for researched fields | `autolead-browser-research-agent` (research_agent's `ResearchEvidence` model) or `autolead-lead-intelligence-and-scoring` (intelligence's `research_evidence` table) — pick by subsystem |
| Geographic expansion (city/state/country/worldwide → bounded city list) | `autolead-browser-research-agent` (`planner.py::expand_geography`) |
| Contact validation (email/phone/WhatsApp) | `autolead-lead-intelligence-and-scoring` → `autolead-security-and-secrets` (provider keys) |
| AI qualification / pain points / opportunities / scoring | `autolead-lead-intelligence-and-scoring` → `autolead-ai-llm-engineering` |
| Personalized message generation | `autolead-lead-intelligence-and-scoring` → `autolead-ai-llm-engineering` → `autolead-outreach-safety` |
| Anything that sends, approves, or opts out a lead | `autolead-outreach-safety` (start here, non-negotiable) → `autolead-reliability-and-background-jobs` if scheduled/queued |
| Database schema change | `autolead-database-and-migrations` → `autolead-backend-architecture` → `autolead-verification-before-completion` |
| New API endpoint | `autolead-backend-architecture` → `autolead-security-and-secrets` → `autolead-verification-before-completion` |
| New UI page/flow | `autolead-frontend-architecture` → `autolead-product-ux` → `autolead-verification-before-completion` |
| Campaign engine / scheduling / queue change | `autolead-reliability-and-background-jobs` (read the two-engines landmine first) → `autolead-verification-before-completion` |
| JobQueue producer/handler change (Quick Search, Research Agent) | `autolead-reliability-and-background-jobs` → the owning subsystem skill (`autolead-discovery-and-source-adapters` / `autolead-browser-research-agent`) → `autolead-verification-before-completion` |
| Production bug / unexpected behavior | `autolead-systematic-debugging` → the relevant subsystem skill (see rows above) → `autolead-verification-before-completion` |
| Secrets, API keys, auth, exports | `autolead-security-and-secrets` |
| Any lead-gen task you're unsure how to scope | `autolead-lead-generation-architecture` first — it's the map |

## Rule of thumb

Start at the row matching the task. If the task spans rows (e.g., a new campaign stage that also sends messages), load all matching rows' skills — don't guess which single skill covers a cross-cutting change.
