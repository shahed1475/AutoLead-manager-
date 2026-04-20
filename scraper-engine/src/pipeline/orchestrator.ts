/**
 * Pipeline Orchestrator — full AI-powered lead generation flow.
 *
 * Flow:
 *   1. AI Planner      → generate targeted search queries
 *   2. Scraping        → collect raw leads from selected sources
 *   3. Website QA      → HTTP analysis of each lead's website
 *   4. AI Analysis     → problems, opportunities, pitch_angle per lead
 *   5. Lead Scoring    → priority score 1-100
 *   6. AI Messaging    → personalized outreach messages
 *   7. Save            → persist to FastAPI + update with AI messages
 */

import { generateSearchQueries }        from '../planner'
import { checkWebsiteQuality }          from '../analyzer/websiteQuality'
import { analyzeLead }                  from '../analyzer/leadAnalyzer'
import { generateMessages }             from '../messaging/messageGenerator'
import { scoreLead, sortByScore }       from '../scoring/leadScorer'
import { scrapeMultiSource, scrapeGoogleMapsWithQuery } from '../scraperService'
import { launchBrowser, newPage, closeBrowser } from '../browserController'
import { normalizeLead, isValidLead, DuplicateTracker } from '../leadProcessor'
import { saveLead, updateLeadAI, checkFastapiHealth }   from '../apiClient'
import { logger }                       from '../logger'
import type {
  PipelineOptions, PipelinePhase, EnrichedLead,
  ScraperConfig, SearchQuery,
} from '../types'

// ── Phase manager ─────────────────────────────────────────────────────────────

export class PhaseTracker {
  private phases: Record<string, PipelinePhase> = {}
  private onUpdate: (phases: Record<string, PipelinePhase>) => void

  constructor(onUpdate: (phases: Record<string, PipelinePhase>) => void) {
    this.onUpdate = onUpdate
  }

  init(names: string[]): void {
    for (const name of names) {
      this.phases[name] = { name, status: 'pending', count: 0 }
    }
    this.onUpdate({ ...this.phases })
  }

  start(name: string): void {
    this.phases[name] = { ...this.phases[name], status: 'running', startedAt: new Date().toISOString() }
    this.onUpdate({ ...this.phases })
  }

  complete(name: string, count: number): void {
    this.phases[name] = {
      ...this.phases[name],
      status:      'completed',
      count,
      completedAt: new Date().toISOString(),
    }
    this.onUpdate({ ...this.phases })
  }

  fail(name: string, error: string): void {
    this.phases[name] = { ...this.phases[name], status: 'failed', error, completedAt: new Date().toISOString() }
    this.onUpdate({ ...this.phases })
  }

  skip(name: string): void {
    this.phases[name] = { ...this.phases[name], status: 'skipped' }
    this.onUpdate({ ...this.phases })
  }

  get(): Record<string, PipelinePhase> {
    return { ...this.phases }
  }
}

// ── Result type ───────────────────────────────────────────────────────────────

export interface PipelineResult {
  leadsFound:         number
  leadsSaved:         number
  leadsAnalyzed:      number
  leadsScored:        number
  messagesGenerated:  number
  errors:             string[]
  durationMs:         number
  phases:             Record<string, PipelinePhase>
}

// ── Main orchestrator ─────────────────────────────────────────────────────────

export async function runPipeline(
  opts: PipelineOptions,
  scraperCfg: ScraperConfig,
  ollamaBaseUrl: string,
  ollamaModel: string,
  ollamaTimeout: number,
  stopFn:       () => boolean,
  onLeadSaved:  (lead: EnrichedLead) => void,
  onPhaseUpdate:(phases: Record<string, PipelinePhase>) => void,
): Promise<PipelineResult> {
  const t0      = Date.now()
  const errors: string[] = []

  const ollama = { baseUrl: ollamaBaseUrl, model: ollamaModel, timeout: ollamaTimeout }
  const phases  = new PhaseTracker(onPhaseUpdate)
  const phaseNames = [
    'planning', 'scraping',
    ...(opts.runAiAnalysis ? ['website_analysis', 'ai_analysis'] : []),
    ...(opts.runScoring    ? ['scoring'] : []),
    ...(opts.runMessaging  ? ['messaging'] : []),
    'saving',
  ]
  phases.init(phaseNames)

  const result: PipelineResult = {
    leadsFound:        0,
    leadsSaved:        0,
    leadsAnalyzed:     0,
    leadsScored:       0,
    messagesGenerated: 0,
    errors,
    durationMs:        0,
    phases:            {},
  }

  // ── Phase 1: AI Planner ────────────────────────────────────────────────────
  let searchQueries: SearchQuery[] = []

  phases.start('planning')
  try {
    searchQueries = await generateSearchQueries(opts.niche, opts.city, ollama)
    logger.info(`Planning complete — ${searchQueries.length} queries`, 'pipeline')
    phases.complete('planning', searchQueries.length)
  } catch (err) {
    const msg = `Planning failed: ${err}`
    errors.push(msg)
    logger.warn(msg, 'pipeline')
    phases.fail('planning', msg)
    // proceed with empty queries → scrapeMultiSource uses default niche+city
  }

  if (stopFn()) return finalize(result, phases, t0)

  // ── Phase 2: Scraping ──────────────────────────────────────────────────────
  const rawLeads: EnrichedLead[] = []
  const dedup = new DuplicateTracker()
  const budget = opts.daily_cap ?? scraperCfg.maxResults

  phases.start('scraping')
  try {
    const mapsQueries = searchQueries.filter((q) => q.source === 'GOOGLE_MAPS')

    if (mapsQueries.length > 0 && opts.sources.includes('GOOGLE_MAPS')) {
      // AI planner queries: run each targeted query through Maps
      const browser = await launchBrowser(scraperCfg)
      const perQuery = Math.ceil(budget / Math.max(mapsQueries.length, 1))

      for (const sq of mapsQueries) {
        if (stopFn()) break
        const page = await newPage(browser)
        try {
          logger.info(`Maps query: "${sq.query}" [${sq.intent}]`, 'pipeline')
          const found = await scrapeGoogleMapsWithQuery(
            page, sq.query, opts.niche, opts.city, perQuery, scraperCfg, stopFn,
          )
          for (const raw of found) {
            if (dedup.isDuplicate(raw)) continue
            dedup.track(raw)
            rawLeads.push(raw as EnrichedLead)
            rawLeads[rawLeads.length - 1].searchQuery = sq.query
          }
        } finally {
          await page.close().catch(() => {})
        }

        if (rawLeads.length >= budget) break
      }

      // Run non-Maps sources separately
      const otherSources = opts.sources.filter((s) => s !== 'GOOGLE_MAPS')
      if (otherSources.length > 0) {
        const extras = await scrapeMultiSource(
          otherSources, opts.niche, opts.city,
          Math.max(budget - rawLeads.length, 0),
          scraperCfg, stopFn,
        )
        for (const raw of extras) {
          if (dedup.isDuplicate(raw)) continue
          dedup.track(raw)
          rawLeads.push(raw as EnrichedLead)
        }
      }

      await closeBrowser()
    } else {
      // No planner queries: fall back to standard multi-source scrape
      const found = await scrapeMultiSource(
        opts.sources, opts.niche, opts.city, budget, scraperCfg, stopFn,
      )
      rawLeads.push(...(found as EnrichedLead[]))
    }

    result.leadsFound = rawLeads.length
    phases.complete('scraping', rawLeads.length)
    logger.success(`Scraping complete — ${rawLeads.length} unique leads`, 'pipeline')
  } catch (err) {
    const msg = `Scraping error: ${err}`
    errors.push(msg)
    logger.error(msg, 'pipeline')
    phases.fail('scraping', msg)
    await closeBrowser().catch(() => {})
    return finalize(result, phases, t0)
  }

  if (stopFn() || rawLeads.length === 0) return finalize(result, phases, t0)

  // ── Phase 3: Website Quality Analysis ─────────────────────────────────────
  if (opts.runAiAnalysis) {
    phases.start('website_analysis')
    let wqDone = 0
    for (const lead of rawLeads) {
      if (stopFn()) break
      if (lead.website) {
        try {
          lead.websiteQuality = await checkWebsiteQuality(lead.website)
        } catch { /* skip — lead still usable */ }
      }
      wqDone++
    }
    phases.complete('website_analysis', wqDone)
    logger.success(`Website analysis complete — ${wqDone} leads checked`, 'pipeline')
  }

  // ── Phase 4: AI Lead Analysis ──────────────────────────────────────────────
  if (opts.runAiAnalysis && !stopFn()) {
    phases.start('ai_analysis')
    let analyzed = 0
    for (const lead of rawLeads) {
      if (stopFn()) break
      try {
        lead.analysis = await analyzeLead(lead, ollama)
        analyzed++
      } catch (err) {
        errors.push(`Analysis failed for ${lead.business_name}: ${err}`)
      }
    }
    result.leadsAnalyzed = analyzed
    phases.complete('ai_analysis', analyzed)
    logger.success(`AI analysis complete — ${analyzed} leads analyzed`, 'pipeline')
  }

  // ── Phase 5: Lead Scoring ─────────────────────────────────────────────────
  if (opts.runScoring && !stopFn()) {
    phases.start('scoring')
    for (const lead of rawLeads) {
      lead.score = scoreLead(lead)
      result.leadsScored++
    }
    // Reorder by score so highest-priority leads are saved first
    rawLeads.sort((a, b) => (b.score?.score ?? 0) - (a.score?.score ?? 0))
    phases.complete('scoring', result.leadsScored)
    logger.success(`Scoring complete — top lead score: ${rawLeads[0]?.score?.score ?? 0}`, 'pipeline')
  }

  // ── Phase 6: AI Messaging ─────────────────────────────────────────────────
  if (opts.runMessaging && !stopFn()) {
    phases.start('messaging')
    let msgDone = 0
    for (const lead of rawLeads) {
      if (stopFn()) break
      try {
        lead.messages = await generateMessages(lead, ollama)
        msgDone++
      } catch (err) {
        errors.push(`Messaging failed for ${lead.business_name}: ${err}`)
      }
    }
    result.messagesGenerated = msgDone
    phases.complete('messaging', msgDone)
    logger.success(`Messaging complete — ${msgDone} message sets generated`, 'pipeline')
  }

  // ── Phase 7: Save to FastAPI ──────────────────────────────────────────────
  phases.start('saving')
  let saved = 0

  for (const lead of rawLeads) {
    if (stopFn()) break
    try {
      const saveResult = await saveLead(lead)

      if (saveResult.success && saveResult.id && lead.messages) {
        await updateLeadAI(saveResult.id, {
          ai_whatsapp_msg:  lead.messages.whatsapp,
          ai_email_subject: lead.messages.email_subject,
          ai_email_body:    lead.messages.email_body,
          ai_followup_msg:  lead.messages.followup,
        })
      }

      if (saveResult.success) {
        lead._savedId = saveResult.id
        saved++
        onLeadSaved(lead)
        logger.success(
          `Saved: ${lead.business_name} | score=${lead.score?.score ?? '?'} | ${lead.score?.priority ?? ''} priority`,
          'pipeline',
        )
      }
    } catch (err) {
      errors.push(`Save failed for ${lead.business_name}: ${err}`)
    }
  }

  result.leadsSaved = saved
  phases.complete('saving', saved)
  logger.success(`Pipeline complete — ${saved}/${rawLeads.length} leads saved`, 'pipeline')

  return finalize(result, phases, t0)
}

function finalize(
  result: PipelineResult,
  phases: PhaseTracker,
  t0: number,
): PipelineResult {
  result.durationMs = Date.now() - t0
  result.phases     = phases.get()
  return result
}
