import { useState, useEffect, useRef, Fragment } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Rocket, Square, RefreshCw, Target, History,
  Radio, Activity, Trash2, MapPin, Bot, Mail,
  MessageSquare, AlertTriangle, Filter,
  TrendingUp, Users, Clock, Globe, Monitor,
  Flame, Send, BarChart3, Zap,
} from 'lucide-react'
import { campaignApi, engineApi, statsApi, enrichApi } from '../api/client'
import toast from 'react-hot-toast'
import clsx from 'clsx'

// ── Constants ─────────────────────────────────────────────────────────────────

const CHANNELS = [
  { id: 'EMAIL',    label: 'Email',    emoji: '📧' },
  { id: 'WHATSAPP', label: 'WhatsApp', emoji: '💬' },
  { id: 'BOTH',     label: 'Both',     emoji: '📨' },
]

const SOURCE_LIST = [
  { id: 'GOOGLE_MAPS',   label: 'Google Maps',   emoji: '🗺️', estimate: 60, hasCap: true  },
  { id: 'GOOGLE_SEARCH', label: 'Google Search',  emoji: '🔍', estimate: 40, hasCap: true  },
  { id: 'YELLOW_PAGES',  label: 'Yellow Pages',   emoji: '📒', estimate: 20, hasCap: false },
]

const PIPELINE_STEPS = [
  { n: 1, label: 'Scraping',  icon: '🕷️' },
  { n: 2, label: 'Enriching', icon: '🔍' },
  { n: 3, label: 'Scoring',   icon: '📊' },
  { n: 4, label: 'Writing',   icon: '✍️' },
  { n: 5, label: 'Sending',   icon: '📤' },
]

const CHANNEL_ACTIVE = {
  EMAIL:    'border-blue-500/60 bg-blue-500/15 text-blue-300',
  WHATSAPP: 'border-green-500/60 bg-green-500/15 text-green-300',
  BOTH:     'border-teal-500/60 bg-teal-500/15 text-teal-300',
}

const CHANNEL_BADGE = {
  EMAIL:    'badge-email',
  WHATSAPP: 'badge-whatsapp',
  BOTH:     'badge bg-teal-500/20 text-teal-400 border border-teal-500/30',
}

const RUN_STATUS_CLS = {
  RUNNING:   'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
  COMPLETED: 'bg-blue-500/20 text-blue-400 border border-blue-500/30',
  STOPPED:   'bg-amber-500/20 text-amber-400 border border-amber-500/30',
  FAILED:    'bg-red-500/20 text-red-400 border border-red-500/30',
}

const LOG_FILTERS = [
  { id: 'all',   label: 'All',    Icon: Activity      },
  { id: 'found', label: 'Found',  Icon: MapPin        },
  { id: 'ai',    label: 'AI',     Icon: Bot           },
  { id: 'sent',  label: 'Sent',   Icon: Mail          },
  { id: 'error', label: 'Errors', Icon: AlertTriangle },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function getLogMeta(msg) {
  if (msg.startsWith('✅'))                                                    return { color: 'text-emerald-400', type: 'found'  }
  if (msg.startsWith('🤖'))                                                    return { color: 'text-blue-400',    type: 'ai'     }
  if (msg.startsWith('📤') || msg.startsWith('💬') || msg.startsWith('📨') ||
      msg.startsWith('🔄'))                                                    return { color: 'text-violet-400',  type: 'sent'   }
  if (msg.startsWith('❌') || msg.startsWith('⚠️'))                           return { color: 'text-red-400',     type: 'error'  }
  if (msg.startsWith('ℹ️'))                                                   return { color: 'text-slate-500',   type: 'info'   }
  return                                                                              { color: 'text-slate-300',   type: 'info'   }
}

function fmtRunDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso + 'Z')
  return (
    d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) +
    ' · ' +
    d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  )
}

function fmtDuration(startIso, endIso) {
  if (!startIso) return '—'
  const s   = new Date(startIso + 'Z')
  const e   = endIso ? new Date(endIso + 'Z') : new Date()
  const sec = Math.max(0, Math.round((e - s) / 1000))
  if (sec < 60)   return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
}

// Derive which pipeline step is currently active based on recent log messages
function derivePipelineStep(logs, isRunning) {
  if (!isRunning) return 0
  const recent = logs.slice(-15)
  for (let i = recent.length - 1; i >= 0; i--) {
    const t = recent[i].type
    if (t === 'sent')  return 5
    if (t === 'ai')    return 4
    if (t === 'found') return 1
  }
  return 1 // running but no logs = scraping phase
}

// ── Sub-components ────────────────────────────────────────────────────────────

function EngineStatusBadge({ running, niche, city }) {
  return (
    <div className={clsx(
      'flex items-center gap-2.5 px-4 py-2 rounded-xl border text-xs font-mono font-bold uppercase tracking-widest transition-all duration-500',
      running
        ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400 shadow-lg shadow-emerald-950/40'
        : 'bg-slate-800/60 border-slate-700/50 text-slate-500',
    )}>
      <div className={clsx(
        'w-2.5 h-2.5 rounded-full flex-shrink-0 transition-all',
        running ? 'bg-emerald-400 animate-pulse shadow-lg shadow-emerald-500/50' : 'bg-slate-600',
      )} />
      {running ? 'ENGINE RUNNING' : 'ENGINE IDLE'}
      {running && niche && (
        <span className="text-emerald-600/80 font-normal normal-case tracking-normal ml-0.5">
          — {niche}{city ? `, ${city}` : ''}
        </span>
      )}
    </div>
  )
}

function StatBox({ label, value, colorClass }) {
  return (
    <div className="bg-slate-900/70 border border-slate-700/40 rounded-xl p-3 text-center">
      <p className="text-[10px] text-slate-500 uppercase tracking-widest mb-1">{label}</p>
      <p className={`text-2xl font-mono font-bold tabular-nums ${colorClass}`}>{value}</p>
    </div>
  )
}

function SessionStat({ label, value, Icon, colorClass }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="flex items-center gap-1.5 text-slate-600">
        <Icon size={10} />
        {label}
      </span>
      <span className={`font-mono font-bold tabular-nums ${colorClass}`}>{value}</span>
    </div>
  )
}

// ── Pipeline Progress Card ────────────────────────────────────────────────────

function PipelineCard({ isRunning, logs, leadsFound, leadsSent, scoreDist }) {
  const currentStep = derivePipelineStep(logs, isRunning)
  const campaignDone = !isRunning && leadsSent > 0

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
    if (n === 5) return leadsSent
    if (state === 'pending') return null
    return leadsFound
  }

  const hot  = scoreDist?.HOT  ?? 0
  const warm = scoreDist?.WARM ?? 0
  const cold = scoreDist?.COLD ?? 0
  const distTotal = hot + warm + cold || 1
  const hasDistData = hot + warm + cold > 0

  return (
    <div className="card p-4 space-y-4 shrink-0">

      {/* ── 5-step pipeline ─────────────────────────────────────────────── */}
      <div className="flex items-start">
        {PIPELINE_STEPS.map((step, idx) => {
          const state = stepState(step.n)
          const count = stepCount(step.n)
          const prevDone = idx > 0 && stepState(step.n - 1) === 'done'

          return (
            <Fragment key={step.n}>
              {idx > 0 && (
                <div className={clsx(
                  'h-px mt-[18px] shrink-0 transition-colors duration-500',
                  'flex-1 max-w-[40px]',
                  prevDone ? 'bg-emerald-500/50' : 'bg-slate-700/40',
                )} />
              )}

              <div className="flex flex-col items-center gap-1.5 min-w-[60px]">
                {/* Icon circle */}
                <div className={clsx(
                  'w-9 h-9 rounded-full flex items-center justify-center text-base transition-all duration-500',
                  state === 'done'
                    ? 'bg-emerald-500/15 ring-1 ring-emerald-500/50 shadow-sm shadow-emerald-900/40'
                    : state === 'active'
                    ? 'bg-brand-500/15 ring-2 ring-brand-500/60 animate-pulse shadow-lg shadow-brand-900/30'
                    : state === 'pending'
                    ? 'bg-slate-800/60 ring-1 ring-slate-700/30'
                    : 'bg-slate-800/30 ring-1 ring-slate-800/50',
                )}>
                  {state === 'done' ? '✅' : step.icon}
                </div>

                {/* Label */}
                <span className={clsx(
                  'text-[9px] font-bold uppercase tracking-wider text-center',
                  state === 'done'    ? 'text-emerald-400' :
                  state === 'active'  ? 'text-brand-400'   :
                  state === 'pending' ? 'text-slate-600'   :
                  'text-slate-700',
                )}>
                  {step.label}
                </span>

                {/* Count */}
                <span className={clsx(
                  'text-[11px] font-mono font-bold tabular-nums',
                  state === 'done'   ? 'text-emerald-300' :
                  state === 'active' ? 'text-brand-300'   :
                  'text-slate-700',
                )}>
                  {count !== null ? count : '—'}
                </span>
              </div>
            </Fragment>
          )
        })}
      </div>

      {/* ── Score distribution bars ──────────────────────────────────────── */}
      {hasDistData && (
        <div className="pt-3 border-t border-slate-800/60">
          <p className="text-[9px] text-slate-600 uppercase tracking-widest font-bold mb-2.5 flex items-center gap-1.5">
            <BarChart3 size={9} />
            Lead Quality Distribution
          </p>
          <div className="flex items-center gap-4">
            {[
              { label: 'HOT',  emoji: '🔥', n: hot,  bar: 'bg-red-500/70'   },
              { label: 'WARM', emoji: '♨️', n: warm, bar: 'bg-amber-500/70' },
              { label: 'COLD', emoji: '❄️', n: cold, bar: 'bg-blue-400/25', note: '(skipped)' },
            ].map(({ label, emoji, n, bar, note }) => (
              <div key={label} className="flex-1 space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-slate-500">
                    {emoji} <span className="font-medium">{label}</span>
                    {note && <span className="text-slate-700 ml-1">{note}</span>}
                  </span>
                  <span className="text-[10px] font-mono font-bold text-slate-400 tabular-nums">{n}</span>
                </div>
                <div className="h-1.5 bg-slate-800/80 rounded-full overflow-hidden">
                  <div
                    className={`h-full ${bar} rounded-full transition-all duration-700`}
                    style={{ width: `${Math.round((n / distTotal) * 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Toggle Switch ─────────────────────────────────────────────────────────────

function Toggle({ checked, onChange, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={clsx(
        'relative w-9 h-5 rounded-full border transition-all duration-200 shrink-0',
        'disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none',
        checked
          ? 'bg-orange-500/30 border-orange-500/50'
          : 'bg-slate-700/60 border-slate-600/50',
      )}
    >
      <span className={clsx(
        'absolute top-0.5 w-4 h-4 rounded-full transition-all duration-200',
        checked ? 'left-[18px] bg-orange-400 shadow-sm' : 'left-0.5 bg-slate-500',
      )} />
    </button>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function Campaign() {
  const qc = useQueryClient()

  // ── Form state ────────────────────────────────────────────────────────────
  const [niche,           setNiche]           = useState('')
  const [city,            setCity]            = useState('')
  const [country,         setCountry]         = useState('')
  const [channel,         setChannel]         = useState('EMAIL')
  const [sources,         setSources]         = useState(['GOOGLE_MAPS', 'GOOGLE_SEARCH'])
  const [hotWarmOnly,     setHotWarmOnly]     = useState(true)
  const [googleMapsCap,   setGoogleMapsCap]   = useState(20)
  const [googleSearchCap, setGoogleSearchCap] = useState(15)
  const [headless,        setHeadless]        = useState(false)
  const [logFilter,       setLogFilter]       = useState('all')
  const [logs,            setLogs]            = useState([])
  const [sseLive,         setSseLive]         = useState(false)

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

  const { data: history, refetch: refetchHistory } = useQuery({
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

  // ── SSE with auto-reconnect ───────────────────────────────────────────────

  useEffect(() => {
    let es

    function connect() {
      if (es) es.close()
      es = new EventSource('/api/logs/stream')
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
          const { color, type } = getLogMeta(data.message)
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
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  // ── Derived state ─────────────────────────────────────────────────────────

  const estimatedLeads = SOURCE_LIST
    .filter(s => sources.includes(s.id))
    .reduce((sum, s) => sum + s.estimate, 0)

  const totalCap = (
    (sources.includes('GOOGLE_MAPS')   ? googleMapsCap   : 0) +
    (sources.includes('GOOGLE_SEARCH') ? googleSearchCap : 0) +
    (sources.includes('YELLOW_PAGES')  ? 15              : 0)
  )

  const hotWarmCount = (stats?.hot_leads ?? 0) + (stats?.warm_leads ?? 0)
  const leadsFound   = engine?.campaign_leads_found ?? 0
  const leadsSent    = engine?.campaign_leads_sent  ?? 0

  const canStart = (
    niche.trim() !== '' &&
    city.trim()  !== '' &&
    !isRunning           &&
    sources.length > 0   &&
    totalCap > 0
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
      country:           country || undefined,
      channel,
      daily_cap:         totalCap,
      sources,
      headless,
      hot_warm_only:     hotWarmOnly,
      google_maps_cap:   sources.includes('GOOGLE_MAPS')   ? googleMapsCap   : undefined,
      google_search_cap: sources.includes('GOOGLE_SEARCH') ? googleSearchCap : undefined,
    }),
    onSuccess: (data) => {
      refetchEngine()
      refetchHistory()
      toast.success(`Engine launched — Run #${data.run_id}`)
    },
    onError: (e) => toast.error(e.message),
  })

  const stopMut = useMutation({
    mutationFn: () => campaignApi.stop(),
    onSuccess: () => { refetchEngine(); toast.success('Stop signal sent') },
    onError: (e) => toast.error(e.message),
  })

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 h-full flex flex-col gap-5 overflow-hidden">

      {/* ── Page header ──────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-xl font-bold text-slate-100 flex items-center gap-2.5">
            <Rocket size={20} className="text-emerald-400" />
            Mission Control
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Automated lead scraping · AI generation · multi-source outreach
          </p>
        </div>
        <EngineStatusBadge
          running={isRunning}
          niche={engine?.campaign_niche}
          city={engine?.campaign_city}
        />
      </div>

      {/* ── Main layout ──────────────────────────────────────────────────── */}
      <div className="flex gap-5 flex-1 min-h-0">

        {/* ══════ LEFT — Control Panel ══════ */}
        <div className="w-80 shrink-0 flex flex-col gap-4 overflow-y-auto pr-0.5">

          {/* Config card */}
          <div className="card p-5 flex flex-col gap-4">

            {/* Live stat boxes */}
            <div className="grid grid-cols-2 gap-2">
              {isRunning ? (
                <>
                  <StatBox label="Found" value={leadsFound} colorClass="text-emerald-400" />
                  <StatBox label="Sent"  value={leadsSent}  colorClass="text-violet-400"  />
                </>
              ) : (
                <>
                  <StatBox label="Email Today" value={stats?.email_sent_today    ?? 0} colorClass="text-blue-400"    />
                  <StatBox label="WA Today"    value={stats?.whatsapp_sent_today ?? 0} colorClass="text-emerald-400" />
                </>
              )}
            </div>

            {/* ── Target Inputs ─────────────────────────────────────────── */}
            <div className="space-y-3">
              <div>
                <label className="label flex items-center gap-1.5">
                  <Target size={10} /> Target Niche
                </label>
                <input
                  className="input text-sm"
                  placeholder="e.g. dental clinic"
                  value={niche}
                  onChange={e => setNiche(e.target.value)}
                  disabled={isRunning}
                />
              </div>

              <div>
                <label className="label flex items-center gap-1.5">
                  <MapPin size={10} /> Target City
                </label>
                <input
                  className="input text-sm"
                  placeholder="e.g. Dubai"
                  value={city}
                  onChange={e => setCity(e.target.value)}
                  disabled={isRunning}
                />
              </div>

              <div>
                <label className="label flex items-center gap-1.5">
                  <Globe size={10} /> Country
                </label>
                <input
                  className="input text-sm"
                  placeholder="e.g. UAE (optional)"
                  value={country}
                  onChange={e => setCountry(e.target.value)}
                  disabled={isRunning}
                />
              </div>
            </div>

            {/* ── Lead Sources ──────────────────────────────────────────── */}
            <div>
              <label className="label flex items-center gap-1.5">
                <Zap size={10} /> Lead Sources
              </label>
              <div className="space-y-2">
                {SOURCE_LIST.map(({ id, label, emoji }) => {
                  const checked = sources.includes(id)
                  const onlyOne = checked && sources.length === 1
                  return (
                    <label
                      key={id}
                      className={clsx(
                        'flex items-center gap-2.5 cursor-pointer group rounded-lg px-2.5 py-1.5 transition-colors',
                        'border border-transparent',
                        checked
                          ? 'bg-brand-500/5 border-brand-500/20'
                          : 'hover:bg-slate-800/40',
                        (isRunning || onlyOne) && 'opacity-40 pointer-events-none',
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => !onlyOne && toggleSource(id)}
                        disabled={isRunning || onlyOne}
                        className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800 accent-brand-500
                                   disabled:cursor-not-allowed shrink-0"
                      />
                      <span className="text-sm leading-none">{emoji}</span>
                      <span className={clsx(
                        'text-xs font-medium transition-colors',
                        checked ? 'text-slate-200' : 'text-slate-500 group-hover:text-slate-300',
                      )}>
                        {label}
                      </span>
                    </label>
                  )
                })}
              </div>
              <p className="text-[11px] text-slate-500 mt-2 pl-1">
                ≈ <span className="font-bold text-slate-300">{estimatedLeads}</span> leads expected
              </p>
            </div>

            {/* ── Daily Cap per Source ──────────────────────────────────── */}
            {(sources.includes('GOOGLE_MAPS') || sources.includes('GOOGLE_SEARCH')) && (
              <div>
                <label className="label flex items-center gap-1.5">
                  <BarChart3 size={10} /> Daily Cap per Source
                </label>
                <div className="space-y-2">
                  {sources.includes('GOOGLE_MAPS') && (
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-slate-500 w-28 shrink-0">🗺️ Maps cap</span>
                      <input
                        type="number"
                        min={1} max={200}
                        value={googleMapsCap}
                        onChange={e => setGoogleMapsCap(Math.max(1, Number(e.target.value)))}
                        disabled={isRunning}
                        className="input text-xs h-8 w-20 text-center tabular-nums"
                      />
                    </div>
                  )}
                  {sources.includes('GOOGLE_SEARCH') && (
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-slate-500 w-28 shrink-0">🔍 Search cap</span>
                      <input
                        type="number"
                        min={1} max={200}
                        value={googleSearchCap}
                        onChange={e => setGoogleSearchCap(Math.max(1, Number(e.target.value)))}
                        disabled={isRunning}
                        className="input text-xs h-8 w-20 text-center tabular-nums"
                      />
                    </div>
                  )}
                  <p className="text-[10px] text-slate-600 pl-0.5">
                    Total: <span className="font-mono font-bold text-slate-400">{totalCap}</span> leads / run
                  </p>
                </div>
              </div>
            )}

            {/* ── Scoring Filter ────────────────────────────────────────── */}
            <div className="rounded-lg bg-slate-900/50 border border-slate-700/40 p-3 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <Flame
                    size={13}
                    className={clsx(hotWarmOnly ? 'text-orange-400' : 'text-slate-600', 'shrink-0')}
                  />
                  <span className="text-xs text-slate-300 truncate">Only HOT + WARM leads</span>
                </div>
                <Toggle
                  checked={hotWarmOnly}
                  onChange={setHotWarmOnly}
                  disabled={isRunning}
                />
              </div>
              {hotWarmOnly && (
                <p className="text-[10px] text-slate-500 pl-0.5">
                  Estimated emails to send:{' '}
                  <span className="font-bold text-orange-400 font-mono">{hotWarmCount}</span>
                </p>
              )}
            </div>

            {/* ── Outreach Channel ─────────────────────────────────────── */}
            <div>
              <label className="label">Outreach Channel</label>
              <div className="flex gap-1.5">
                {CHANNELS.map(({ id, label, emoji }) => (
                  <button
                    key={id}
                    onClick={() => setChannel(id)}
                    disabled={isRunning}
                    className={clsx(
                      'flex-1 flex flex-col items-center gap-1.5 py-2.5 rounded-xl border',
                      'text-xs font-medium transition-all duration-150',
                      'disabled:opacity-40 disabled:cursor-not-allowed',
                      channel === id
                        ? CHANNEL_ACTIVE[id]
                        : 'border-slate-700/50 text-slate-500 hover:border-slate-600 hover:text-slate-400',
                    )}
                  >
                    <span className="text-base leading-none">{emoji}</span>
                    <span>{label}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* ── Show Browser Window ───────────────────────────────────── */}
            <label className="flex items-center gap-2 cursor-pointer group select-none">
              <input
                type="checkbox"
                checked={!headless}
                onChange={e => setHeadless(!e.target.checked)}
                disabled={isRunning}
                className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800 accent-emerald-500
                           disabled:opacity-40 disabled:cursor-not-allowed"
              />
              <Monitor size={11} className="text-slate-500" />
              <span className="text-xs text-slate-400 group-hover:text-slate-300 transition-colors">
                Show Browser Window
              </span>
            </label>

            {/* ── Start / Stop buttons ──────────────────────────────────── */}
            <div className="flex flex-col gap-2 pt-1">
              <button
                onClick={() => startMut.mutate()}
                disabled={!canStart || startMut.isPending}
                className="w-full py-3.5 rounded-xl font-bold text-sm tracking-widest uppercase
                           bg-emerald-600 hover:bg-emerald-500 text-white
                           shadow-lg shadow-emerald-950/60 hover:shadow-emerald-800/40
                           disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none
                           transition-all duration-150 active:scale-[0.98] disabled:active:scale-100
                           flex items-center justify-center gap-2"
              >
                {startMut.isPending
                  ? <><RefreshCw size={15} className="animate-spin" /> Launching…</>
                  : <><Rocket size={15} /> START ENGINE</>}
              </button>

              <button
                onClick={() => stopMut.mutate()}
                disabled={!isRunning || stopMut.isPending}
                className="w-full py-2.5 rounded-xl font-bold text-sm tracking-widest uppercase
                           bg-red-600/10 hover:bg-red-600/25 text-red-400 border border-red-600/30
                           disabled:opacity-25 disabled:cursor-not-allowed
                           transition-all duration-150 active:scale-[0.98]
                           flex items-center justify-center gap-2"
              >
                {stopMut.isPending
                  ? <RefreshCw size={14} className="animate-spin" />
                  : <Square size={13} />}
                STOP ENGINE
              </button>

              {!niche.trim() && !isRunning && (
                <p className="text-[10px] text-amber-500/70 text-center">
                  Enter niche &amp; city to enable launch
                </p>
              )}
            </div>
          </div>

          {/* Session stats card */}
          <div className="card p-4 space-y-3">
            <p className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold flex items-center gap-1.5">
              <TrendingUp size={10} />
              Session Overview
            </p>
            <div className="space-y-2">
              <SessionStat label="Total Leads"  value={stats?.total_leads ?? '—'}              Icon={Users}         colorClass="text-slate-300"   />
              <SessionStat label="Pending"       value={stats?.pending     ?? '—'}              Icon={Clock}         colorClass="text-amber-400"   />
              <SessionStat label="Replies"       value={stats?.replied     ?? '—'}              Icon={MessageSquare} colorClass="text-emerald-400" />
              <SessionStat label="Reply Rate"    value={stats ? `${stats.reply_rate}%` : '—'}  Icon={TrendingUp}    colorClass="text-purple-400"  />
              <SessionStat label="HOT Leads"     value={stats?.hot_leads   ?? '—'}              Icon={Flame}         colorClass="text-red-400"     />
              <SessionStat label="WARM Leads"    value={stats?.warm_leads  ?? '—'}              Icon={Activity}      colorClass="text-amber-400"   />
            </div>
          </div>
        </div>

        {/* ══════ RIGHT — Pipeline + Terminal + History ══════ */}
        <div className="flex-1 flex flex-col gap-4 min-h-0 min-w-0">

          {/* ── Pipeline progress card ────────────────────────────────── */}
          <PipelineCard
            isRunning={isRunning}
            logs={logs}
            leadsFound={leadsFound}
            leadsSent={leadsSent}
            scoreDist={scoreDist}
          />

          {/* ── Live terminal ─────────────────────────────────────────── */}
          <div className="bg-[#07080d] border border-slate-700/60 rounded-xl overflow-hidden flex flex-col flex-1 min-h-0">

            {/* Title bar */}
            <div className="flex items-center gap-2 px-4 py-2.5 bg-slate-900/90 border-b border-slate-800 shrink-0">
              <div className="flex gap-1.5 mr-2">
                <div className="w-3 h-3 rounded-full bg-red-500/70" />
                <div className="w-3 h-3 rounded-full bg-amber-400/70" />
                <div className="w-3 h-3 rounded-full bg-emerald-500/70" />
              </div>

              <Radio size={11} className="text-slate-600" />
              <span className="text-[11px] font-mono text-slate-500 uppercase tracking-widest">
                outreach engine — live feed
              </span>

              <div className="ml-auto flex items-center gap-3">
                <span className="text-[10px] font-mono text-slate-700 tabular-nums">
                  {logs.length} entries
                </span>

                <div className="flex items-center gap-1.5">
                  <div className={clsx(
                    'w-1.5 h-1.5 rounded-full transition-colors duration-300',
                    sseLive ? 'bg-emerald-400 animate-pulse' : 'bg-red-500',
                  )} />
                  <span className={clsx(
                    'text-[10px] font-mono',
                    sseLive ? 'text-emerald-500' : 'text-red-500',
                  )}>
                    {sseLive ? 'LIVE' : 'RECONNECTING'}
                  </span>
                </div>

                <button
                  onClick={() => setLogs([])}
                  title="Clear log"
                  className="p-1 rounded text-slate-700 hover:text-slate-400 transition-colors"
                >
                  <Trash2 size={11} />
                </button>
              </div>
            </div>

            {/* Filter tabs */}
            <div className="flex items-center gap-0.5 px-4 py-2 bg-slate-900/50 border-b border-slate-800/60 shrink-0">
              <Filter size={10} className="text-slate-700 mr-1.5 shrink-0" />
              {LOG_FILTERS.map(({ id, label }) => {
                const count = id === 'all' ? logs.length : (logCounts[id] || 0)
                return (
                  <button
                    key={id}
                    onClick={() => setLogFilter(id)}
                    className={clsx(
                      'px-2.5 py-0.5 rounded text-[10px] font-mono font-medium transition-all whitespace-nowrap',
                      logFilter === id
                        ? 'bg-slate-700/80 text-slate-200'
                        : 'text-slate-600 hover:text-slate-400 hover:bg-slate-800/50',
                    )}
                  >
                    {label}
                    {count > 0 && <span className="ml-1 opacity-60">({count})</span>}
                  </button>
                )
              })}
            </div>

            {/* Log body */}
            <div className="flex-1 overflow-y-auto p-4 space-y-0.5 font-mono text-[11px] leading-5 min-h-0">
              {filteredLogs.length === 0 && (
                <span className="text-slate-700 select-none">
                  {logs.length === 0
                    ? 'Waiting for activity… Start the engine to see live output.'
                    : `No "${logFilter}" events yet.`}
                </span>
              )}

              {filteredLogs.map((entry, i) => (
                <div
                  key={i}
                  className="flex gap-3 hover:bg-white/[0.02] rounded px-1 -mx-1 transition-colors"
                >
                  <span className="text-slate-700 shrink-0 tabular-nums w-16 text-right pt-px select-none">
                    {entry.ts}
                  </span>
                  <span className={clsx('flex-1 break-words', entry.color)}>
                    {entry.message}
                  </span>
                </div>
              ))}

              {/* Blinking cursor */}
              <div className="flex items-center h-5 mt-0.5 pl-[calc(4rem+0.75rem)]">
                <span className="inline-block w-2 h-3.5 bg-emerald-500/60 animate-pulse rounded-sm" />
              </div>
              <div ref={logEndRef} />
            </div>
          </div>

          {/* ── Campaign history ──────────────────────────────────────── */}
          <div className="card overflow-hidden shrink-0">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-700/50">
              <History size={13} className="text-slate-500" />
              <h3 className="text-sm font-semibold text-slate-300">Recent Campaigns</h3>
              {history?.length > 0 && (
                <span className="text-[10px] text-slate-600 ml-0.5">({history.length})</span>
              )}
              <button
                onClick={() => refetchHistory()}
                title="Refresh history"
                className="ml-auto p-1 rounded text-slate-600 hover:text-slate-400 transition-colors"
              >
                <RefreshCw size={12} />
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-800/60">
                    {[
                      { label: 'Date',     right: false },
                      { label: 'Niche',    right: false },
                      { label: 'City',     right: false },
                      { label: 'Channel',  right: false },
                      { label: 'Found',    right: true  },
                      { label: 'Sent',     right: true  },
                      { label: 'Duration', right: true  },
                      { label: 'Status',   right: false },
                    ].map(({ label, right }) => (
                      <th
                        key={label}
                        className={clsx(
                          'px-3 py-2 font-medium text-slate-500 text-[10px] uppercase tracking-wide',
                          right ? 'text-right' : 'text-left',
                        )}
                      >
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/40">
                  {history?.map((run) => (
                    <tr key={run.id} className="hover:bg-slate-800/20 transition-colors group">
                      <td className="px-3 py-2.5 text-slate-500 font-mono whitespace-nowrap text-[11px]">
                        {fmtRunDate(run.started_at)}
                      </td>
                      <td className="px-3 py-2.5 text-slate-300 max-w-[130px] truncate" title={run.niche}>
                        {run.niche || '—'}
                      </td>
                      <td className="px-3 py-2.5 text-slate-400 whitespace-nowrap">
                        {run.city || '—'}
                      </td>
                      <td className="px-3 py-2.5">
                        {run.channel
                          ? <span className={`badge text-[10px] px-2 py-0.5 ${CHANNEL_BADGE[run.channel] || 'badge'}`}>{run.channel}</span>
                          : <span className="text-slate-600">—</span>}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-slate-300 tabular-nums">
                        {run.leads_found ?? 0}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-emerald-400 font-bold tabular-nums">
                        {run.leads_sent ?? 0}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-slate-600 text-[10px] tabular-nums whitespace-nowrap">
                        {fmtDuration(run.started_at, run.finished_at)}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium ${RUN_STATUS_CLS[run.status] || ''}`}>
                          {run.status}
                        </span>
                      </td>
                    </tr>
                  ))}

                  {(!history || history.length === 0) && (
                    <tr>
                      <td colSpan={8} className="px-4 py-10 text-center">
                        <div className="flex flex-col items-center gap-2">
                          <Rocket size={24} className="text-slate-700" />
                          <p className="text-slate-600 text-xs">No campaigns yet</p>
                          <p className="text-slate-700 text-[10px]">
                            Enter a niche &amp; city above, then hit START ENGINE
                          </p>
                        </div>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

        </div>
      </div>
    </div>
  )
}
