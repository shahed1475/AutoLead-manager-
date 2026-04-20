export interface Lead {
  business_name: string
  phone?: string
  email?: string
  website?: string
  niche?: string
  city?: string
  address?: string
  source?: string
  rating?: string
  review_count?: string
}

// ── AI enrichment types ───────────────────────────────────────────────────────

export interface WebsiteQuality {
  hasWebsite: boolean
  isReachable: boolean
  hasSSL: boolean
  hasMobileViewport: boolean
  hasContactInfo: boolean
  hasAnalytics: boolean
  technologies: string[]
  issues: string[]
  quality: 'none' | 'poor' | 'average' | 'good'
  qualityScore: number
}

export interface LeadAnalysis {
  problems: string[]
  opportunities: string[]
  pitch_angle: string
  summary: string
  confidence: 'high' | 'medium' | 'low'
}

export interface OutreachMessages {
  whatsapp: string
  email_subject: string
  email_body: string
  followup: string
}

export interface LeadScore {
  score: number
  priority: 'low' | 'medium' | 'high'
  reasons: string[]
}

export interface EnrichedLead extends Lead {
  _savedId?: number
  websiteQuality?: WebsiteQuality
  analysis?: LeadAnalysis
  score?: LeadScore
  messages?: OutreachMessages
  searchQuery?: string
}

export interface OllamaConfig {
  baseUrl: string
  model: string
  timeout: number
}

export type QuerySource = 'GOOGLE_MAPS' | 'GOOGLE_SEARCH' | 'YELP' | 'YELLOW_PAGES'

export interface SearchQuery {
  query: string
  source: QuerySource
  intent: string
}

export interface PipelinePhase {
  name: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
  count: number
  error?: string
  startedAt?: string
  completedAt?: string
}

export interface PipelineOptions {
  niche: string
  city: string
  channel: string
  sources: ScraperSource[]
  daily_cap?: number
  runAiAnalysis?: boolean
  runScoring?: boolean
  runMessaging?: boolean
  headless?: boolean
}

export interface ScraperConfig {
  headless: boolean
  userDataDir: string
  maxResults: number
  pageDelayMin: number
  pageDelayMax: number
  scrollDelayMin: number
  scrollDelayMax: number
  fastapiUrl: string
}

export type ScraperSource = 'GOOGLE_MAPS' | 'YELP' | 'YELLOW_PAGES'

export type EngineStatus = 'idle' | 'running' | 'stopping' | 'error'

export interface ScraperState {
  status: EngineStatus
  runId: string | null
  niche: string | null
  city: string | null
  channel: string | null
  sources: ScraperSource[]
  leadsFound: number
  leadsSaved: number
  leadsFailed: number
  startedAt: string | null
  stopRequested: boolean
  currentSource: string | null
  currentAction: string | null
}

export interface LogEntry {
  level: 'info' | 'success' | 'warn' | 'error' | 'debug'
  message: string
  timestamp: string
  source?: string
  data?: Record<string, unknown>
}

export interface StartPayload {
  niche: string
  city: string
  channel: string
  sources: ScraperSource[]
  headless?: boolean
  daily_cap?: number
  runAiAnalysis?: boolean
  runScoring?: boolean
  runMessaging?: boolean
}

export interface WsMessage {
  type: 'log' | 'state' | 'lead' | 'done' | 'error'
  payload: LogEntry | ScraperState | Lead | { message: string }
}

export interface SaveLeadResult {
  id: number
  is_new: boolean
  success: boolean
  error?: string
}
