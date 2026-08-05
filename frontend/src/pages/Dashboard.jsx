import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Users, Send, TrendingUp, DollarSign, MailOpen, MessageSquare,
  MapPin, Clock, AlertCircle, PlayCircle, RefreshCw, Rocket,
  CheckCircle2, XCircle, Activity, Flame, Sun, Snowflake, Inbox,
} from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell, Legend,
} from 'recharts'
import { statsApi, engineApi, logsApi, scraperApi } from '../api/client'
import StatCard from '../components/StatCard'
import { SkeletonStatCard, Skeleton } from '../components/ui/Skeleton'
import ErrorState from '../components/ui/ErrorState'
import toast from 'react-hot-toast'
import clsx from 'clsx'

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

const COLOR_CLASSES = {
  blue:   { border: 'border-l-blue-500',   icon: 'text-blue-400',   badge: 'bg-blue-500/15 text-blue-400 border border-blue-500/30'   },
  green:  { border: 'border-l-green-500',  icon: 'text-green-400',  badge: 'bg-green-500/15 text-green-400 border border-green-500/30' },
  purple: { border: 'border-l-purple-500', icon: 'text-purple-400', badge: 'bg-purple-500/15 text-purple-400 border border-purple-500/30' },
  amber:  { border: 'border-l-amber-500',  icon: 'text-amber-400',  badge: 'bg-amber-500/15 text-amber-400 border border-amber-500/30'  },
  red:    { border: 'border-l-red-500',    icon: 'text-red-400',    badge: 'bg-red-500/15 text-red-400 border border-red-500/30'       },
  slate:  { border: 'border-l-slate-600',  icon: 'text-slate-400',  badge: 'bg-slate-500/15 text-slate-400 border border-slate-500/30' },
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
    <div className="flex flex-col items-center justify-center py-14 gap-2">
      <Activity size={28} className="text-slate-700" />
      <p className="text-sm text-slate-500">No activity yet</p>
      <p className="text-xs text-slate-600">Run your first campaign to see actions here</p>
    </div>
  )

  return (
    <div className="divide-y divide-slate-800/50 max-h-72 overflow-y-auto">
      {logs.map((log) => {
        const { color, Icon, label } = logCfg(log)
        const cc = COLOR_CLASSES[color]
        return (
          <div
            key={log.id}
            className={clsx(
              'flex items-center gap-3 px-5 py-3 border-l-2 hover:bg-slate-700/20 transition-colors',
              cc.border
            )}
          >
            <Icon size={14} className={clsx('shrink-0', cc.icon)} />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-slate-200 truncate">{log.business_name}</p>
              {(log.niche || log.city) && (
                <p className="text-xs text-slate-600 truncate">
                  {[log.niche, log.city].filter(Boolean).join(' · ')}
                </p>
              )}
            </div>
            <div className="text-right shrink-0">
              <span className={clsx('badge text-[10px] px-2 py-0.5', cc.badge)}>{label}</span>
              <p className="text-[10px] text-slate-600 mt-1">{timeAgo(log.timestamp)}</p>
            </div>
          </div>
        )
      })}
    </div>
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
    <div className="p-5 space-y-4 h-full flex flex-col">
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg bg-brand-600/20 border border-brand-600/30 flex items-center justify-center">
          <Rocket size={13} className="text-brand-400" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-slate-200">Quick Launch</h3>
          <p className="text-[10px] text-slate-500">Scrape + generate + send</p>
        </div>
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
          className="btn-primary w-full justify-center text-xs py-2.5"
        >
          {isBusy
            ? <><RefreshCw size={12} className="animate-spin" /> Running...</>
            : <><Rocket size={12} /> Launch Scraper</>}
        </button>

        <div className="relative flex items-center gap-2">
          <div className="flex-1 h-px bg-slate-700/60" />
          <span className="text-[10px] text-slate-600 shrink-0">or</span>
          <div className="flex-1 h-px bg-slate-700/60" />
        </div>

        <button
          onClick={() => runNowMut.mutate()}
          disabled={runNowMut.isPending}
          className="btn-secondary w-full justify-center text-xs py-2"
        >
          {runNowMut.isPending
            ? <><RefreshCw size={12} className="animate-spin" /> Queuing...</>
            : <><PlayCircle size={12} /> Run Campaign Now</>}
        </button>
        <p className="text-[10px] text-slate-600 text-center">
          AI generate + send all PENDING leads
        </p>
      </div>
    </div>
  )
}

// ─── Custom Recharts Tooltip ─────────────────────────────────────────────────

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-xs shadow-xl">
      <p className="text-slate-400 mb-1.5 font-medium">{label}</p>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full" style={{ background: p.color }} />
          <span className="text-slate-400">{p.name}:</span>
          <span className="text-slate-100 font-semibold">{p.value}</span>
        </div>
      ))}
    </div>
  )
}

// ─── Pie chart colours ───────────────────────────────────────────────────────

const PIE_COLORS = ['#f59e0b', '#3b82f6', '#10b981', '#64748b']

// ─── Dashboard ───────────────────────────────────────────────────────────────

export default function Dashboard() {
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

  // Est. Revenue — pulled from backend (avg_deal_value is configurable in Settings)
  const estRevenue = stats?.estimated_revenue ?? 0
  const sentToday  = (stats?.email_sent_today || 0) + (stats?.whatsapp_sent_today || 0)

  const pieData = stats
    ? [
        { name: 'Pending',  value: stats.pending  || 0 },
        { name: 'Sent',     value: stats.sent     || 0 },
        { name: 'Replied',  value: stats.replied  || 0 },
        { name: 'Skipped',  value: stats.skipped  || 0 },
      ].filter((d) => d.value > 0)
    : []

  const hasWeekly = weeklyData.some((d) => d.leads_created > 0 || d.total_sent > 0)

  return (
    <div className="p-6 space-y-5">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Dashboard</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Live overview · auto-refreshes every 15 s
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          {statsError ? (
            <span className="flex items-center gap-1.5 text-red-400">
              <XCircle size={12} /> Connection error
            </span>
          ) : (
            <>
              <CheckCircle2 size={12} className={clsx(statsRefetching ? 'text-slate-500 animate-pulse' : 'text-emerald-500')} />
              {stats ? 'Live' : 'Loading...'}
            </>
          )}
        </div>
      </div>

      {/* ── Row 1: Stat cards ─────────────────────────────────────────── */}
      {statsError ? (
        <ErrorState message="Couldn't load dashboard stats." onRetry={refetchStats} retrying={statsRefetching} />
      ) : statsLoading ? (
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => <SkeletonStatCard key={i} />)}
        </div>
      ) : (
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          <StatCard
            label="Total Leads"
            value={stats?.total_leads ?? '—'}
            icon={Users}
            color="brand"
            sub={`${stats?.pending ?? 0} pending`}
          />
          <StatCard
            label="Sent Today"
            value={stats ? sentToday : '—'}
            icon={Send}
            color="blue"
            sub={`${stats?.email_sent_today ?? 0} email · ${stats?.whatsapp_sent_today ?? 0} wa`}
          />
          <StatCard
            label="Reply Rate"
            value={stats ? `${stats.reply_rate}%` : '—'}
            icon={TrendingUp}
            color="purple"
            sub={`${stats?.replied ?? 0} total replies`}
          />
          <StatCard
            label="Est. Revenue"
            value={stats ? fmtCurrency(estRevenue) : '—'}
            icon={DollarSign}
            color="amber"
            sub={`${stats?.replied ?? 0} replies converted`}
          />
        </div>
      )}

      {/* ── Row 2: Charts ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

        {/* Area chart — Leads Found vs Messages Sent */}
        <div className="card p-5 lg:col-span-2">
          <div className="flex items-start justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-slate-200">Leads Found vs Messages Sent</h3>
              <p className="text-[11px] text-slate-500 mt-0.5">7-day trend</p>
            </div>
            <div className="flex items-center gap-4 text-[10px] text-slate-500">
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-2.5 h-0.5 rounded-full bg-indigo-500" /> Leads Found
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-2.5 h-0.5 rounded-full bg-emerald-500" /> Sent
              </span>
            </div>
          </div>

          {weeklyError ? (
            <ErrorState message="Couldn't load the weekly trend." onRetry={refetchWeekly} className="h-[210px] justify-center" />
          ) : hasWeekly ? (
            <ResponsiveContainer width="100%" height={210}>
              <AreaChart data={weeklyData} margin={{ top: 5, right: 5, bottom: 0, left: -20 }}>
                <defs>
                  <linearGradient id="gradLeads" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#6366f1" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0}    />
                  </linearGradient>
                  <linearGradient id="gradSent" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#10b981" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0}    />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                <XAxis
                  dataKey="day_label"
                  tick={{ fontSize: 10, fill: '#64748b' }}
                  axisLine={false} tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: '#64748b' }}
                  axisLine={false} tickLine={false}
                  allowDecimals={false}
                />
                <Tooltip content={<ChartTooltip />} />
                <Area
                  type="monotone" dataKey="leads_created" name="Leads Found"
                  stroke="#6366f1" strokeWidth={2.5} fill="url(#gradLeads)"
                  dot={false} activeDot={{ r: 4, fill: '#6366f1', strokeWidth: 0 }}
                />
                <Area
                  type="monotone" dataKey="total_sent" name="Messages Sent"
                  stroke="#10b981" strokeWidth={2.5} fill="url(#gradSent)"
                  dot={false} activeDot={{ r: 4, fill: '#10b981', strokeWidth: 0 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex flex-col items-center justify-center h-[210px] gap-2">
              <TrendingUp size={32} className="text-slate-700" />
              <p className="text-sm text-slate-500">No activity yet</p>
              <p className="text-xs text-slate-700">Start a campaign to see the chart populate</p>
            </div>
          )}
        </div>

        {/* Pie — Lead Distribution */}
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-slate-200 mb-4">Lead Distribution</h3>
          {pieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={210}>
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%" cy="46%"
                  innerRadius={56} outerRadius={82}
                  paddingAngle={3} dataKey="value"
                >
                  {pieData.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} strokeWidth={0} />
                  ))}
                </Pie>
                <Legend
                  iconSize={7}
                  wrapperStyle={{ fontSize: 10, color: '#94a3b8', paddingTop: 8 }}
                />
                <Tooltip content={<ChartTooltip />} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex flex-col items-center justify-center h-[210px] gap-2">
              <Users size={28} className="text-slate-700" />
              {stats?.total_leads > 0 ? (
                <>
                  <p className="text-sm text-slate-500">{stats.total_leads} leads ready</p>
                  <p className="text-xs text-slate-600">Run a campaign to send them</p>
                </>
              ) : (
                <p className="text-sm text-slate-600">No leads yet</p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── Row 3: Activity + Quick Launch ────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

        {/* Recent Activity — spans 2 cols */}
        <div className="card lg:col-span-2 overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-700/50">
            <div className="flex items-center gap-2">
              <Activity size={14} className="text-slate-400" />
              <h3 className="text-sm font-semibold text-slate-200">Recent Activity</h3>
            </div>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-700/60 text-slate-400 border border-slate-600/40">
              Last {logs.length} actions · live
            </span>
          </div>
          <ActivityFeed logs={logs} isLoading={logsLoading} />
        </div>

        {/* Quick Launch */}
        <div className="card overflow-hidden">
          <QuickLaunchPanel engineStatus={engineStatus} />
        </div>
      </div>

      {/* ── Row 4: Secondary stats strip ──────────────────────────────── */}
      {statsLoading ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="card p-4 border border-slate-700/30 space-y-2">
              <Skeleton className="h-2.5 w-20" />
              <Skeleton className="h-6 w-12" />
            </div>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="card p-4 border border-slate-700/30">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider">Skipped</p>
            <p className="text-xl font-bold text-slate-300 mt-1">{stats?.skipped ?? '—'}</p>
          </div>
          <div className="card p-4 border border-slate-700/30">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider">Emails Today</p>
            <p className="text-xl font-bold text-purple-400 mt-1">{stats?.email_sent_today ?? '—'}</p>
          </div>
          <div className="card p-4 border border-slate-700/30">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider">WhatsApp Today</p>
            <p className="text-xl font-bold text-green-400 mt-1">{stats?.whatsapp_sent_today ?? '—'}</p>
          </div>
          <div className="card p-4 border border-slate-700/30">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider">Conversion %</p>
            <p className="text-xl font-bold text-amber-400 mt-1">
              {stats?.sent
                ? `${((stats.replied / stats.sent) * 100).toFixed(1)}%`
                : '—'}
            </p>
          </div>
        </div>
      )}

      {/* ── Row 5: Lead Quality Scoring ───────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <div className="card p-4 border border-red-500/20 bg-red-500/5">
          <div className="flex items-center gap-2 mb-2">
            <Flame size={14} className="text-red-400" />
            <p className="text-[10px] text-red-400 uppercase tracking-wider font-semibold">HOT Leads</p>
          </div>
          <p className="text-2xl font-bold text-red-300">{stats?.hot_leads ?? '—'}</p>
          <p className="text-[10px] text-slate-600 mt-1">Score 70–100 · High priority</p>
        </div>
        <div className="card p-4 border border-amber-500/20 bg-amber-500/5">
          <div className="flex items-center gap-2 mb-2">
            <Sun size={14} className="text-amber-400" />
            <p className="text-[10px] text-amber-400 uppercase tracking-wider font-semibold">WARM Leads</p>
          </div>
          <p className="text-2xl font-bold text-amber-300">{stats?.warm_leads ?? '—'}</p>
          <p className="text-[10px] text-slate-600 mt-1">Score 40–69 · Worth sending</p>
        </div>
        <div className="card p-4 border border-blue-500/20 bg-blue-500/5">
          <div className="flex items-center gap-2 mb-2">
            <Snowflake size={14} className="text-blue-400" />
            <p className="text-[10px] text-blue-400 uppercase tracking-wider font-semibold">COLD Leads</p>
          </div>
          <p className="text-2xl font-bold text-blue-300">{stats?.cold_leads ?? '—'}</p>
          <p className="text-[10px] text-slate-600 mt-1">Score 0–39 · Incomplete data</p>
        </div>
        <div className="card p-4 border border-brand-500/20 bg-brand-500/5">
          <div className="flex items-center gap-2 mb-2">
            <Inbox size={14} className="text-brand-400" />
            <p className="text-[10px] text-brand-400 uppercase tracking-wider font-semibold">Unread Replies</p>
          </div>
          <p className="text-2xl font-bold text-brand-300">{stats?.unread_replies ?? '—'}</p>
          <p className="text-[10px] text-slate-600 mt-1">New inbox messages</p>
        </div>
      </div>

    </div>
  )
}
