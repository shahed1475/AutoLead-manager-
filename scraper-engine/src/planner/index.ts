/**
 * AI Planner — transforms niche + city into 10-20 targeted search queries.
 * Calls Ollama locally; falls back to curated template queries if Ollama is down.
 */

import axios from 'axios'
import type { OllamaConfig, SearchQuery } from '../types'
import { logger } from '../logger'

// ── Prompt ────────────────────────────────────────────────────────────────────

function buildPlannerPrompt(niche: string, city: string): string {
  return `You are a senior lead generation strategist.
Generate exactly 15 targeted search queries to find high-potential business leads.

Target:
  Niche: ${niche}
  City: ${city}

Objective: Find businesses that need digital marketing help — poor/no website,
low reviews, no social presence, outdated branding.

Generate queries across these intent categories:
  1. Direct search — core niche discovery
  2. District/neighbourhood targeting — specific areas within ${city}
  3. Low-review businesses — "few reviews" or comparison searches
  4. No-website opportunity — businesses without strong online presence
  5. Contact discovery — find email/phone leads
  6. Sub-niche precision — specialisations within the niche

Return ONLY a valid JSON array — no text before or after:
[
  {"query": "exact search string", "source": "GOOGLE_MAPS", "intent": "brief_description"},
  ...
]

Rules:
  - "source" must be GOOGLE_MAPS or GOOGLE_SEARCH
  - GOOGLE_MAPS queries: natural language like "${niche} in ${city} Marina"
  - GOOGLE_SEARCH queries: discovery searches like "${niche} ${city} contact email"
  - 15 entries total — varied and realistic`
}

// ── Fallback template queries ──────────────────────────────────────────────────

function templateQueries(niche: string, city: string): SearchQuery[] {
  return [
    { query: `${niche} in ${city}`,                      source: 'GOOGLE_MAPS',   intent: 'direct_search' },
    { query: `best ${niche} ${city}`,                    source: 'GOOGLE_MAPS',   intent: 'quality_search' },
    { query: `top rated ${niche} ${city}`,               source: 'GOOGLE_MAPS',   intent: 'rating_search' },
    { query: `affordable ${niche} ${city}`,              source: 'GOOGLE_MAPS',   intent: 'price_segment' },
    { query: `new ${niche} ${city}`,                     source: 'GOOGLE_MAPS',   intent: 'new_businesses' },
    { query: `${niche} ${city} contact email`,           source: 'GOOGLE_SEARCH', intent: 'email_discovery' },
    { query: `${niche} ${city} phone number website`,    source: 'GOOGLE_SEARCH', intent: 'contact_discovery' },
    { query: `${niche} ${city} social media`,            source: 'GOOGLE_SEARCH', intent: 'social_presence' },
    { query: `small ${niche} business ${city}`,          source: 'GOOGLE_MAPS',   intent: 'small_business' },
    { query: `local ${niche} ${city}`,                   source: 'GOOGLE_MAPS',   intent: 'local_targeting' },
  ]
}

// ── JSON parser with fallback ─────────────────────────────────────────────────

function parseQueries(raw: string, niche: string, city: string): SearchQuery[] {
  try {
    const match = raw.match(/\[[\s\S]+\]/)
    if (!match) return templateQueries(niche, city)

    const arr = JSON.parse(match[0])
    if (!Array.isArray(arr) || arr.length === 0) return templateQueries(niche, city)

    const parsed: SearchQuery[] = arr
      .filter((q: unknown) => q && typeof (q as Record<string, unknown>).query === 'string')
      .map((q: Record<string, unknown>) => ({
        query:  String(q.query).trim(),
        source: q.source === 'GOOGLE_SEARCH' ? 'GOOGLE_SEARCH' : 'GOOGLE_MAPS',
        intent: String(q.intent || 'general').trim(),
      } as SearchQuery))
      .slice(0, 20)

    return parsed.length >= 3 ? parsed : templateQueries(niche, city)
  } catch {
    return templateQueries(niche, city)
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function generateSearchQueries(
  niche: string,
  city: string,
  cfg: OllamaConfig,
): Promise<SearchQuery[]> {
  logger.info(`AI Planner → generating queries for "${niche}" / "${city}"`, 'planner')

  try {
    const res = await axios.post(
      `${cfg.baseUrl}/api/generate`,
      {
        model:   cfg.model,
        prompt:  buildPlannerPrompt(niche, city),
        stream:  false,
        think:   false,
        options: { temperature: 0.75, num_predict: 900 },
      },
      { timeout: cfg.timeout * 1000 },
    )

    const raw     = ((res.data?.response ?? '') as string).replace(/<think>[\s\S]*?<\/think>/g, '').trim()
    const queries = parseQueries(raw, niche, city)
    logger.success(`AI Planner → ${queries.length} queries generated`, 'planner')
    return queries
  } catch (err) {
    logger.warn(`AI Planner → Ollama unavailable, using template queries (${err})`, 'planner')
    return templateQueries(niche, city)
  }
}
