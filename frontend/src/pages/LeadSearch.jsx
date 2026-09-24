import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Zap, ScanSearch, PenLine, CalendarClock, ChevronRight, ChevronDown, RefreshCw, Mail, MessageSquare, Check, Lock,
} from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { leadRunsApi, engineApi, automationApi } from '../api/client'
import TargetTitlesInput from '../components/TargetTitlesInput'
import { stepsLabel } from '../lib/leadRuns'
import { isClientEdition } from '../lib/edition'

// Find leads — the one place to start. The user picks niche, place and count,
// then any combination of steps; one run chains them over the same leads
// (backend/lead_runs). Sources are chosen by the planner and never shown.
// Outreach here only drafts — sending happens after review in AI Lab.
const STEPS = [
  {
    id: 'collect', icon: Zap, title: 'Find leads', locked: true,
    desc: 'Matching businesses with their websites, phone numbers and emails.',
  },
  {
    id: 'research', icon: ScanSearch, title: 'Deep research',
    desc: 'Reads each website to find decision makers and their contact details.',
  },
  {
    id: 'outreach', icon: PenLine, title: 'Write outreach',
    desc: 'Scores each lead and drafts a personal message for you to review.',
  },
]
const MAX_LEADS = 100

// Daily automation takes several niches / places; ";" separates them because
// a place name can itself contain a comma ("Austin, TX").
const splitList = (v) => v.split(';').map((x) => x.trim()).filter(Boolean)

// Rough, honest time estimate — deep research dominates.
function estimate(n, research, outreach) {
  const minutes = 3 + (research ? 2.5 * n : 0) + (outreach ? 1.2 * n : 0)
  if (minutes < 60) return `about ${Math.max(5, Math.round(minutes / 5) * 5)} minutes`
  const h = Math.floor(minutes / 60)
  const m = Math.round((minutes % 60) / 10) * 10
  return `about ${h} h${m ? ` ${m} min` : ''}`
}

// Email / WhatsApp chips, plus "Select both". At least one stays on.
function ChannelPicker({ email, whatsapp, onChange }) {
  const chip = (on, label, Icon, next) => (
    <button
      type="button"
      aria-pressed={on}
      onClick={(e) => { e.preventDefault(); e.stopPropagation(); onChange(next) }}
      className={clsx(
        'inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full border text-xs font-medium transition-colors',
        on ? 'border-primary bg-primary text-primary-foreground' : 'border-border text-muted-foreground hover:text-foreground',
      )}
    >
      {on ? <Check size={12} strokeWidth={2.6} /> : <Icon size={12} strokeWidth={2} />}
      {label}
    </button>
  )
  const both = email && whatsapp
  // Client workspaces send email only (WhatsApp desktop belongs to the owner's computer).
  if (isClientEdition()) {
    return <div className="flex flex-wrap items-center gap-1.5">{chip(true, 'Email', Mail, { email: true, whatsapp: false })}</div>
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {chip(email, 'Email', Mail, email && !whatsapp ? { email, whatsapp } : { email: !email, whatsapp })}
      {chip(whatsapp, 'WhatsApp', MessageSquare, whatsapp && !email ? { email, whatsapp } : { email, whatsapp: !whatsapp })}
      <button
        type="button"
        disabled={both}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); onChange({ email: true, whatsapp: true }) }}
        className="h-7 px-2 rounded-full text-xs font-semibold text-primary hover:bg-primary/10 disabled:text-muted-foreground disabled:hover:bg-transparent transition-colors"
      >
        {both ? 'Both selected' : 'Select both'}
      </button>
    </div>
  )
}

const ACTIVE = new Set(['QUEUED', 'RUNNING', 'CANCEL_REQUESTED', 'SCHEDULED'])
const STATUS_TEXT = {
  QUEUED: 'Queued', RUNNING: 'Running', CANCEL_REQUESTED: 'Stopping', SCHEDULED: 'Scheduled',
  COMPLETED: 'Finished', FAILED: 'Failed', CANCELLED: 'Cancelled', PAUSED: 'Paused',
  STOPPED: 'Stopped', LIMIT_REACHED: 'Daily limit reached',
}

function relTime(iso) {
  if (!iso) return ''
  const t = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).getTime()
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function ActivityRow({ to, title, detail, status, when }) {
  const running = ACTIVE.has(status)
  return (
    <li>
      <Link to={to} className="group flex items-center gap-4 py-3.5 -mx-3 px-3 rounded-xl hover:bg-secondary/60 transition-colors">
        <span className={clsx('w-2 h-2 rounded-full shrink-0',
          running ? 'bg-success animate-pulse' : status === 'FAILED' ? 'bg-error' : 'bg-slate-500')} />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{title}</p>
          <p className="text-meta truncate">{[STATUS_TEXT[status] || status, detail, when].filter(Boolean).join(' · ')}</p>
        </div>
        <ChevronRight size={16} className="text-muted-foreground group-hover:text-foreground shrink-0" />
      </Link>
    </li>
  )
}

export default function FindLeads() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [what, setWhat] = useState('')
  const [where, setWhere] = useState('')
  const [count, setCount] = useState(20)
  const [research, setResearch] = useState(false)
  const [outreach, setOutreach] = useState(false)
  const [titles, setTitles] = useState([])
  const [channels, setChannels] = useState({ email: true, whatsapp: false })
  const [hotWarmOnly, setHotWarmOnly] = useState(true)
  const [dailyOpen, setDailyOpen] = useState(false)
  const channel = channels.email && channels.whatsapp ? 'BOTH' : channels.whatsapp ? 'WHATSAPP' : 'EMAIL'
  const n = Math.max(1, Math.min(MAX_LEADS, Number(count) || 20))
  const selected = { collect: true, research, outreach }
  const toggle = { research: () => setResearch((v) => !v), outreach: () => setOutreach((v) => !v) }
  const steps = ['collect', ...(research ? ['research'] : []), ...(outreach ? ['outreach'] : [])]

  const soft = { retry: false, refetchInterval: 10_000 }
  const { data: runs } = useQuery({ queryKey: ['lead-runs'], queryFn: () => leadRunsApi.list(8), ...soft })
  const { data: engine } = useQuery({ queryKey: ['engine-status'], queryFn: engineApi.status, ...soft })
  const { data: auto } = useQuery({ queryKey: ['fl-automation'], queryFn: automationApi.status, ...soft })

  const start = useMutation({
    mutationFn: () => leadRunsApi.start({
      niche: what.trim(), location: where.trim(), target_count: n, steps, channel,
      target_titles: research && titles.length ? titles : null, hot_warm_only: hotWarmOnly,
    }),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ['lead-runs'] })
      navigate(`/lead-search/runs/${run.id}`)
    },
    onError: (e) => toast.error(e?.response?.data?.detail || e.message || 'Could not start'),
  })

  const schedule = useMutation({
    mutationFn: async () => {
      await automationApi.saveSettings({ automation_enabled: true, automation_daily_limit: n })
      await automationApi.confirmImport({
        locations: splitList(where).map((city) => ({ city, state: null })),
        niches: splitList(what), filename: 'Find leads', layout: 'combined',
      })
      try {
        await automationApi.start()   // run today's first searches now
      } catch (e) {
        if ((e.status ?? e?.response?.status) !== 409) throw e   // 409 = today's limit already met
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fl-automation'] })
      navigate('/lead-search/automation')
    },
    onError: (e) => toast.error(e?.response?.data?.detail || e.message || 'Could not schedule'),
  })

  function submit(e) {
    e.preventDefault()
    if (!what.trim() || !where.trim()) {
      toast.error('Tell us what kind of business and where')
      return
    }
    start.mutate()
  }

  const autoNiches = splitList(what)
  const autoPlaces = splitList(where)
  const existingQueue = Number(auto?.queue_total || 0)

  const activity = [
    engine?.campaign_running && {
      key: 'campaign', to: '/campaign', status: 'RUNNING',
      title: `Outreach campaign · ${engine.campaign_niche || ''}${engine.campaign_city ? ` in ${engine.campaign_city}` : ''}`,
      detail: `${engine.campaign_leads_found ?? 0} found, ${engine.campaign_leads_sent ?? 0} sent`,
    },
    auto && auto.status && auto.status !== 'IDLE' && {
      key: 'auto', to: '/lead-search/automation', status: auto.status, title: 'Daily automation',
      detail: `${auto.today_count ?? 0} today, ${auto.total_count ?? 0} total`,
    },
    ...(runs?.runs || []).map((r) => ({
      key: `run-${r.id}`, to: `/lead-search/runs/${r.id}`, status: r.status,
      title: `${stepsLabel(r.steps)} · ${r.niche} in ${r.location}`,
      detail: [
        `${r.leads_found} lead${r.leads_found === 1 ? '' : 's'}`,
        r.steps.includes('research') && `${r.leads_researched} researched`,
        r.steps.includes('outreach') && `${r.drafts_written} draft${r.drafts_written === 1 ? '' : 's'}`,
      ].filter(Boolean).join(', '),
      when: relTime(r.finished_at || r.created_at),
    })),
  ].filter(Boolean)

  return (
    <div className="px-4 sm:px-8 py-10 max-w-4xl mx-auto space-y-12">
      <header>
        <h1 className="text-3xl font-bold">Find leads</h1>
        <p className="text-support mt-2 max-w-xl">
          Say who you're looking for and where, then pick what we should do. We choose the best places to look.
        </p>
      </header>

      <form onSubmit={submit} className="surface-raised p-5 sm:p-7 space-y-7">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_120px]">
          <div>
            <label htmlFor="fl-what" className="label">Who are you looking for?</label>
            <input id="fl-what" className="input h-11 text-base" autoFocus placeholder="e.g. Dental clinics"
              value={what} onChange={(e) => setWhat(e.target.value)} />
          </div>
          <div>
            <label htmlFor="fl-where" className="label">Where?</label>
            <input id="fl-where" className="input h-11 text-base" placeholder="e.g. Dubai"
              value={where} onChange={(e) => setWhere(e.target.value)} />
          </div>
          <div>
            <label htmlFor="fl-count" className="label">How many leads?</label>
            <input id="fl-count" type="number" min={1} max={MAX_LEADS} className="input h-11 text-base tabular"
              value={count} onChange={(e) => setCount(e.target.value)} />
          </div>
        </div>

        <fieldset>
          <legend className="text-subheading">What should we do?</legend>
          <p className="text-meta mt-0.5 mb-3">Pick one step or all three. They run in order, on the same leads.</p>
          <div className="grid gap-3 sm:grid-cols-3">
            {STEPS.map(({ id, icon: Icon, title, desc, locked }, i) => {
              const on = selected[id]
              return (
                <label key={id} className={clsx(
                  'relative flex flex-col gap-2 rounded-xl border p-4 transition-all',
                  locked ? 'cursor-default' : 'cursor-pointer',
                  on ? 'border-primary bg-primary/5 ring-4 ring-primary/10' : 'border-border hover:border-slate-600/60 hover:bg-secondary/40',
                )}>
                  <input type="checkbox" className="sr-only" checked={on} disabled={locked}
                    onChange={() => !locked && toggle[id]()} aria-label={title} />
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-2">
                      <span className={clsx('grid place-items-center w-5 h-5 rounded-full text-2xs font-bold tabular',
                        on ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground')}>{i + 1}</span>
                      <Icon size={17} strokeWidth={1.9} className={on ? 'text-primary' : 'text-muted-foreground'} />
                    </span>
                    {locked
                      ? <span className="flex items-center gap-1 text-2xs text-muted-foreground"><Lock size={11} /> Always on</span>
                      : <span className={clsx('w-4 h-4 rounded border-2 grid place-items-center',
                          on ? 'border-primary bg-primary text-primary-foreground' : 'border-slate-600')}>
                          {on && <Check size={11} strokeWidth={3} />}
                        </span>}
                  </div>
                  <p className="text-subheading">{title}</p>
                  <p className="text-meta leading-relaxed flex-1">{desc}</p>
                  {id === 'outreach' && (
                    <ChannelPicker email={channels.email} whatsapp={channels.whatsapp}
                      onChange={(c) => { setChannels(c); setOutreach(true) }} />
                  )}
                </label>
              )
            })}
          </div>
        </fieldset>

        {research && (
          <TargetTitlesInput titles={titles} onChange={setTitles} niche={what} inputId="fl-titles" />
        )}

        {outreach && (
          <div className="surface-subtle p-4 space-y-3">
            <p className="text-subheading">How outreach works</p>
            <ol className="grid gap-2 sm:grid-cols-4 text-sm">
              {[
                ['Scores', 'each lead hot, warm or cold'],
                ['Writes', `a personal ${channel === 'BOTH' ? 'email and WhatsApp message' : channel === 'WHATSAPP' ? 'WhatsApp message' : 'email'}`],
                ['You review', 'and edit every draft in AI Lab'],
                ['You send', 'the ones you approve'],
              ].map(([verb, rest], i) => (
                <li key={verb} className="flex gap-2">
                  <span className="grid place-items-center w-5 h-5 rounded-full bg-primary/15 text-primary text-2xs font-bold shrink-0 tabular">{i + 1}</span>
                  <span><span className="font-semibold">{verb}</span> <span className="text-muted-foreground">{rest}</span></span>
                </li>
              ))}
            </ol>
            <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
              <input type="checkbox" checked={hotWarmOnly} onChange={(e) => setHotWarmOnly(e.target.checked)}
                className="w-3.5 h-3.5 rounded accent-[rgb(var(--primary))]" />
              Only write for hot and warm leads
            </label>
            <p className="text-meta">Nothing is sent until you approve it.</p>
          </div>
        )}

        <div className="flex flex-col-reverse sm:flex-row sm:items-center justify-between gap-3 pt-5 border-t border-border-subtle">
          <p className="text-meta">
            {n} lead{n === 1 ? '' : 's'} · {estimate(n, research, outreach)}. Sources are chosen automatically.
          </p>
          <button type="submit" disabled={start.isPending} className="btn-primary h-11 px-6 text-base">
            {start.isPending && <RefreshCw size={15} className="animate-spin" />}
            {steps.length === 1 ? 'Find leads' : `Start: ${stepsLabel(steps).toLowerCase()}`}
          </button>
        </div>
      </form>

      {/* Every day — a schedule, kept separate from the one-off steps above. */}
      <section className="surface-subtle">
        <button type="button" onClick={() => setDailyOpen((v) => !v)} aria-expanded={dailyOpen}
          className="w-full flex items-center gap-4 p-5 text-left">
          <CalendarClock size={20} strokeWidth={1.8} className="text-primary shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-subheading">Run a search every day</p>
            <p className="text-meta mt-0.5">Find new leads for several niches and places on a schedule.</p>
          </div>
          <ChevronDown size={16} className={clsx('text-muted-foreground transition-transform', dailyOpen && 'rotate-180')} />
        </button>
        {dailyOpen && (
          <div className="px-5 pb-5 pt-4 space-y-3 border-t border-border-subtle">
            <p className="text-sm text-muted-foreground">
              Uses the fields above. Add several niches or places separated by a semicolon, like
              <span className="text-foreground"> Dental clinics; Law firms</span> and
              <span className="text-foreground"> Dubai; Austin, TX</span>. “How many leads” becomes the daily limit.
            </p>
            <p className="text-sm">
              {autoNiches.length && autoPlaces.length
                ? <><span className="font-semibold tabular">{autoNiches.length * autoPlaces.length}</span> searches
                    ({autoNiches.length} niche{autoNiches.length === 1 ? '' : 's'} × {autoPlaces.length} place{autoPlaces.length === 1 ? '' : 's'}),
                    up to <span className="font-semibold tabular">{n}</span> new leads a day.</>
                : <span className="text-muted-foreground">Fill in who and where above first.</span>}
            </p>
            {existingQueue > 0 && (
              <p className="text-sm text-warning">This replaces your current daily list of {existingQueue} search{existingQueue === 1 ? '' : 'es'}.</p>
            )}
            <div className="flex flex-wrap items-center gap-3">
              <button type="button" className="btn-secondary"
                disabled={schedule.isPending || !autoNiches.length || !autoPlaces.length}
                onClick={() => schedule.mutate()}>
                {schedule.isPending && <RefreshCw size={14} className="animate-spin" />}
                {existingQueue > 0 ? 'Replace list & schedule' : 'Schedule daily'}
              </button>
              <Link to="/lead-search/automation" className="text-sm font-semibold text-primary hover:underline">
                Daily automation settings
              </Link>
            </div>
          </div>
        )}
      </section>

      <section>
        <h2 className="text-section mb-2">Your searches</h2>
        {activity.length === 0 ? (
          <p className="text-support py-6">Searches you start will appear here, with their progress and results.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {activity.map(({ key, ...row }) => <ActivityRow key={key} {...row} />)}
          </ul>
        )}
      </section>
    </div>
  )
}
