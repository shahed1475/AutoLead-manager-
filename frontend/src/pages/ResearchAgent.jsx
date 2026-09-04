import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Loader2, Mail, Phone, Globe, User, XCircle, StopCircle, Download, RotateCcw, Send } from 'lucide-react'
import toast from 'react-hot-toast'
import { researchAgentApi } from '../api/client'
import { SkeletonTableRows } from '../components/ui/Skeleton'
import EmptyState from '../components/ui/EmptyState'
import ErrorState from '../components/ui/ErrorState'
import ResearchResultDrawer from '../components/ResearchResultDrawer'

const ACTIVE_STATUSES = new Set(['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'])
const TERMINAL_STATUSES = new Set(['COMPLETED', 'FAILED', 'CANCELLED'])
const LS_KEY = 'autolead_research_session'

// Session id persisted outside React state so navigating away / refreshing
// never loses a job that is still running on the backend worker.
function loadStoredId() {
  try { return localStorage.getItem(LS_KEY) || null } catch { return null }
}
function storeId(id) {
  try { id ? localStorage.setItem(LS_KEY, String(id)) : localStorage.removeItem(LS_KEY) } catch { /* private mode */ }
}

function elapsed(startIso) {
  if (!startIso) return null
  const start = new Date(startIso.endsWith('Z') || startIso.includes('+') ? startIso : `${startIso}Z`).getTime()
  if (Number.isNaN(start)) return null
  const s = Math.max(0, Math.floor((Date.now() - start) / 1000))
  const m = Math.floor(s / 60)
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`
}

const STATUS_CLS = {
  COMPLETE: 'border-emerald-500/50 bg-emerald-500/15 text-emerald-300',
  PARTIAL:  'border-amber-500/50 bg-amber-500/15 text-amber-300',
  FAILED:   'border-red-500/50 bg-red-500/15 text-red-300',
  PENDING:  'border-slate-500/50 bg-slate-500/15 text-slate-300',
}

function ResultsTable({ results, onRowClick }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left text-[10px] font-bold uppercase tracking-widest text-slate-500">
              <th className="px-4 py-2.5">Business</th>
              <th className="px-4 py-2.5">Location</th>
              <th className="px-4 py-2.5">Contact</th>
              <th className="px-4 py-2.5">Website</th>
              <th className="px-4 py-2.5">Management</th>
              <th className="px-4 py-2.5">Confidence</th>
              <th className="px-4 py-2.5">Status</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <tr
                key={r.id}
                onClick={() => onRowClick(r)}
                className="border-b border-slate-800/60 last:border-0 align-top cursor-pointer hover:bg-slate-800/40"
              >
                <td className="px-4 py-3 font-medium text-slate-200">{r.business_name || '—'}</td>
                <td className="px-4 py-3 text-xs text-slate-400">
                  {[r.city, r.state, r.country].filter(Boolean).join(', ') || '—'}
                </td>
                <td className="px-4 py-3 text-xs text-slate-400 space-y-1">
                  {r.business_phone && <div className="flex items-center gap-1.5"><Phone size={11} />{r.business_phone}</div>}
                  {r.business_email ? (
                    <div className="flex items-center gap-1.5"><Mail size={11} />{r.business_email}</div>
                  ) : r.business_email_status === 'SECURE_WEB_FORM' ? (
                    <div className="text-slate-600 italic">Secure web form</div>
                  ) : null}
                  {!r.business_phone && !r.business_email && <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-3 text-xs">
                  {r.business_website ? (
                    <a href={r.business_website} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
                      className="flex items-center gap-1.5 text-brand-400 hover:text-brand-300 truncate max-w-[180px]">
                      <Globe size={11} className="shrink-0" /><span className="truncate">{r.business_website}</span>
                    </a>
                  ) : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-3 text-xs text-slate-400">
                  {r.management_contact_name ? (
                    <div className="space-y-0.5">
                      <div className="flex items-center gap-1.5 text-slate-300"><User size={11} />{r.management_contact_name}</div>
                      {r.management_title && <div className="text-slate-500">{r.management_title}</div>}
                      {r.management_phone && <div className="text-slate-500">{r.management_phone} {r.management_phone_type === 'BUSINESS' && '(business line)'}</div>}
                      {r.management_email && <div className="text-slate-500">{r.management_email}</div>}
                    </div>
                  ) : <span className="text-slate-600 italic">Not found</span>}
                </td>
                <td className="px-4 py-3 text-xs text-slate-400">{Math.round((r.confidence || 0) * 100)}%</td>
                <td className="px-4 py-3">
                  <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-full border ${STATUS_CLS[r.research_status] || STATUS_CLS.PENDING}`}>
                    {r.research_status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-widest text-slate-500">{label}</p>
      <p className="text-slate-200 font-medium truncate">{value ?? '—'}</p>
    </div>
  )
}

const MODE_BADGE = {
  handoff:   'bg-indigo-500/15 text-indigo-300 border border-indigo-500/30',
  discovery: 'bg-slate-600/25 text-slate-300 border border-slate-600/40',
}
const SUBMISSION_LABEL = {
  manual:                 'manual send',
  lead_search_automation: 'auto — Lead Search Automation',
  manual_from_automation: 'manual — from Automation results',
}
const S_STATUS_CLS = {
  RUNNING: 'text-emerald-400', QUEUED: 'text-amber-400', CANCEL_REQUESTED: 'text-amber-400',
  COMPLETED: 'text-slate-400', FAILED: 'text-rose-400', CANCELLED: 'text-slate-500',
}

function seedCount(s) {
  try { return (JSON.parse(s.seed_lead_ids || '[]') || []).length } catch { return 0 }
}
function relTime(iso) {
  if (!iso) return ''
  const t = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).getTime()
  const secs = Math.floor((Date.now() - t) / 1000)
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

function SessionsPanel({ currentId, onSelect }) {
  const [filter, setFilter] = useState('all')
  const { data } = useQuery({
    queryKey: ['research-sessions', filter],
    queryFn: () => researchAgentApi.sessions(filter === 'all' ? { limit: 25 } : { mode: filter, limit: 25 }),
    refetchInterval: (q) =>
      (q.state.data?.sessions?.some((s) => ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(s.status)) ? 5000 : false),
  })
  const sessions = data?.sessions || []
  const chips = [['all', 'All'], ['handoff', 'From Lead Search'], ['discovery', 'Started here']]

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-800">
        <span className="text-xs font-semibold text-slate-300">Sessions</span>
        <div className="flex gap-1">
          {chips.map(([v, label]) => (
            <button key={v} onClick={() => setFilter(v)}
              className={`text-[10px] px-2 py-0.5 rounded-full border ${
                filter === v ? 'border-brand-500/50 bg-brand-600/20 text-brand-300' : 'border-slate-700 text-slate-500 hover:text-slate-300'
              }`}>
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="max-h-64 overflow-y-auto divide-y divide-slate-800/60">
        {sessions.length === 0 && (
          <p className="px-4 py-6 text-center text-xs text-slate-600">No research sessions yet.</p>
        )}
        {sessions.map((s) => {
          const isHandoff = s.mode === 'handoff'
          const n = seedCount(s)
          return (
            <button key={s.id} onClick={() => onSelect(s.id)}
              className={`w-full text-left px-4 py-2.5 flex items-center gap-3 hover:bg-slate-800/40 transition-colors ${
                String(s.id) === String(currentId) ? 'bg-brand-600/10' : ''
              }`}>
              <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded ${MODE_BADGE[s.mode] || MODE_BADGE.discovery}`}>
                {isHandoff ? 'Handoff' : 'Discovery'}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-slate-200 truncate">
                  {isHandoff
                    ? `${n || s.target_count} lead${(n || s.target_count) === 1 ? '' : 's'}`
                    : `${s.niche || '—'}${s.location ? ` · ${s.location}` : ''}`}
                </p>
                <p className="text-[10px] text-slate-500 truncate">
                  {isHandoff ? (SUBMISSION_LABEL[s.submission_source] || 'handoff') : 'started here'}
                  {' · '}{relTime(s.created_at)}
                </p>
              </div>
              <div className="text-right shrink-0">
                <p className={`text-[11px] font-semibold ${S_STATUS_CLS[s.status] || 'text-slate-400'}`}>{s.status}</p>
                <p className="text-[10px] text-slate-600">{s.leads_completed ?? 0}/{s.leads_found ?? 0}</p>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default function ResearchAgent() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const urlSession = searchParams.get('session')
  const [niche, setNiche] = useState('')
  const [location, setLocation] = useState('')
  const [country, setCountry] = useState('')
  const [targetCount, setTargetCount] = useState(10)
  const [sessionId, setSessionId] = useState(() => urlSession || loadStoredId())
  const [detail, setDetail] = useState(null)

  function selectSession(id) {
    setSessionId(String(id))
    storeId(id)
    setSearchParams((p) => { p.set('session', String(id)); return p }, { replace: true })
    queryClient.removeQueries({ queryKey: ['research-results'] })
    setDetail(null)
  }

  // Precedence: ?session= URL param (a "View in Research Agent" link) wins.
  useEffect(() => {
    if (urlSession && String(urlSession) !== String(sessionId)) {
      setSessionId(String(urlSession))
      storeId(urlSession)
      queryClient.removeQueries({ queryKey: ['research-results'] })
    }
  }, [urlSession]) // eslint-disable-line react-hooks/exhaustive-deps

  // Else reconnect to an in-flight session on mount if we have no id at all.
  useEffect(() => {
    if (sessionId) return
    let cancelled = false
    researchAgentApi.active()
      .then((s) => { if (!cancelled && s?.id) { setSessionId(String(s.id)); storeId(s.id) } })
      .catch(() => {})
    return () => { cancelled = true }
  }, [sessionId])

  const startMutation = useMutation({
    mutationFn: () => researchAgentApi.start({
      niche, location, country, target_count: Number(targetCount) || 10,
    }),
    onSuccess: (data) => {
      selectSession(data.session_id)
      queryClient.invalidateQueries({ queryKey: ['research-sessions'] })
    },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Could not start research'),
  })

  const cancelMutation = useMutation({
    mutationFn: () => researchAgentApi.cancel(sessionId),
    onSuccess: () => toast.success('Cancelling…'),
  })

  const resumeMutation = useMutation({
    mutationFn: () => researchAgentApi.resume(sessionId),
    onSuccess: () => { toast.success('Resuming research…'); statusQuery.refetch() },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Could not resume'),
  })

  const statusQuery = useQuery({
    queryKey: ['research-status', sessionId],
    queryFn: () => researchAgentApi.status(sessionId),
    enabled: !!sessionId,
    retry: false,
    refetchInterval: (query) => (query.state.data && ACTIVE_STATUSES.has(query.state.data.status) ? 2000 : false),
  })

  useEffect(() => {
    if (statusQuery.error?.response?.status === 404) { storeId(null); setSessionId(null) }
  }, [statusQuery.error])

  const session = statusQuery.data
  const isActive = session && ACTIVE_STATUSES.has(session.status)

  const resultsQuery = useQuery({
    queryKey: ['research-results', sessionId],
    queryFn: () => researchAgentApi.results(sessionId),
    enabled: !!sessionId && (!!session) && (session.status === 'COMPLETED' || (session.leads_found || 0) > 0 || TERMINAL_STATUSES.has(session.status)),
    refetchInterval: isActive ? 3000 : false,
  })

  function handleSubmit(e) {
    e.preventDefault()
    if (!niche.trim() || (!location.trim() && !country.trim())) {
      toast.error('Please fill in a niche and a location or country')
      return
    }
    storeId(null)
    setSessionId(null)
    startMutation.mutate()
  }

  async function handleExport() {
    try {
      const blob = await researchAgentApi.exportCsv(sessionId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `research-${sessionId}.csv`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      toast.error('Could not export results')
    }
  }

  const results = resultsQuery.data?.results || []
  const canResume = session?.status === 'FAILED' && session?.resumable

  return (
    <div className="p-6 space-y-5 max-w-6xl">
      <div className="flex items-center gap-2">
        <Bot size={18} className="text-brand-400" />
        <h1 className="text-lg font-bold text-slate-100">Research Agent</h1>
      </div>
      <p className="text-sm text-slate-500 -mt-3">
        Deep, iterative browser research — a local AI decides what to search next, one step at a time.
        Runs on the backend: you can leave this page or refresh and it keeps going. No outreach is sent from here.
        Leads sent from <span className="text-slate-400">Lead Search</span> or <span className="text-slate-400">Lead Search Automation</span> land here as <span className="text-indigo-300">Handoff</span> sessions.
      </p>

      <SessionsPanel currentId={sessionId} onSelect={selectSession} />

      <form onSubmit={handleSubmit} className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
          <label className="block sm:col-span-2">
            <span className="text-xs font-medium text-slate-400">Niche</span>
            <input type="text" value={niche} onChange={(e) => setNiche(e.target.value)} placeholder="e.g. Dental clinics"
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-brand-500 focus:outline-none" />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-slate-400">Location</span>
            <input type="text" value={location} onChange={(e) => setLocation(e.target.value)} placeholder="e.g. Abbeville / California / Worldwide"
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-brand-500 focus:outline-none" />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-slate-400">Country</span>
            <input type="text" value={country} onChange={(e) => setCountry(e.target.value)} placeholder="e.g. USA"
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-brand-500 focus:outline-none" />
          </label>
        </div>
        <div className="flex items-end gap-4 flex-wrap">
          <label className="block">
            <span className="text-xs font-medium text-slate-400">Target leads</span>
            <input type="number" min={1} max={500} value={targetCount} onChange={(e) => setTargetCount(e.target.value)}
              className="mt-1 w-28 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 focus:border-brand-500 focus:outline-none" />
          </label>
          <button type="submit" disabled={startMutation.isPending || isActive} className="btn-primary flex items-center gap-2">
            {(startMutation.isPending || isActive) ? <Loader2 size={14} className="animate-spin" /> : <Bot size={14} />}
            Start Research
          </button>
          {isActive && (
            <button type="button" onClick={() => cancelMutation.mutate()} disabled={cancelMutation.isPending}
              className="btn-secondary flex items-center gap-2 text-xs">
              <StopCircle size={14} /> Cancel
            </button>
          )}
          {canResume && (
            <button type="button" onClick={() => resumeMutation.mutate()} disabled={resumeMutation.isPending}
              className="btn-secondary flex items-center gap-2 text-xs">
              <RotateCcw size={14} /> Resume
            </button>
          )}
          {sessionId && results.length > 0 && (
            <button type="button" onClick={handleExport}
              className="btn-secondary flex items-center gap-2 text-xs ml-auto">
              <Download size={14} /> Export CSV
            </button>
          )}
        </div>
      </form>

      {session && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 space-y-3">
        {session.mode === 'handoff' && (
          <div className="flex items-center gap-2 text-xs">
            <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded ${MODE_BADGE.handoff}`}>Handoff</span>
            <span className="text-slate-400">
              {seedCount(session) || session.target_count} lead{(seedCount(session) || session.target_count) === 1 ? '' : 's'}
              {' · '}{SUBMISSION_LABEL[session.submission_source] || 'sent from Lead Search'}
            </span>
          </div>
        )}
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-4 text-sm">
          <Stat label="Status" value={session.status} />
          <Stat label="Phase" value={session.research_phase} />
          <Stat label="Current action" value={session.current_action} />
          <Stat label="Current business" value={session.current_business} />
          <Stat label="Current source" value={session.current_source} />
          <Stat label="Current city" value={session.current_city} />
          <Stat label="Leads found" value={`${session.leads_found ?? 0} / ${session.target_count}`} />
          <Stat label="Researched" value={session.businesses_researched} />
          <Stat label="Skipped" value={session.businesses_skipped} />
          <Stat label="Completed / Failed" value={`${session.leads_completed ?? 0} / ${session.leads_failed ?? 0}`} />
          <Stat label="Elapsed" value={isActive ? elapsed(session.started_at) : null} />
          {session.resume_count > 0 && <Stat label="Resumed" value={`${session.resume_count}×`} />}
          {isActive && session.mode !== 'handoff' && session.current_query && (
            <div className="col-span-full flex items-center gap-2 text-xs text-slate-500">
              <Loader2 size={12} className="animate-spin" /> Searching: "{session.current_query}"
            </div>
          )}
        </div>
        </div>
      )}

      {session?.status === 'FAILED' && (
        <ErrorState message={session.error_message || 'The research session failed unexpectedly.'} />
      )}

      {session?.status === 'COMPLETED' && results.length === 0 && !resultsQuery.isLoading && (
        <EmptyState icon={XCircle} title="No leads researched" description="Try a broader location or a different niche." />
      )}

      {resultsQuery.isLoading && isActive && results.length === 0 && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/30 overflow-hidden">
          <table className="w-full text-sm"><tbody><SkeletonTableRows rows={3} cols={7} /></tbody></table>
        </div>
      )}

      {results.length > 0 && (
        <>
          <p className="text-xs text-slate-500">
            {results.length} researched business{results.length === 1 ? '' : 'es'}
            {isActive ? ' so far — click a row for full detail + evidence' : ' — click a row for full detail + evidence'}
          </p>
          <ResultsTable results={results} onRowClick={setDetail} />
        </>
      )}

      <ResearchResultDrawer result={detail} onClose={() => setDetail(null)} />
    </div>
  )
}
