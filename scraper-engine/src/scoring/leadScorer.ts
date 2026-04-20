/**
 * Lead Scorer — deterministic rule-based scoring engine.
 * Scores leads 1-100 based on digital marketing opportunity signals.
 * No LLM needed — fast, consistent, explainable.
 *
 * Score bands:  0-34 = low | 35-64 = medium | 65-100 = high priority
 */

import type { EnrichedLead, LeadScore } from '../types'

interface ScoringRule {
  check:  (lead: EnrichedLead) => boolean
  points: number
  reason: string
}

// ── Scoring rules (highest-value opportunities first) ─────────────────────────

const RULES: ScoringRule[] = [
  // Website presence — single biggest opportunity signal
  {
    check:  (l) => !l.website || l.websiteQuality?.hasWebsite === false,
    points: 35,
    reason: 'No website — huge digital marketing gap',
  },
  {
    check:  (l) => l.websiteQuality?.hasWebsite === true && l.websiteQuality.quality === 'poor',
    points: 22,
    reason: 'Poor website quality — redesign opportunity',
  },
  {
    check:  (l) => l.websiteQuality?.hasWebsite === true && l.websiteQuality.quality === 'average',
    points: 10,
    reason: 'Average website — significant improvement opportunity',
  },

  // Review signals — local SEO / reputation management
  {
    check:  (l) => parseInt(l.review_count ?? '0', 10) === 0,
    points: 25,
    reason: 'Zero reviews — social proof entirely missing',
  },
  {
    check:  (l) => {
      const r = parseInt(l.review_count ?? '-1', 10)
      return r >= 1 && r <= 10
    },
    points: 18,
    reason: 'Very few reviews — easy to dominate local search',
  },
  {
    check:  (l) => {
      const r = parseInt(l.review_count ?? '-1', 10)
      return r >= 11 && r <= 30
    },
    points: 8,
    reason: 'Below-average review count — growth opportunity',
  },

  // Rating signals
  {
    check:  (l) => {
      const r = parseFloat(l.rating ?? '0')
      return r > 0 && r < 3.5
    },
    points: 22,
    reason: 'Low star rating — reputation management is urgent',
  },
  {
    check:  (l) => {
      const r = parseFloat(l.rating ?? '0')
      return r >= 3.5 && r < 4.0
    },
    points: 10,
    reason: 'Below-average rating — reputation improvement needed',
  },

  // Website technical issues (only if website exists)
  {
    check:  (l) => l.websiteQuality?.hasWebsite === true && !l.websiteQuality.hasSSL,
    points: 12,
    reason: 'No SSL certificate — security red flag for visitors',
  },
  {
    check:  (l) => l.websiteQuality?.hasWebsite === true && !l.websiteQuality.hasMobileViewport,
    points: 10,
    reason: 'Not mobile-friendly — losing 60%+ of mobile traffic',
  },
  {
    check:  (l) => l.websiteQuality?.hasWebsite === true && !l.websiteQuality.hasAnalytics,
    points: 6,
    reason: 'No analytics — marketing decisions made blind',
  },
  {
    check:  (l) => l.websiteQuality?.hasWebsite === true && !l.websiteQuality.isReachable,
    points: 15,
    reason: 'Website unreachable — customers hitting error pages',
  },

  // Contact availability (affects reachability & urgency)
  {
    check:  (l) => !l.email && !l.phone,
    points: 8,
    reason: 'No contact details — hard for customers to reach them',
  },
  {
    check:  (l) => Boolean(l.email),
    points: 4,
    reason: 'Has email — direct outreach channel available',
  },
]

// ── Penalty rules (subtract points for already-strong signals) ────────────────

interface PenaltyRule {
  check:  (lead: EnrichedLead) => boolean
  points: number
  reason: string
}

const PENALTIES: PenaltyRule[] = [
  {
    check:  (l) => l.websiteQuality?.quality === 'good',
    points: 10,
    reason: 'Good website — already has solid online presence',
  },
  {
    check:  (l) => parseInt(l.review_count ?? '0', 10) > 100,
    points: 8,
    reason: 'Well-reviewed — established online reputation',
  },
  {
    check:  (l) => parseFloat(l.rating ?? '0') >= 4.5,
    points: 5,
    reason: 'Excellent rating — strong reputation already',
  },
]

// ── Score → priority mapping ──────────────────────────────────────────────────

function toPriority(score: number): LeadScore['priority'] {
  if (score >= 65) return 'high'
  if (score >= 35) return 'medium'
  return 'low'
}

// ── Public API ────────────────────────────────────────────────────────────────

export function scoreLead(lead: EnrichedLead): LeadScore {
  let score    = 0
  const reasons: string[] = []

  for (const rule of RULES) {
    if (rule.check(lead)) {
      score += rule.points
      reasons.push(rule.reason)
    }
  }

  for (const penalty of PENALTIES) {
    if (penalty.check(lead)) {
      score -= penalty.points
      reasons.push(`(-) ${penalty.reason}`)
    }
  }

  // Boost score slightly if AI analysis identified an urgent pitch angle
  const urgentAngles = new Set([
    'website_creation', 'reputation_management', 'website_redesign',
  ])
  if (lead.analysis && urgentAngles.has(lead.analysis.pitch_angle)) {
    score += 8
    reasons.push(`AI identified urgent opportunity: ${lead.analysis.pitch_angle.replace(/_/g, ' ')}`)
  }

  score = Math.max(1, Math.min(100, score))

  return {
    score,
    priority: toPriority(score),
    reasons,
  }
}

export function scoreLeads(leads: EnrichedLead[]): void {
  for (const lead of leads) {
    lead.score = scoreLead(lead)
  }
}

// ── Sort helper ───────────────────────────────────────────────────────────────

export function sortByScore(leads: EnrichedLead[]): EnrichedLead[] {
  return [...leads].sort((a, b) => (b.score?.score ?? 0) - (a.score?.score ?? 0))
}
