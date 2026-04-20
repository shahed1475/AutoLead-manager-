import express from 'express'
import cors from 'cors'
import { createServer } from 'http'
import { WebSocketServer } from 'ws'
import { v4 as uuidv4 } from 'uuid'
import * as dotenv from 'dotenv'
import * as path from 'path'

dotenv.config({ path: path.join(__dirname, '..', '.env') })

import { PORT, config, ollamaConfig } from './config'
import { logger, registerWsClient, broadcastState, broadcastLead, broadcastDone } from './logger'
import { checkFastapiHealth, saveLead, updateLeadChannel, setFastapiUrl, getLeadsByStatus, updateLeadAI } from './apiClient'
import { scrapeMultiSource } from './scraperService'
import { closeBrowser, getBrowser } from './browserController'
import { runPipeline, PhaseTracker, type PipelineResult } from './pipeline/orchestrator'
import { analyzeLead } from './analyzer/leadAnalyzer'
import { checkWebsiteQuality } from './analyzer/websiteQuality'
import { generateMessages } from './messaging/messageGenerator'
import { scoreLead } from './scoring/leadScorer'
import type { ScraperState, StartPayload, ScraperSource, ScraperConfig, PipelinePhase, EnrichedLead } from './types'

// ── State ─────────────────────────────────────────────────────────────────────

const state: ScraperState = {
  status:        'idle',
  runId:         null,
  niche:         null,
  city:          null,
  channel:       null,
  sources:       [],
  leadsFound:    0,
  leadsSaved:    0,
  leadsFailed:   0,
  startedAt:     null,
  stopRequested: false,
  currentSource: null,
  currentAction: null,
}

let pipelinePhases: Record<string, PipelinePhase> = {}
let lastPipelineResult: PipelineResult | null      = null

function emitState() {
  broadcastState({ ...state })
}

// ── Background run ────────────────────────────────────────────────────────────

async function runEngine(payload: StartPayload, runCfg: ScraperConfig): Promise<void> {
  state.status      = 'running'
  state.runId       = uuidv4()
  state.niche       = payload.niche
  state.city        = payload.city
  state.channel     = payload.channel
  state.sources     = payload.sources
  state.leadsFound  = 0
  state.leadsSaved  = 0
  state.leadsFailed = 0
  state.startedAt   = new Date().toISOString()
  state.stopRequested = false
  pipelinePhases    = {}
  emitState()

  const useAiPipeline = payload.runAiAnalysis || payload.runScoring || payload.runMessaging

  try {
    logger.info(
      `Engine started: ${payload.niche} / ${payload.city} [${useAiPipeline ? 'AI pipeline' : 'standard'}]`,
      'engine',
    )

    if (useAiPipeline) {
      // ── AI Pipeline mode ──────────────────────────────────────────────────
      const pipelineResult = await runPipeline(
        {
          niche:          payload.niche,
          city:           payload.city,
          channel:        payload.channel,
          sources:        payload.sources as ScraperSource[],
          daily_cap:      payload.daily_cap ?? runCfg.maxResults,
          runAiAnalysis:  payload.runAiAnalysis ?? true,
          runScoring:     payload.runScoring ?? true,
          runMessaging:   payload.runMessaging ?? false,
          headless:       runCfg.headless,
        },
        runCfg,
        ollamaConfig.baseUrl,
        ollamaConfig.model,
        ollamaConfig.timeout,
        () => state.stopRequested,
        (lead: EnrichedLead) => {
          state.leadsFound++
          state.leadsSaved++
          emitState()
          broadcastLead(lead)
        },
        (phases) => {
          pipelinePhases = phases
          emitState()
        },
      )

      lastPipelineResult = pipelineResult

      const finalStatus  = state.stopRequested ? 'STOPPED' : 'COMPLETED'
      logger.success(
        `Pipeline ${finalStatus} — found=${pipelineResult.leadsFound} saved=${pipelineResult.leadsSaved} analyzed=${pipelineResult.leadsAnalyzed} msgs=${pipelineResult.messagesGenerated}`,
        'engine',
      )
      broadcastDone(
        `${finalStatus}: ${pipelineResult.leadsSaved} leads saved, ${pipelineResult.leadsAnalyzed} analyzed, ${pipelineResult.messagesGenerated} messages generated`,
      )
    } else {
      // ── Standard scrape mode (original behavior) ──────────────────────────
      const leads = await scrapeMultiSource(
        payload.sources as ScraperSource[],
        payload.niche,
        payload.city,
        payload.daily_cap ?? runCfg.maxResults,
        runCfg,
        () => state.stopRequested,
        async (lead) => {
          state.leadsFound++
          emitState()
          const result = await saveLead(lead)
          if (result.success) {
            if (result.is_new && payload.channel) {
              await updateLeadChannel(result.id, payload.channel)
            }
            state.leadsSaved++
            broadcastLead(lead)
            logger.success(`Saved: ${lead.business_name}`, 'engine')
          } else {
            state.leadsFailed++
            logger.warn(`Save failed: ${lead.business_name}`, 'engine')
          }
          emitState()
        },
      )

      const finalStatus = state.stopRequested ? 'STOPPED' : 'COMPLETED'
      logger.success(`Engine ${finalStatus} — ${leads.length} found, ${state.leadsSaved} saved`, 'engine')
      broadcastDone(`${finalStatus}: ${state.leadsSaved} leads saved`)
    }
  } catch (err) {
    logger.error(`Engine crashed: ${err}`, 'engine')
    broadcastDone(`ERROR: ${err}`)
  } finally {
    await closeBrowser()
    state.status        = 'idle'
    state.stopRequested = false
    state.currentSource = null
    state.currentAction = null
    emitState()
  }
}

// ── Express + WebSocket setup ─────────────────────────────────────────────────

const app    = express()
const server = createServer(app)
const wss    = new WebSocketServer({ server })

app.use(cors())
app.use(express.json())

setFastapiUrl(config.fastapiUrl)

// WebSocket connections
wss.on('connection', (ws) => {
  registerWsClient(ws)
  logger.info('WebSocket client connected', 'ws')
  // Send current state immediately on connect
  ws.send(JSON.stringify({ type: 'state', payload: { ...state } }))
})

// ── REST endpoints ────────────────────────────────────────────────────────────

app.get('/engine/health', async (_req, res) => {
  const fastapiOk = await checkFastapiHealth()
  res.json({ status: 'ok', fastapi: fastapiOk, engine: state.status })
})

app.get('/engine/status', (_req, res) => {
  res.json({ ...state, pipelinePhases, ollama: ollamaConfig.baseUrl })
})

app.post('/engine/start', (req, res) => {
  if (state.status === 'running') {
    return res.status(409).json({ error: 'Engine already running' })
  }

  const payload = req.body as StartPayload

  if (!payload.niche || !payload.city || !payload.sources?.length) {
    return res.status(400).json({ error: 'niche, city, and sources are required' })
  }

  const runCfg: ScraperConfig = {
    ...config,
    headless: payload.headless ?? config.headless,
  }

  // Fire-and-forget
  runEngine(payload, runCfg).catch((err) => logger.error(`Unhandled engine error: ${err}`, 'engine'))

  return res.json({ started: true, runId: state.runId })
})

app.post('/engine/stop', (_req, res) => {
  if (state.status !== 'running') {
    return res.status(400).json({ error: 'Engine not running' })
  }
  state.stopRequested = true
  state.status        = 'stopping'
  emitState()
  logger.warn('Stop requested — finishing current lead', 'engine')
  return res.json({ stopping: true })
})

// ── Engine status — includes pipeline phases ──────────────────────────────────

app.get('/engine/pipeline/phases', (_req, res) => {
  res.json({ phases: pipelinePhases, lastResult: lastPipelineResult })
})

// ── POST /engine/analyze — run AI analysis + scoring on existing PENDING leads ──

app.post('/engine/analyze', async (req, res) => {
  if (state.status === 'running') {
    return res.status(409).json({ error: 'Engine is currently running a scrape job' })
  }

  const { lead_ids, limit = 20, run_scoring = true, run_messaging = false } = req.body as {
    lead_ids?: number[]
    limit?: number
    run_scoring?: boolean
    run_messaging?: boolean
  }

  res.json({ started: true, message: 'AI analysis running in background — watch WebSocket for progress' })

  // Fire and forget
  ;(async () => {
    state.status = 'running'
    emitState()

    try {
      let rawLeads: Array<Record<string, unknown>> = []

      if (lead_ids?.length) {
        // Specific lead IDs — fetch each from FastAPI
        const fastapiClient = (await import('axios')).default.create({
          baseURL: config.fastapiUrl, timeout: 10000,
        })
        rawLeads = (
          await Promise.all(
            lead_ids.map((id) =>
              fastapiClient.get(`/api/leads/${id}`).then((r) => r.data).catch(() => null),
            ),
          )
        ).filter(Boolean)
      } else {
        rawLeads = await getLeadsByStatus('PENDING', limit)
      }

      logger.info(`AI Analyzer: processing ${rawLeads.length} leads`, 'analyze')

      let analyzed = 0, scored = 0, messaged = 0

      for (const raw of rawLeads) {
        if (state.stopRequested) break
        const lead = raw as unknown as EnrichedLead

        // Website quality
        if (lead.website) {
          lead.websiteQuality = await checkWebsiteQuality(lead.website)
        }

        // AI analysis
        lead.analysis = await analyzeLead(lead, ollamaConfig)
        analyzed++

        // Scoring
        if (run_scoring) {
          lead.score = scoreLead(lead)
          scored++
        }

        // Messaging
        if (run_messaging) {
          lead.messages = await generateMessages(lead, ollamaConfig)
          messaged++
        }

        // Save AI fields back to FastAPI
        const leadId = Number(raw.id)
        if (leadId && lead.messages) {
          await updateLeadAI(leadId, {
            ai_whatsapp_msg:  lead.messages.whatsapp,
            ai_email_subject: lead.messages.email_subject,
            ai_email_body:    lead.messages.email_body,
            ai_followup_msg:  lead.messages.followup,
          })
        }

        broadcastLead({ ...lead, _savedId: leadId })
        logger.success(
          `Analyzed: ${lead.business_name} → ${lead.analysis?.pitch_angle ?? '?'} | score=${lead.score?.score ?? '?'}`,
          'analyze',
        )
      }

      broadcastDone(`Analysis complete: ${analyzed} analyzed, ${scored} scored, ${messaged} messages generated`)
    } catch (err) {
      logger.error(`Analyze job failed: ${err}`, 'analyze')
      broadcastDone(`Analysis ERROR: ${err}`)
    } finally {
      state.status        = 'idle'
      state.stopRequested = false
      emitState()
    }
  })()

  return
})

// ── GET /engine/ai/status — Ollama connectivity check ────────────────────────

app.get('/engine/ai/status', async (_req, res) => {
  try {
    const axiosLib = (await import('axios')).default
    await axiosLib.get(`${ollamaConfig.baseUrl}/api/tags`, { timeout: 5000 })
    res.json({ connected: true, model: ollamaConfig.model, baseUrl: ollamaConfig.baseUrl })
  } catch {
    res.json({ connected: false, model: ollamaConfig.model, baseUrl: ollamaConfig.baseUrl })
  }
})

// ── Start ─────────────────────────────────────────────────────────────────────

server.listen(PORT, () => {
  logger.success(`Scraper engine running on http://localhost:${PORT}`, 'engine')
  logger.info('WebSocket ready for live log streaming', 'engine')
})

export default server
