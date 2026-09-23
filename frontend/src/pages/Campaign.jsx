import { useState, useEffect, useRef, Fragment } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Rocket, Square, RefreshCw, Target,
  Radio, Activity, Trash2, MapPin, Bot, Mail,
  MessageSquare, AlertTriangle, Filter,
  TrendingUp, Users, Clock, Globe, Monitor,
  Flame, Send, BarChart3, Zap, FlaskConical, Database,
  Pause, Play, Gauge, Timer, MessageCircle, Check, PenLine,
} from 'lucide-react'
import { campaignApi, engineApi, statsApi, enrichApi, withSessionToken } from '../api/client'
import { useAnimatedNumber } from '../hooks/useAnimatedNumber'
import BackToFindLeads from '../components/BackToFindLeads'
import CampaignHistoryTable from '../components/CampaignHistoryTable'
import toast from 'react-hot-toast'
import clsx from 'clsx'

// ── Constants ─────────────────────────────────────────────────────────────────

const CHANNELS = [
  { id: 'EMAIL',    label: 'Email',    icon: Mail },
  { id: 'WHATSAPP', label: 'WhatsApp', icon: MessageSquare },
  { id: 'BOTH',     label: 'Both',     icon: Send },
]

const SOURCE_GROUPS = [
  {
    label: 'Maps · opens a browser',
    sources: [
      { id: 'GOOGLE_MAPS',  label: 'Google Maps' },
    ],
  },
  {
    label: 'Search engines & directories',
    sources: [
      { id: 'GOOGLE_SEARCH', label: 'Google Search' },
      { id: 'BING_SEARCH',   label: 'Bing Search' },
      { id: 'YELP',          label: 'Yelp' },
      { id: 'YELLOW_PAGES',  label: 'Yellow Pages' },
      { id: 'HOTFROG',       label: 'Hotfrog' },
      { id: 'FOURSQUARE',    label: 'Foursquare' },
      { id: 'TOP_LIST',      label: 'Top Lists' },
      { id: 'GENERIC_DIR',   label: 'Directories' },
    ],
  },
]

// Flat list for helpers that need it
const SOURCE_LIST = SOURCE_GROUPS.flatMap(g => g.sources)

const PIPELINE_STEPS = [
  { n: 1, label: 'Scraping',  icon: Globe },
  { n: 2, label: 'Enriching', icon: Database },
  { n: 3, label: 'Scoring',   icon: Gauge },
  { n: 4, label: 'Writing',   icon: PenLine },
  { n: 5, label: 'Sending',   icon: Send },
]

const LOG_FILTERS = [
  { id: 'all',   label: 'All',    Icon: Activity      },
  { id: 'found', label: 'Found',  Icon: MapPin        },
  { id: 'ai',    label: 'AI',     Icon: Bot           },
  { id: 'sent',  label: 'Sent',   Icon: Mail          },
  { id: 'error', label: 'Errors', Icon: AlertTriangle },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function getLogMeta(msg, level) {
  // Severity comes from the backend's `level` field (ERROR/WARNING/INFO) when
  // present — previously this only sniffed emoji prefixes in the message
  // text, the same log-text-guessing anti-pattern the stage tracker moved
  // away from. Emoji-sniffing remains as a fallback for older/malformed frames.
  if (level === 'ERROR' || level === 'WARNING') return { color: 'text-red-400', type: 'error' }

  if (msg.startsWith('✅'))                                                    return { color: 'text-emerald-400', type: 'found'  }
  if (msg.startsWith('🤖'))                                                    return { color: 'text-blue-400',    type: 'ai'     }
  if (msg.startsWith('📤') || msg.startsWith('💬') || msg.startsWith('📨') ||
      msg.startsWith('🔄'))                                                    return { color: 'text-violet-400',  type: 'sent'   }
  if (msg.startsWith('❌') || msg.startsWith('⚠️'))                           return { color: 'text-red-400',     type: 'error'  }
  if (msg.startsWith('ℹ️'))                                                   return { color: 'text-slate-500',   type: 'info'   }
  return                                                                              { color: 'text-slate-300',   type: 'info'   }
}

// Maps the backend's real campaign_stage (from /api/engine/status) onto the
// 5-step pipeline display — no log-text guessing.
const STAGE_TO_STEP = {
  QUEUED: 0, STARTING: 0,
  SCRAPING: 1, ENRICHING: 2, SCORING: 3, WRITING: 4, SENDING: 5,
  COMPLETED: 5, STOPPED: 5, FAILED: 5,
}

function pipelineStepFromStage(stage, isRunning) {
  if (!isRunning) return 0
  return STAGE_TO_STEP[stage] ?? 1
}

// ── Sub-components ────────────────────────────────────────────────────────────

function RunStatus({ running, paused, niche, city }) {
  const label = !running ? 'Idle' : paused ? 'Paused' : 'Running'
  return (
    <div className="flex items-center gap-2 text-sm min-w-0" role="status">
      <span className={clsx('w-2 h-2 rounded-full shrink-0',
        !running ? 'bg-slate-500' : paused ? 'bg-warning' : 'bg-success animate-pulse')} />
      <span className="font-semibold">{label}</span>
      {running && niche && (
        <span className="text-muted-foreground truncate">· {niche}{city ? `, ${city}` : ''}</span>
      )}
    </div>
  )
}

function Figure({ label, value, detail }) {
  return (
    <div className="min-w-0">
      <dt className="text-meta">{label}</dt>
      <dd className="text-2xl font-bold tabular mt-1 text-foreground">{value}</dd>
      {detail && <dd className="text-meta mt-0.5 truncate">{detail}</dd>}
    </div>
  )
}

function FormSection({ title, aside, children }) {
  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-subheading">{title}</h3>
        {aside && <span className="text-meta">{aside}</span>}
      </div>
      {children}
    </section>
  )
}

function SectionTitle({ title, children }) {
  return (
    <div className="flex items-baseline justify-between gap-4 mb-4">
      <h2 className="text-section">{title}</h2>
      {children && <div className="text-meta flex items-center gap-4">{children}</div>}
    </div>
  )
}

function fmtEta(seconds) {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return null
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

// Live figures for a running campaign (animated counters).
function RunFigures({ leadsFound, leadsSent, messages, leadsPerMin, etaSeconds }) {
  const found = useAnimatedNumber(leadsFound)
  const sent  = useAnimatedNumber(leadsSent)
  const msgs  = useAnimatedNumber(messages)
  return (
    <dl className="grid grid-cols-2 sm:grid-cols-5 gap-6">
      <Figure label="Found" value={found} />
      <Figure label="Sent" value={sent} />
      <Figure label="Messages drafted" value={msgs} />
      <Figure label="Leads per minute" value={leadsPerMin != null ? leadsPerMin.toFixed(1) : '—'} />
      <Figure label="Time left" value={fmtEta(etaSeconds) ?? '—'} />
    </dl>
  )
}

// The five real backend stages as a segmented rail — no log-text guessing.
function PipelineTracker({ isRunning, stage, paused, leadsFound, leadsSent, currentLead }) {
  const currentStep = pipelineStepFromStage(stage, isRunning)
  const campaignDone = !isRunning && leadsSent > 0
  const animFound = useAnimatedNumber(leadsFound)
  const animSent  = useAnimatedNumber(leadsSent)
  const completionPct = leadsFound > 0 ? Math.min(100, Math.round((leadsSent / leadsFound) * 100)) : 0

  function stepState(n) {
    if (campaignDone) return 'done'
    if (!isRunning)   return 'idle'
    if (n < currentStep) return 'done'
    if (n === currentStep) return 'active'
    return 'pending'
  }
  function stepCount(n) {
    const state = stepState(n)
    if (state === 'idle') return null
    if (n === 5) return animSent
    if (state === 'pending') return null
    return animFound
  }

  return (
    <div className="space-y-6">
      {isRunning && paused && (
        <p className="flex items-center gap-2 text-sm text-warning">
          <Pause size={14} /> Paused. The current lead ({stage?.toLowerCase() || 'idle'}) finishes first.
        </p>
      )}
      <ol className="grid grid-cols-5 gap-2">
        {PIPELINE_STEPS.map((step) => {
          const state = stepState(step.n)
          const count = stepCount(step.n)
          const lit = state === 'done' || state === 'active'
          return (
            <li key={step.n} className="min-w-0">
              <div className={clsx('h-1 rounded-full transition-colors duration-500',
                state === 'done' ? 'bg-success' : state === 'active' ? 'bg-primary animate-pulse' : 'bg-secondary')} />
              <div className="mt-3 flex items-center gap-1.5 min-w-0">
                {state === 'done'
                  ? <Check size={14} strokeWidth={2.4} className="text-success shrink-0" />
                  : <step.icon size={14} strokeWidth={1.9} className={clsx('shrink-0', state === 'active' ? 'text-primary' : 'text-muted-foreground')} />}
                <span className={clsx('text-sm truncate', lit ? 'font-semibold text-foreground' : 'text-muted-foreground')}>
                  {step.label}
                </span>
              </div>
              <p className="text-meta tabular mt-0.5">{count !== null ? count : '—'}</p>
            </li>
          )
        })}
      </ol>
      {isRunning && (
        <div className="space-y-2">
          <div className="flex items-baseline justify-between gap-4 text-meta">
            <span className="truncate">
              {currentLead
                ? <>Working on <span className="font-medium text-foreground">{currentLead}</span></>
                : 'Completion'}
            </span>
            <span className="font-semibold text-foreground tabular">{completionPct}%</span>
          </div>
          <div className="h-1.5 rounded-full bg-secondary overflow-hidden">
            <div className="h-full bg-primary rounded-full transition-all duration-700" style={{ width: `${completionPct}%` }} />
          </div>
        </div>
      )}
    </div>
  )
}

function QualityBar({ hot, warm, cold, scope }) {
  const total = hot + warm + cold
  const parts = [['Hot', hot, 'bg-error'], ['Warm', warm, 'bg-warning'], ['Cold', cold, 'bg-info']]
  return (
    <section>
      <SectionTitle title="Lead quality"><span>{scope}</span></SectionTitle>
      <div className="flex h-2 rounded-full overflow-hidden bg-secondary gap-0.5" role="img"
        aria-label={parts.map(([l, n]) => `${l} ${n}`).join(', ')}>
        {total > 0 && parts.map(([l, n, c]) => n > 0 && (
          <div key={l} className={clsx(c, 'h-full transition-all duration-700')} style={{ width: `${(n / total) * 100}%` }} />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-sm">
        {parts.map(([l, n, c]) => (
          <span key={l} className="flex items-center gap-2">
            <span className={clsx('w-2 h-2 rounded-full', c)} />
            <span className="text-muted-foreground">{l}</span>
            <span className="font-semibold tabular">{n}</span>
          </span>
        ))}
      </div>
    </section>
  )
}

// ── Toggle Switch ─────────────────────────────────────────────────────────────

function Toggle({ checked, onChange, disabled, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={clsx(
        'relative w-9 h-5 rounded-full transition-colors duration-200 shrink-0',
        'disabled:opacity-40 disabled:cursor-not-allowed',
        checked ? 'bg-primary' : 'bg-slate-600/60',
      )}
    >
      <span className={clsx(
        'absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-transform duration-200',
        checked && 'translate-x-4',
      )} />
    </button>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function Campaign() {
  const qc = useQueryClient()

  // ── Form state ────────────────────────────────────────────────────────────
  const [niche,       setNiche]       = useState('')
  const [city,        setCity]        = useState('')
  const [country,     setCountry]     = useState('')
  const [channel,     setChannel]     = useState('EMAIL')
  const [sources,     setSources]     = useState(['GOOGLE_MAPS', 'GOOGLE_SEARCH'])
  const [hotWarmOnly, setHotWarmOnly] = useState(true)
  const [dailyCap,    setDailyCap]    = useState(30)
  const [headless,    setHeadless]    = useState(false)
  const [logFilter,   setLogFilter]   = useState('all')
  const [logs,        setLogs]        = useState([])
  const [sseLive,     setSseLive]     = useState(false)

  const logEndRef = useRef(null)
  const esRef     = useRef(null)
  const retryRef  = useRef(null)

  function toggleSource(id) {
    setSources(prev =>
      prev.includes(id)
        ? prev.length === 1 ? prev : prev.filter(s => s !== id)
        : [...prev, id]
    )
  }

  // ── Queries ───────────────────────────────────────────────────────────────

  const { data: engine, refetch: refetchEngine } = useQuery({
    queryKey: ['engineStatus'],
    queryFn:  () => engineApi.status(),
    refetchInterval: 3000,
  })

  const { data: stats } = useQuery({
    queryKey: ['campaignPageStats'],
    queryFn:  () => statsApi.dashboard(),
    refetchInterval: 5000,
  })

  const { data: history, isFetching: historyLoading, refetch: refetchHistory } = useQuery({
    queryKey: ['campaignHistory'],
    queryFn:  () => campaignApi.history(),
    refetchInterval: 10000,
  })

  const { data: scoreDist } = useQuery({
    queryKey: ['scoreDist'],
    queryFn:  enrichApi.scoreDist,
    refetchInterval: 15000,
  })

  const isRunning = Boolean(engine?.campaign_running)
  const isPaused  = Boolean(engine?.campaign_paused)

  // ── Duplicate a past run — prefill the Start form, no backend call needed ──
  function handleDuplicateRun(run) {
    if (isRunning) { toast.error('Stop the running campaign before duplicating another'); return }
    setNiche(run.niche || '')
    setCity(run.city || '')
    setCountry(run.country || '')
    setChannel(run.channel || 'EMAIL')
    if (run.sources) setSources(run.sources.split(',').filter(Boolean))
    if (run.daily_cap) setDailyCap(run.daily_cap)
    toast.success('Start form pre-filled — review and launch')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  // ── Mirror running campaign params back into form ─────────────────────────

  useEffect(() => {
    if (!engine?.campaign_running) return
    if (engine.campaign_niche)             setNiche(engine.campaign_niche)
    if (engine.campaign_city)              setCity(engine.campaign_city)
    if (engine.campaign_country)           setCountry(engine.campaign_country)
    if (engine.campaign_channel)           setChannel(engine.campaign_channel)
    if (engine.campaign_sources?.length)   setSources(engine.campaign_sources)
    if (engine.campaign_hot_warm_only !== undefined) setHotWarmOnly(Boolean(engine.campaign_hot_warm_only))
  }, [engine?.campaign_running]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Completion toast — fires once when a running campaign stops polling as running ──
  const wasRunningRef = useRef(false)
  useEffect(() => {
    if (wasRunningRef.current && !isRunning) {
      const stage = (engine?.campaign_stage || '').toUpperCase()
      const found = engine?.campaign_leads_found ?? 0
      const sent  = engine?.campaign_leads_sent ?? 0
      if (stage === 'FAILED') {
        toast.error('Campaign failed — check the live log for details')
      } else if (stage === 'STOPPED') {
        toast(`Campaign stopped — ${sent} sent of ${found} found`, { icon: '⏹️' })
      } else {
        toast.success(`Campaign completed — ${sent} sent of ${found} found`)
      }
      qc.invalidateQueries({ queryKey: ['campaignHistory'] })
    }
    wasRunningRef.current = isRunning
  }, [isRunning, engine?.campaign_stage, engine?.campaign_leads_found, engine?.campaign_leads_sent, qc])

  // ── SSE with auto-reconnect ───────────────────────────────────────────────

  useEffect(() => {
    let es

    function connect() {
      if (es) es.close()
      es = new EventSource(withSessionToken('/api/logs/stream'))
      esRef.current = es

      es.onopen = () => { setSseLive(true); clearTimeout(retryRef.current) }
      es.onerror = () => {
        setSseLive(false)
        es.close()
        retryRef.current = setTimeout(connect, 3000)
      }
      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          const { color, type } = getLogMeta(data.message, data.level)
          setLogs((prev) => [
            ...prev.slice(-499),
            { message: data.message, ts: data.timestamp || '', color, type },
          ])
        } catch { /* ignore malformed frames */ }
      }
    }

    connect()
    return () => { es?.close(); clearTimeout(retryRef.current) }
  }, [])

  useEffect(() => {
    // Scroll only the log box — scrollIntoView would also scroll the page.
    const box = logEndRef.current?.parentElement
    if (box) box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' })
  }, [logs])

  // ── Derived state ─────────────────────────────────────────────────────────

  const hotWarmCount   = (stats?.hot_leads ?? 0) + (stats?.warm_leads ?? 0)
  const leadsFound     = engine?.campaign_leads_found ?? 0
  const leadsSent      = engine?.campaign_leads_sent  ?? 0
  const needsBrowser   = sources.includes('GOOGLE_MAPS')
  const hasRunHistory  = (history?.length ?? 0) > 0
  const dbEmpty        = !isRunning && hasRunHistory && (stats?.total_leads ?? 0) === 0

  const canStart = (
    niche.trim() !== '' &&
    city.trim()  !== '' &&
    !isRunning           &&
    sources.length > 0   &&
    dailyCap > 0
  )

  const logCounts = logs.reduce((acc, l) => {
    acc[l.type] = (acc[l.type] || 0) + 1
    return acc
  }, {})
  const filteredLogs = logFilter === 'all' ? logs : logs.filter(l => l.type === logFilter)

  // ── Mutations ─────────────────────────────────────────────────────────────

  const startMut = useMutation({
    mutationFn: () => campaignApi.start({
      niche,
      city,
      country:       country || undefined,
      channel,
      daily_cap:     dailyCap,
      // No sources: the backend's Discovery Planner picks them for the niche.
      headless,
      hot_warm_only: hotWarmOnly,
    }),
    onSuccess: (data) => {
      refetchEngine()
      refetchHistory()
      toast.success(`Campaign started — run #${data.run_id}`)
    },
    onError: (e) => toast.error(e.message),
  })

  const stopMut = useMutation({
    mutationFn: () => campaignApi.stop(),
    onSuccess: () => { refetchEngine(); toast.success('Stop signal sent') },
    onError: (e) => toast.error(e.message),
  })

  const pauseMut = useMutation({
    mutationFn: () => campaignApi.pause(),
    onSuccess: () => { refetchEngine(); toast.success('Campaign paused') },
    onError: (e) => toast.error(e.message),
  })

  const resumeMut = useMutation({
    mutationFn: () => campaignApi.resume(),
    onSuccess: () => { refetchEngine(); toast.success('Campaign resumed') },
    onError: (e) => toast.error(e.message),
  })

  const testMut = useMutation({
    mutationFn: () => campaignApi.testPipeline(),
    onSuccess: (data) => {
      if (data.result?.ok) {
        toast.success(`DB OK — ${data.result.saved}/5 test leads saved (${data.result.total_leads} total in DB)`)
      } else {
        const errs = data.result?.errors?.join(', ') || 'unknown error'
        toast.error(`DB write failed: ${errs}`)
      }
    },
    onError: (e) => toast.error(`Test failed: ${e.message}`),
  })

  // ── Render ────────────────────────────────────────────────────────────────

  // scoreDist is the distribution across every scored lead (not just this run).
  const quality = {
    hot: scoreDist?.HOT ?? stats?.hot_leads ?? 0,
    warm: scoreDist?.WARM ?? stats?.warm_leads ?? 0,
    cold: scoreDist?.COLD ?? stats?.cold_leads ?? 0,
    scope: 'All scored leads',
  }

  return (
    <div className="px-4 sm:px-8 py-8 max-w-[1400px] mx-auto space-y-10">

      {/* ── Page header ──────────────────────────────────────────────────── */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <BackToFindLeads />
          <h1 className="text-page">Outreach campaign</h1>
          <p className="text-support mt-1">
            Finds leads, scores them, writes messages and sends them automatically. To review
            messages before anything is sent, use Find leads with “Write outreach” instead.
          </p>
        </div>
        <RunStatus
          running={isRunning}
          paused={isPaused}
          niche={engine?.campaign_niche}
          city={engine?.campaign_city}
        />
      </header>

      {/* ── DB empty warning (shows after a run with 0 leads in DB) ───── */}
      {dbEmpty && (
        <div className="flex items-start gap-3 rounded-xl bg-warning/10 px-4 py-3">
          <Database size={16} className="shrink-0 mt-0.5 text-warning" />
          <div className="text-sm min-w-0">
            <p className="font-semibold text-foreground">Campaigns ran, but no leads were saved</p>
            <p className="text-muted-foreground mt-0.5">
              This usually means the database was busy and rejected the writes. Use{' '}
              <span className="font-medium text-foreground">Test database connection</span> to check. If the
              test passes, restart the backend to apply the busy-timeout fix.
            </p>
          </div>
        </div>
      )}

      <div className="grid gap-10 lg:grid-cols-[360px_minmax(0,1fr)] items-start">

        {/* ══════ LEFT — campaign setup (the one raised surface) ══════ */}
        <div className="surface-raised p-6 space-y-7 lg:sticky lg:top-8">

          <FormSection title="Target">
            <div className="space-y-3">
              <div>
                <label htmlFor="campaign-niche" className="label">Niche</label>
                <input id="campaign-niche" className="input" placeholder="e.g. dental clinic"
                  value={niche} onChange={e => setNiche(e.target.value)} disabled={isRunning} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="campaign-city" className="label">City</label>
                  <input id="campaign-city" className="input" placeholder="e.g. Dubai"
                    value={city} onChange={e => setCity(e.target.value)} disabled={isRunning} />
                </div>
                <div>
                  <label htmlFor="campaign-country" className="label">Country</label>
                  <input id="campaign-country" className="input" placeholder="Optional"
                    value={country} onChange={e => setCountry(e.target.value)} disabled={isRunning} />
                </div>
              </div>
              <p className="text-meta">We choose the best places to look for this kind of business.</p>
            </div>
          </FormSection>

          <FormSection title="Daily limit">
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={1} max={500}
                aria-label="Daily limit (total leads)"
                value={dailyCap}
                onChange={e => setDailyCap(Math.max(1, Number(e.target.value)))}
                disabled={isRunning}
                className="input w-24 text-center tabular"
              />
              <p className="text-meta">
                leads a day
              </p>
            </div>
          </FormSection>

          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="text-subheading">Only hot and warm leads</p>
              <p className="text-meta mt-0.5">
                {hotWarmOnly
                  ? <>About <span className="font-semibold text-foreground tabular">{hotWarmCount}</span> emails will go out</>
                  : 'Skip leads scored as cold'}
              </p>
            </div>
            <Toggle checked={hotWarmOnly} onChange={setHotWarmOnly} disabled={isRunning} label="Only hot and warm leads" />
          </div>

          <FormSection title="Channel">
            <div className="grid grid-cols-3 gap-1 p-1 rounded-xl bg-secondary" role="radiogroup" aria-label="Outreach channel">
              {CHANNELS.map(({ id, label, icon: ChannelIcon }) => (
                <button
                  key={id}
                  type="button"
                  role="radio"
                  aria-checked={channel === id}
                  onClick={() => setChannel(id)}
                  disabled={isRunning}
                  className={clsx(
                    'flex items-center justify-center gap-1.5 h-8 rounded-lg text-sm transition-all',
                    'disabled:opacity-40 disabled:cursor-not-allowed',
                    channel === id
                      ? 'bg-surface-elevated text-foreground font-semibold shadow-sm'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  <ChannelIcon size={14} strokeWidth={1.9} />
                  {label}
                </button>
              ))}
            </div>
          </FormSection>

          <div className="space-y-2 pt-5 border-t border-border-subtle">
            {!isRunning ? (
              <button
                onClick={() => startMut.mutate()}
                disabled={!canStart || startMut.isPending}
                className="btn-primary w-full h-10"
              >
                {startMut.isPending
                  ? <><RefreshCw size={15} className="animate-spin" /> Starting…</>
                  : <><Play size={15} /> Start campaign</>}
              </button>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                <button
                  onClick={() => (isPaused ? resumeMut.mutate() : pauseMut.mutate())}
                  disabled={pauseMut.isPending || resumeMut.isPending}
                  className="btn-secondary w-full h-10"
                >
                  {(pauseMut.isPending || resumeMut.isPending)
                    ? <RefreshCw size={14} className="animate-spin" />
                    : isPaused ? <Play size={14} /> : <Pause size={14} />}
                  {isPaused ? 'Resume' : 'Pause'}
                </button>
                <button
                  onClick={() => stopMut.mutate()}
                  disabled={stopMut.isPending}
                  className="btn-danger w-full h-10"
                >
                  {stopMut.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Square size={13} />}
                  Stop
                </button>
              </div>
            )}

            {!niche.trim() && !isRunning && (
              <p className="text-meta text-center">Enter a niche and city to start.</p>
            )}

            <button
              onClick={() => testMut.mutate()}
              disabled={isRunning || testMut.isPending}
              title="Insert 5 dummy leads to verify the database write path works"
              className="btn-ghost w-full h-8 text-xs"
            >
              {testMut.isPending
                ? <><RefreshCw size={12} className="animate-spin" /> Testing database…</>
                : <><FlaskConical size={12} /> Test database connection</>}
            </button>
          </div>
        </div>

        {/* ══════ RIGHT — progress, quality, live feed, history (open) ══════ */}
        <div className="space-y-12 min-w-0">

          <section>
            <SectionTitle title={isRunning ? 'This run' : 'Today'}>
              {!isRunning && <span>Live figures appear while a campaign runs</span>}
            </SectionTitle>
            {isRunning ? (
              <RunFigures
                leadsFound={leadsFound}
                leadsSent={leadsSent}
                messages={engine?.campaign_messages_generated ?? 0}
                leadsPerMin={engine?.campaign_leads_per_min}
                etaSeconds={engine?.campaign_eta_seconds}
              />
            ) : (
              <dl className="grid grid-cols-2 sm:grid-cols-4 gap-6">
                <Figure label="Emails sent" value={stats?.email_sent_today ?? 0} />
                <Figure label="WhatsApp sent" value={stats?.whatsapp_sent_today ?? 0} />
                <Figure label="Total leads" value={stats?.total_leads ?? '—'} detail={`${stats?.pending ?? 0} pending`} />
                <Figure label="Replies" value={stats?.replied ?? '—'} detail={stats ? `${stats.reply_rate}% reply rate` : undefined} />
              </dl>
            )}
          </section>

          <section>
            <SectionTitle title="Progress">
              {!isRunning && <span>Every run moves through these five steps</span>}
            </SectionTitle>
            <PipelineTracker
              isRunning={isRunning}
              stage={engine?.campaign_stage}
              paused={Boolean(engine?.campaign_paused)}
              leadsFound={leadsFound}
              leadsSent={leadsSent}
              currentLead={engine?.campaign_current_lead}
            />
          </section>

          <QualityBar {...quality} />

          {/* ── Live feed (stays a dark console in both themes) ─────────── */}
          <section>
            <SectionTitle title="Live feed">
              <span className="tabular">{logs.length} entries</span>
              <span className="flex items-center gap-1.5">
                <span className={clsx('w-1.5 h-1.5 rounded-full', sseLive ? 'bg-success animate-pulse' : 'bg-error')} />
                {sseLive ? 'Live' : 'Reconnecting'}
              </span>
              <button onClick={() => setLogs([])} className="btn-ghost h-7 px-2 text-xs">
                <Trash2 size={12} /> Clear
              </button>
            </SectionTitle>
            <div data-theme="dark" className="rounded-2xl bg-[#0E0E0D] border border-slate-800 overflow-hidden">
              <div className="flex items-center gap-1 px-3 py-2 border-b border-slate-800 overflow-x-auto" role="tablist">
                {LOG_FILTERS.map(({ id, label }) => {
                  const count = id === 'all' ? logs.length : (logCounts[id] || 0)
                  return (
                    <button
                      key={id}
                      role="tab"
                      aria-selected={logFilter === id}
                      onClick={() => setLogFilter(id)}
                      className={clsx(
                        'h-7 px-2.5 rounded-md text-xs font-medium transition-colors whitespace-nowrap',
                        logFilter === id ? 'bg-slate-800 text-slate-100' : 'text-slate-500 hover:text-slate-300',
                      )}
                    >
                      {label}
                      {count > 0 && <span className="ml-1.5 tabular text-slate-500">{count}</span>}
                    </button>
                  )
                })}
              </div>

              <div className="h-[380px] overflow-y-auto p-4 space-y-0.5 font-mono text-xs leading-5">
                {filteredLogs.length === 0 && (
                  <p className="text-slate-500 select-none font-sans text-sm">
                    {logs.length === 0
                      ? 'Nothing yet. Start a campaign and each step will stream here.'
                      : `No "${logFilter}" events yet.`}
                  </p>
                )}

                {filteredLogs.map((entry, i) => (
                  <div key={i} className="flex gap-3 rounded px-1 -mx-1 hover:bg-slate-900 transition-colors">
                    <span className="text-slate-600 shrink-0 tabular w-16 text-right select-none">{entry.ts}</span>
                    <span className={clsx('flex-1 break-words', entry.color)}>{entry.message}</span>
                  </div>
                ))}
                <div ref={logEndRef} />
              </div>
            </div>
          </section>

          <section>
            <CampaignHistoryTable
              history={history}
              isLoading={historyLoading}
              onRefresh={refetchHistory}
              onDuplicate={handleDuplicateRun}
            />
          </section>
        </div>
      </div>
    </div>
  )
}
