import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  TrendingUp, MailOpen, MessageSquare, MapPin, Clock, AlertCircle, PlayCircle,
  RefreshCw, Activity, ArrowRight, Search,
} from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { statsApi, engineApi, logsApi, scraperApi } from '../api/client'
import { Skeleton } from '../components/ui/Skeleton'
import ErrorState from '../components/ui/ErrorState'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { useTheme, chartColors } from '../lib/theme'
import ResultsCard from '../components/ResultsCard'

// ─── Helpers ────────────────────────────────────────────────────────────────

function timeAgo(ts) {
  if (!ts) return '—'
  const secs = Math.floor((Date.now() - new Date(ts + 'Z').getTime()) / 1000)
  if (secs < 60)   return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

function fmtCurrency(n) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: 0,
  }).format(n)
}

// ─── Activity Feed ───────────────────────────────────────────────────────────

const LOG_CFG = {
  EMAIL_SEND:     { color: 'blue',   Icon: MailOpen,      label: 'Email Sent'  },
  WHATSAPP_SEND:  { color: 'green',  Icon: MessageSquare, label: 'WA Sent'     },
  EMAIL_FOLLOWUP: { color: 'purple', Icon: Clock,         label: 'Followup'    },
  SCRAPE_FOUND:   { color: 'amber',  Icon: MapPin,        label: 'Lead Found'  },
  FAILED:         { color: 'red',    Icon: AlertCircle,   label: 'Failed'      },
  DEFAULT:        { color: 'slate',  Icon: Activity,      label: 'Action'      },
}

function logCfg(log) {
  if (!log.success) return LOG_CFG.FAILED
  const key = `${log.channel}_${log.action}`
  return LOG_CFG[key] || LOG_CFG.DEFAULT
}

function ActivityFeed({ logs, isLoading }) {
  if (isLoading) return (
    <div className="flex items-center justify-center py-12 text-slate-600 text-sm gap-2">
      <div className="w-4 h-4 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
      Loading activity...
    </div>
  )

  if (!logs.length) return (
    <div className="py-10">
      <p className="text-subheading">No activity yet</p>
      <p className="text-support mt-1">Actions from campaigns and searches will show up here.</p>
    </div>
  )

  return (
    <ul className="divide-y divide-border-subtle">
      {logs.slice(0, 8).map((log) => {
        const { color, Icon, label } = logCfg(log)
        const failed = color === 'red'
        return (
          <li key={log.id} className="flex items-center gap-3 py-3">
            <span className={clsx('grid place-items-center w-8 h-8 rounded-full shrink-0',
              failed ? 'bg-error/10 text-error' : 'bg-secondary text-muted-foreground')}>
              <Icon size={14} strokeWidth={1.9} />
            </span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground truncate">{log.business_name}</p>
              <p className="text-meta truncate">
                {label}{(log.niche || log.city) && ` · ${[log.niche, log.city].filter(Boolean).join(' · ')}`}
              </p>
            </div>
            <span className="text-meta tabular shrink-0">{timeAgo(log.timestamp)}</span>
          </li>
        )
      })}
    </ul>
  )
}

// ─── Quick Launch Panel ──────────────────────────────────────────────────────

function QuickLaunchPanel({ engineStatus }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ query: '', city: '', niche: '', max_results: 20 })
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  const launchMut = useMutation({
    mutationFn: () => scraperApi.search({ ...form, max_results: Number(form.max_results) }),
    onSuccess: () => {
      toast.success('Scraper launched — check Campaign page for details')
      qc.invalidateQueries({ queryKey: ['engine-status'] })
    },
    onError: (e) => toast.error(e.message),
  })

  const runNowMut = useMutation({
    mutationFn: engineApi.runNow,
    onSuccess: () => toast.success('Campaign job queued — AI generate + send to all pending leads'),
    onError: (e) => toast.error(e.message),
  })

  const isBusy = engineStatus?.scraper_running || launchMut.isPending

  return (
    <div className="p-5 space-y-5 h-full flex flex-col">
      <div>
        <h2 className="text-section">Quick launch</h2>
        <p className="text-support mt-0.5">Find new leads in a city, or run today's campaign.</p>
      </div>

      <div className="space-y-2.5 flex-1">
        <div>
          <label className="label">Search Query</label>
          <input
            className="input text-xs"
            placeholder="e.g. dental clinic, restaurant, gym..."
            value={form.query}
            onChange={set('query')}
          />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label">City</label>
            <input className="input text-xs" placeholder="Dubai, NYC..." value={form.city} onChange={set('city')} />
          </div>
          <div>
            <label className="label">Niche Tag</label>
            <input className="input text-xs" placeholder="dental..." value={form.niche} onChange={set('niche')} />
          </div>
        </div>
        <div>
          <div className="flex justify-between mb-1">
            <label className="label mb-0">Max Results</label>
            <span className="text-xs text-slate-400 font-medium">{form.max_results}</span>
          </div>
          <input
            type="range" min={5} max={50} step={5}
            className="w-full accent-brand-500 h-1.5"
            value={form.max_results}
            onChange={(e) => setForm((f) => ({ ...f, max_results: Number(e.target.value) }))}
          />
          <div className="flex justify-between text-[10px] text-slate-700 mt-0.5"><span>5</span><span>50</span></div>
        </div>
      </div>

      {/* Scraper progress bar */}
      {engineStatus?.scraper_running && (
        <div className="space-y-1.5 py-2 px-3 bg-amber-500/5 border border-amber-500/20 rounded-lg">
          <div className="flex justify-between text-xs text-amber-400/80">
            <span className="flex items-center gap-1"><Activity size={10} className="animate-pulse" /> Scraping...</span>
            <span>{engineStatus.scraper_progress}/{engineStatus.scraper_total}</span>
          </div>
          <div className="w-full bg-slate-700 rounded-full h-1">
            <div
              className="bg-amber-500 h-1 rounded-full transition-all duration-500"
              style={{ width: `${Math.round((engineStatus.scraper_progress / Math.max(engineStatus.scraper_total, 1)) * 100)}%` }}
            />
          </div>
          {engineStatus.scraper_last_name && (
            <p className="text-[10px] text-slate-600 truncate">{engineStatus.scraper_last_name}</p>
          )}
        </div>
      )}

      <div className="space-y-2 pt-1">
        <button
          disabled={!form.query || !form.city || isBusy}
          onClick={() => launchMut.mutate()}
          className="btn-primary w-full"
        >
          {isBusy
            ? <><RefreshCw size={12} className="animate-spin" /> Running...</>
            : <><Search size={14} /> Find leads</>}
        </button>

        <div className="relative flex items-center gap-2">
          <div className="flex-1 h-px bg-slate-700/60" />
          <span className="text-[10px] text-slate-600 shrink-0">or</span>
          <div className="flex-1 h-px bg-slate-700/60" />
        </div>

        <button
          onClick={() => runNowMut.mutate()}
          disabled={runNowMut.isPending}
          className="btn-secondary w-full"
        >
          {runNowMut.isPending
            ? <><RefreshCw size={12} className="animate-spin" /> Queuing...</>
            : <><PlayCircle size={14} /> Run campaign now</>}
        </button>
        <p className="text-meta text-center">
          Drafts messages for pending leads and sends the approved ones.
        </p>
      </div>
    </div>
  )
}

// ─── Custom Recharts Tooltip ─────────────────────────────────────────────────

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="surface-overlay rounded-lg px-3 py-2 text-xs">
      <p className="text-muted-foreground mb-1">{label}</p>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: p.color }} />
          <span className="text-muted-foreground">{p.name}</span>
          <span className="ml-auto pl-3 font-semibold text-foreground tabular">{p.value}</span>
        </div>
      ))}
    </div>
  )
}

function Metric({ label, value, detail, accent }) {
  return (
    <div className="sm:px-5 sm:first:pl-0">
      <dt className="text-meta">{label}</dt>
      <dd className={clsx('text-2xl font-bold tabular mt-1', accent ? 'text-primary' : 'text-foreground')}>{value}</dd>
      <dd className="text-meta mt-0.5 truncate">{detail}</dd>
    </div>
  )
}

function SectionHeader({ title, children }) {
  return (
    <div className="flex items-baseline justify-between gap-4 mb-4">
      <h2 className="text-section">{title}</h2>
      {children && <div className="text-meta flex items-center gap-4">{children}</div>}
    </div>
  )
}

const PIPELINE = [
  { key: 'pending', label: 'Waiting',  bar: 'bg-slate-500' },
  { key: 'sent',    label: 'Contacted', bar: 'bg-primary' },
  { key: 'replied', label: 'Replied',  bar: 'bg-success' },
  { key: 'skipped', label: 'Skipped',  bar: 'bg-slate-700' },
]

export default function Dashboard() {
  const { theme } = useTheme()
  const cc = chartColors(theme)
  const {
    data: stats, isLoading: statsLoading, isError: statsError, refetch: refetchStats, isFetching: statsRefetching,
  } = useQuery({
    queryKey: ['dashboard-stats'],
    queryFn: statsApi.dashboard,
    refetchInterval: 15_000,
  })

  const {
    data: weeklyData = [], isError: weeklyError, refetch: refetchWeekly,
  } = useQuery({
    queryKey: ['stats-weekly'],
    queryFn: statsApi.weekly,
    refetchInterval: 60_000,
  })

  const { data: logs = [], isLoading: logsLoading } = useQuery({
    queryKey: ['recent-logs'],
    queryFn: () => logsApi.recent(20),
    refetchInterval: 15_000,
  })

  const { data: engineStatus } = useQuery({
    queryKey: ['engine-status'],
    queryFn: engineApi.status,
    refetchInterval: 5_000,
    retry: false,
  })

  // Est. revenue comes from the backend (avg_deal_value is set in Settings).
  const estRevenue = stats?.estimated_revenue ?? 0
  const sentToday  = (stats?.email_sent_today || 0) + (stats?.whatsapp_sent_today || 0)
  const conversion = stats?.sent ? `${((stats.replied / stats.sent) * 100).toFixed(1)}%` : '—'
  const pipelineTotal = PIPELINE.reduce((n, p) => n + (stats?.[p.key] || 0), 0)
  const hasWeekly = weeklyData.some((d) => d.leads_created > 0 || d.total_sent > 0)
  const unread = stats?.unread_replies || 0

  return (
    <div className="px-4 sm:px-8 py-8 max-w-[1280px] mx-auto space-y-12">

      {/* Header */}
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-page">Overview</h1>
          <p className="text-support mt-1">Your pipeline at a glance. Updates every 15 seconds.</p>
        </div>
        <p className="text-meta flex items-center gap-1.5 shrink-0">
          <span className={clsx('w-1.5 h-1.5 rounded-full',
            statsError ? 'bg-error' : statsRefetching ? 'bg-slate-500' : 'bg-success')} />
          {statsError ? 'Connection error' : stats ? 'Live' : 'Loading…'}
        </p>
      </header>

      {/* Headline + secondary metrics */}
      {statsError ? (
        <ErrorState message="Couldn't load dashboard stats." onRetry={refetchStats} retrying={statsRefetching} />
      ) : statsLoading ? (
        <div className="space-y-3"><Skeleton className="h-3 w-24" /><Skeleton className="h-12 w-40" /></div>
      ) : (
        <section className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,2.2fr)] lg:items-end">
          <div>
            <p className="text-meta">Total leads</p>
            <p className="text-5xl font-bold tabular mt-2">{stats?.total_leads ?? '—'}</p>
            <p className="text-support mt-2">{stats?.pending ?? 0} waiting to be contacted</p>
          </div>
          <dl className="grid grid-cols-2 sm:grid-cols-4 gap-y-6 sm:divide-x divide-border-subtle">
            <Metric label="Sent today" value={sentToday}
              detail={`${stats?.email_sent_today ?? 0} email · ${stats?.whatsapp_sent_today ?? 0} WhatsApp`} />
            <Metric label="Reply rate" value={`${stats?.reply_rate ?? 0}%`} detail={`${stats?.replied ?? 0} replies`} />
            <Metric label="Conversion" value={conversion} detail="Replies per message sent" />
            <Metric label="Est. revenue" value={fmtCurrency(estRevenue)} detail="From replied leads" accent />
          </dl>
        </section>
      )}

      <ResultsCard />

      {/* Pipeline composition + quality */}
      {stats && (
        <section>
          <SectionHeader title="Pipeline">
            <span className="tabular">{pipelineTotal} leads</span>
          </SectionHeader>
          <div className="flex h-2 rounded-full overflow-hidden bg-secondary gap-0.5" role="img"
            aria-label={PIPELINE.map((p) => `${p.label} ${stats[p.key] || 0}`).join(', ')}>
            {pipelineTotal > 0 && PIPELINE.map((p) => (stats[p.key] || 0) > 0 && (
              <div key={p.key} className={clsx(p.bar, 'h-full')} style={{ width: `${(stats[p.key] / pipelineTotal) * 100}%` }} />
            ))}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-x-8 gap-y-3">
            {PIPELINE.map((p) => (
              <div key={p.key} className="flex items-center gap-2 text-sm">
                <span className={clsx('w-2 h-2 rounded-full', p.bar)} />
                <span className="text-muted-foreground">{p.label}</span>
                <span className="font-semibold tabular">{stats[p.key] || 0}</span>
              </div>
            ))}
            <span className="hidden sm:block w-px h-4 bg-border" />
            <div className="flex items-center gap-5 text-sm">
              <span className="text-muted-foreground">Lead quality</span>
              {[['Hot', stats.hot_leads, 'bg-error'], ['Warm', stats.warm_leads, 'bg-warning'], ['Cold', stats.cold_leads, 'bg-info']].map(([l, n, dot]) => (
                <span key={l} className="flex items-center gap-1.5">
                  <span className={clsx('w-2 h-2 rounded-full', dot)} />
                  <span className="text-muted-foreground">{l}</span>
                  <span className="font-semibold tabular">{n ?? 0}</span>
                </span>
              ))}
            </div>
          </div>
          {unread > 0 && (
            <Link to="/inbox" className="mt-6 flex items-center justify-between gap-3 rounded-xl bg-primary/10 px-4 py-3 text-sm text-foreground hover:bg-primary/[0.14] transition-colors">
              <span><span className="font-semibold">{unread} unread {unread === 1 ? 'reply' : 'replies'}</span>
                <span className="text-muted-foreground"> waiting in your inbox</span></span>
              <ArrowRight size={16} className="text-primary" />
            </Link>
          )}
        </section>
      )}

      {/* Trend + activity (open), quick launch (raised) */}
      <div className="grid gap-12 lg:grid-cols-3 lg:gap-10">
        <div className="lg:col-span-2 space-y-12 min-w-0">
          <section>
            <SectionHeader title="Last 7 days">
              <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 rounded-full" style={{ background: cc.primary }} />Leads found</span>
              <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 rounded-full" style={{ background: cc.secondary }} />Messages sent</span>
            </SectionHeader>
            {weeklyError ? (
              <ErrorState message="Couldn't load the weekly trend." onRetry={refetchWeekly} className="h-[220px] justify-center" />
            ) : hasWeekly ? (
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={weeklyData} margin={{ top: 6, right: 4, bottom: 0, left: -24 }}>
                  <defs>
                    <linearGradient id="gradLeads" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={cc.primary} stopOpacity={0.18} />
                      <stop offset="100%" stopColor={cc.primary} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke={cc.grid} vertical={false} />
                  <XAxis dataKey="day_label" tick={{ fontSize: 11, fill: cc.axis }} axisLine={false} tickLine={false} dy={6} />
                  <YAxis tick={{ fontSize: 11, fill: cc.axis }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip content={<ChartTooltip />} cursor={{ stroke: cc.grid }} />
                  <Area type="monotone" dataKey="leads_created" name="Leads found"
                    stroke={cc.primary} strokeWidth={2} fill="url(#gradLeads)"
                    dot={false} activeDot={{ r: 3.5, fill: cc.primary, strokeWidth: 0 }} />
                  <Area type="monotone" dataKey="total_sent" name="Messages sent"
                    stroke={cc.secondary} strokeWidth={1.75} fill="none"
                    dot={false} activeDot={{ r: 3.5, fill: cc.secondary, strokeWidth: 0 }} />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-[220px] flex flex-col justify-center border-y border-border-subtle">
                <TrendingUp size={20} className="text-muted-foreground" />
                <p className="text-subheading mt-3">No activity this week</p>
                <p className="text-support mt-1">Start a search or campaign and the trend will appear here.</p>
              </div>
            )}
          </section>

          <section>
            <SectionHeader title="Recent activity">
              <span>Live</span>
            </SectionHeader>
            <ActivityFeed logs={logs} isLoading={logsLoading} />
          </section>
        </div>

        <aside>
          <div className="surface-raised lg:sticky lg:top-8">
            <QuickLaunchPanel engineStatus={engineStatus} />
          </div>
        </aside>
      </div>
    </div>
  )
}
