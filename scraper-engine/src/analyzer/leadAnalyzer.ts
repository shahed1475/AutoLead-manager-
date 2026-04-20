/**
 * AI Lead Analyzer — uses Ollama to identify problems, opportunities, and pitch angles.
 * Falls back to a rule-based analysis when Ollama is unavailable.
 */

import axios from 'axios'
import type { EnrichedLead, LeadAnalysis, OllamaConfig } from '../types'
import { logger } from '../logger'

// ── Prompt builder ────────────────────────────────────────────────────────────

function buildAnalysisPrompt(lead: EnrichedLead): string {
  const wq = lead.websiteQuality
  const websiteSummary = !wq || !wq.hasWebsite
    ? 'NO WEBSITE — major digital gap'
    : !wq.isReachable
      ? 'Website exists but is unreachable/broken'
      : `Quality: ${wq.quality.toUpperCase()} (score ${wq.qualityScore}/100)${wq.issues.length ? ' | Issues: ' + wq.issues.join('; ') : ''}`

  return `You are a digital marketing strategist. Analyze this business lead and identify concrete sales opportunities.

LEAD DATA:
  Business:      ${lead.business_name}
  Industry:      ${lead.niche ?? 'Unknown'}
  City:          ${lead.city ?? 'Unknown'}
  Phone:         ${lead.phone ?? 'Not found'}
  Email:         ${lead.email ?? 'Not found'}
  Rating:        ${lead.rating ? lead.rating + ' stars' : 'No rating'}
  Reviews:       ${lead.review_count ? lead.review_count + ' reviews' : 'No reviews'}
  Website:       ${lead.website ?? 'NONE'}
  Web presence:  ${websiteSummary}

TASK: Based on the data above, provide:
  1. Specific problems this business has (digital/marketing pain points)
  2. Concrete opportunities to help them grow
  3. The single best pitch angle for a cold outreach

VALID PITCH ANGLES:
  website_redesign | website_creation | seo_optimization | social_media_management |
  reputation_management | lead_generation | email_marketing | google_ads | local_seo |
  chatbot_integration | brand_identity

Return ONLY valid JSON — no text before or after:
{
  "problems": ["specific problem 1", "specific problem 2", "specific problem 3"],
  "opportunities": ["opportunity 1", "opportunity 2"],
  "pitch_angle": "one_of_the_valid_angles_above",
  "summary": "One compelling sentence describing the primary business opportunity",
  "confidence": "high"
}

Be specific. Reference the actual data (rating, review count, website issues).
"problems" must reflect REAL issues visible in the data — not generic statements.`
}

// ── JSON extraction ───────────────────────────────────────────────────────────

function extractAnalysis(raw: string): LeadAnalysis | null {
  const candidates = [
    raw.match(/\{[\s\S]+\}/)?.[0],
    raw.match(/```json\s*([\s\S]+?)\s*```/)?.[1],
    raw.match(/```\s*([\s\S]+?)\s*```/)?.[1],
    raw.trim(),
  ]

  for (const candidate of candidates) {
    if (!candidate) continue
    try {
      const d = JSON.parse(candidate)
      if (Array.isArray(d.problems) && Array.isArray(d.opportunities) && d.pitch_angle) {
        return {
          problems:     d.problems.slice(0, 5).map(String),
          opportunities:d.opportunities.slice(0, 4).map(String),
          pitch_angle:  String(d.pitch_angle).trim(),
          summary:      String(d.summary ?? '').trim() || `Opportunity to improve ${d.pitch_angle.replace(/_/g, ' ')}`,
          confidence:   (['high', 'medium', 'low'].includes(d.confidence) ? d.confidence : 'medium') as LeadAnalysis['confidence'],
        }
      }
    } catch { /* try next */ }
  }
  return null
}

// ── Rule-based fallback ───────────────────────────────────────────────────────

function ruleBasedAnalysis(lead: EnrichedLead): LeadAnalysis {
  const problems: string[]     = []
  const opportunities: string[]= []
  let pitch_angle              = 'lead_generation'
  const wq                     = lead.websiteQuality
  const reviews                = parseInt(lead.review_count ?? '0', 10)
  const rating                 = parseFloat(lead.rating ?? '0')

  if (!wq?.hasWebsite || !lead.website) {
    problems.push('No website — invisible to online customers')
    opportunities.push('Build a professional website to capture online leads')
    pitch_angle = 'website_creation'
  } else if (wq.quality === 'poor') {
    problems.push('Website is outdated or broken — hurts credibility')
    opportunities.push('Modern website redesign to convert visitors to customers')
    pitch_angle = 'website_redesign'
  } else if (!wq.hasSSL) {
    problems.push('Website has no SSL — browsers show security warnings')
    opportunities.push('Security upgrade + website modernisation')
    pitch_angle = 'website_redesign'
  }

  if (!wq?.hasMobileViewport) {
    problems.push('Website not mobile-friendly — 60%+ of searches are on phones')
    opportunities.push('Mobile-first redesign to capture mobile traffic')
  }

  if (reviews === 0) {
    problems.push('Zero reviews — loses trust battles against competitors')
    opportunities.push('Review generation campaign to build social proof fast')
    pitch_angle = pitch_angle === 'lead_generation' ? 'reputation_management' : pitch_angle
  } else if (reviews < 15) {
    problems.push(`Only ${reviews} reviews — well below the market average`)
    opportunities.push('Systematic review acquisition strategy')
  }

  if (rating > 0 && rating < 3.8) {
    problems.push(`${rating} star rating — actively repelling potential customers`)
    opportunities.push('Reputation recovery + negative review response strategy')
    pitch_angle = 'reputation_management'
  }

  if (!wq?.hasAnalytics) {
    problems.push('No analytics — flying blind with no data on website visitors')
    opportunities.push('Analytics setup to track ROI and identify growth levers')
  }

  if (!lead.email) {
    problems.push('No public email found — hard for customers to reach them')
    opportunities.push('Contact page optimisation and lead capture setup')
  }

  if (problems.length === 0) {
    problems.push('Average online presence — room to outpace competitors')
    opportunities.push('SEO and local search optimisation')
    pitch_angle = 'seo_optimization'
  }

  const summary = pitch_angle === 'website_creation'
    ? `${lead.business_name} has zero web presence — a full website build is the primary opportunity.`
    : pitch_angle === 'reputation_management'
      ? `${lead.business_name} has a damaged reputation that's costing them customers daily.`
      : `${lead.business_name} has a weak digital presence that can be improved quickly.`

  return { problems, opportunities, pitch_angle, summary, confidence: 'medium' }
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function analyzeLead(
  lead: EnrichedLead,
  cfg: OllamaConfig,
): Promise<LeadAnalysis> {
  try {
    const res = await axios.post(
      `${cfg.baseUrl}/api/generate`,
      {
        model:   cfg.model,
        prompt:  buildAnalysisPrompt(lead),
        stream:  false,
        think:   false,
        options: { temperature: 0.6, num_predict: 700 },
      },
      { timeout: cfg.timeout * 1000 },
    )

    const raw    = ((res.data?.response ?? '') as string).replace(/<think>[\s\S]*?<\/think>/g, '').trim()
    const parsed = extractAnalysis(raw)

    if (parsed) {
      logger.debug(`AI analyzed: ${lead.business_name} → ${parsed.pitch_angle}`, 'analyzer')
      return parsed
    }

    logger.warn(`AI analysis parse failed for ${lead.business_name} — using rule-based`, 'analyzer')
    return ruleBasedAnalysis(lead)
  } catch {
    return ruleBasedAnalysis(lead)
  }
}

// ── Batch helper ──────────────────────────────────────────────────────────────

export async function analyzeLeadsInBatch(
  leads: EnrichedLead[],
  cfg: OllamaConfig,
  onProgress?: (lead: EnrichedLead) => void,
): Promise<void> {
  for (const lead of leads) {
    lead.analysis = await analyzeLead(lead, cfg)
    onProgress?.(lead)
    await new Promise((r) => setTimeout(r, 200))
  }
}
