import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { Search, Loader2, MapPin, Mail, Phone, Globe, XCircle, FileText, Send as SendIcon, ArrowLeft, Bot } from 'lucide-react'
import toast from 'react-hot-toast'
import { discoveryApi, leadsApi } from '../api/client'
import { RESEARCH_BADGE, RESEARCH_LABEL } from '../lib/badges'
import { researchSentToast } from '../lib/researchToast'
import ScoreBadge from '../components/ScoreBadge'
import { SkeletonTableRows } from '../components/ui/Skeleton'
import EmptyState from '../components/ui/EmptyState'
import ErrorState from '../components/ui/ErrorState'
import SendToCampaignModal from '../components/lead-search/SendToCampaignModal'
import BackToFindLeads from '../components/BackToFindLeads'

const ACTIVE_STATUSES = new Set(['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'])
const TERMINAL_STATUSES = new Set(['COMPLETED', 'FAILED', 'CANCELLED'])
const LS_KEY = 'autolead_discovery_run'

// The run id is persisted outside React state so navigating away / refreshing
// the page never loses track of a job that is still running on the backend.
function loadStoredRunId() {
  try { return localStorage.getItem(LS_KEY) || null } catch { return null }
}
function storeRunId(id) {
  try { id ? localStorage.setItem(LS_KEY, String(id)) : localStorage.removeItem(LS_KEY) } catch { /* private mode */ }
}

const STAGE_LABEL = {
  QUEUED:            'Queued…',
  RUNNING:           'Searching…',
  CANCEL_REQUESTED:  'Cancelling…',
  COMPLETED:         'Done',
  FAILED:            'Failed',
  CANCELLED:         'Cancelled',
}

function ResultsTable({ leads, selected, onToggle, onToggleAll }) {
  const allSelected = leads.length > 0 && leads.every((l) => selected.has(l.id))
  const someSelected = leads.some((l) => selected.has(l.id)) && !allSelected

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left text-[10px] font-bold uppercase tracking-widest text-slate-500">
              <th className="px-3 py-2.5 w-9">
                <input
                  type="checkbox"
                  aria-label="Select all"
                  checked={allSelected}
                  ref={(el) => { if (el) el.indeterminate = someSelected }}
                  onChange={onToggleAll}
                />
              </th>
              <th className="px-4 py-2.5">Business</th>
              <th className="px-4 py-2.5">Contact</th>
              <th className="px-4 py-2.5">Website</th>
              <th className="px-4 py-2.5">Score</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((lead) => (
              <tr key={lead.id} className="border-b border-slate-800/60 last:border-0">
                <td className="px-3 py-3">
                  <input
                    type="checkbox"
                    aria-label={`Select ${lead.business_name}`}
                    checked={selected.has(lead.id)}
                    onChange={() => onToggle(lead.id)}
                  />
                </td>
                <td className="px-4 py-3">
                  <p className="font-medium text-slate-200">{lead.business_name}</p>
                  <p className="text-xs text-slate-500">
                    {lead.niche}{lead.city ? ` · ${lead.city}` : ''}
                    {lead.research_status && lead.research_status !== 'NOT_STARTED' && (
                      <span className={RESEARCH_BADGE[lead.research_status] || 'text-slate-500'}>
                        {' · '}{RESEARCH_LABEL[lead.research_status] || lead.research_status}
                      </span>
                    )}
                  </p>
                </td>
                <td className="px-4 py-3 text-xs text-slate-400 space-y-1">
                  {lead.email && (
                    <div className="flex items-center gap-1.5"><Mail size={11} />{lead.email}</div>
                  )}
                  {lead.phone && (
                    <div className="flex items-center gap-1.5"><Phone size={11} />{lead.phone}</div>
                  )}
                  {!lead.email && !lead.phone && <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-3 text-xs">
                  {lead.website ? (
                    <a
                      href={lead.website} target="_blank" rel="noreferrer"
                      className="flex items-center gap-1.5 text-brand-400 hover:text-brand-300 truncate max-w-[220px]"
                    >
                      <Globe size={11} className="shrink-0" />
                      <span className="truncate">{lead.website}</span>
                    </a>
                  ) : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-3">
                  {lead.score > 0 ? (
                    <ScoreBadge score={lead.score} label={lead.score_label} />
                  ) : (
                    <span className="text-[10px] text-slate-600 italic">Not scored yet</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function SelectionActionBar({ count, onClear, onSend, onResearch, researching }) {
  if (count === 0) return null
  return (
    <div className="sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-brand-500/40 bg-slate-900/95 px-4 py-2.5 shadow-lg">
      <span className="text-xs font-semibold text-brand-300">{count} selected</span>
      <button className="btn-secondary text-xs" onClick={onClear}>Clear</button>
      <div className="flex-1" />
      <button className="btn-secondary text-xs" onClick={onResearch} disabled={researching}>
        {researching ? <Loader2 size={12} className="animate-spin" /> : <Bot size={12} />} Send to Research Agent ({count})
      </button>
      <button className="btn-secondary text-xs opacity-50 cursor-not-allowed"
              disabled title="Coming in a later step">
        <FileText size={12} /> Generate Reports
      </button>
      <button className="btn-primary text-xs" onClick={onSend}>
        <SendIcon size={12} /> Send to Email Campaign
      </button>
    </div>
  )
}

export default function LeadSearchManual() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [location, setLocation] = useState('')
  const [targetCount, setTargetCount] = useState(20)
  const [runId, setRunId] = useState(loadStoredRunId)

  // On mount, if we have no run id (fresh tab, cleared storage), ask the
  // backend whether a run is already in flight and reconnect to it.
  useEffect(() => {
    if (runId) return
    let cancelled = false
    discoveryApi.active()
      .then((run) => { if (!cancelled && run?.id) { setRunId(String(run.id)); storeRunId(run.id) } })
      .catch(() => { /* nothing to reconnect to */ })
    return () => { cancelled = true }
  }, [runId])

  const searchMutation = useMutation({
    mutationFn: () => discoveryApi.search({
      query, niche: query, city: location, target_count: Number(targetCount) || 20,
    }),
    onSuccess: (data) => {
      setRunId(String(data.run_id))
      storeRunId(data.run_id)
      queryClient.removeQueries({ queryKey: ['discovery-results'] })
    },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Could not start search'),
  })

  const statusQuery = useQuery({
    queryKey: ['discovery-status', runId],
    queryFn: () => discoveryApi.status(runId),
    enabled: !!runId,
    retry: false,
    refetchInterval: (query) => (query.state.data && ACTIVE_STATUSES.has(query.state.data.status) ? 1500 : false),
  })

  // A stored id pointing at a run that no longer exists — drop it and reset.
  useEffect(() => {
    if (statusQuery.error?.response?.status === 404) {
      storeRunId(null)
      setRunId(null)
    }
  }, [statusQuery.error])

  const run = statusQuery.data
  const isActive = run && ACTIVE_STATUSES.has(run.status)

  const resultsQuery = useQuery({
    queryKey: ['discovery-results', runId],
    queryFn: () => discoveryApi.results(runId),
    enabled: !!runId && !!run && TERMINAL_STATUSES.has(run.status),
  })

  const leads = resultsQuery.data?.leads || []

  const [selected, setSelected] = useState(() => new Set())
  const [showSendModal, setShowSendModal] = useState(false)
  const [researching, setResearching] = useState(false)

  async function handleResearch() {
    setResearching(true)
    try {
      const res = await leadsApi.research([...selected], 'manual')
      researchSentToast(res, navigate)
      resultsQuery.refetch()
      queryClient.invalidateQueries({ queryKey: ['research-sessions'] })
      setSelected(new Set())
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not start research')
    } finally {
      setResearching(false)
    }
  }

  const toggle = (id) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  const toggleAll = () =>
    setSelected((prev) => {
      const ids = leads.map((l) => l.id)
      const allOn = ids.length > 0 && ids.every((id) => prev.has(id))
      return allOn ? new Set() : new Set(ids)
    })
  const clearSelection = () => setSelected(new Set())

  // Any change of runId (new search, mount-reconnect, 404 reset) means the
  // current results set is about to change — drop the selection so "N selected"
  // never refers to ids that aren't in the results anymore.
  useEffect(() => { setSelected(new Set()) }, [runId])

  function handleSubmit(e) {
    e.preventDefault()
    if (!query.trim() || !location.trim()) {
      toast.error('Please fill in what you’re looking for and a location')
      return
    }
    storeRunId(null)
    setRunId(null)
    clearSelection()
    searchMutation.mutate()
  }

  return (
    <div className="px-4 sm:px-8 py-8 max-w-5xl mx-auto space-y-6">
      <header>
        <BackToFindLeads />
        <h1 className="text-page">Quick search</h1>
        <p className="text-support mt-1">
          A one-time list of matching businesses. We pick where to look, and it keeps running if you leave.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <label className="block">
            <span className="text-xs font-medium text-slate-400">What are you looking for?</span>
            <input
              type="text" value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. Dental clinics"
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-brand-500 focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-slate-400">Location</span>
            <input
              type="text" value={location} onChange={(e) => setLocation(e.target.value)}
              placeholder="e.g. California"
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-brand-500 focus:outline-none"
            />
          </label>
        </div>
        <div className="flex items-end gap-4">
          <label className="block">
            <span className="text-xs font-medium text-slate-400">Number of leads</span>
            <input
              type="number" min={1} max={100} value={targetCount}
              onChange={(e) => setTargetCount(e.target.value)}
              className="mt-1 w-28 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 focus:border-brand-500 focus:outline-none"
            />
          </label>
          <button
            type="submit"
            disabled={searchMutation.isPending || isActive}
            className="btn-primary flex items-center gap-2"
          >
            {(searchMutation.isPending || isActive) ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            Search Leads
          </button>
        </div>
      </form>

      {isActive && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 flex items-center gap-3">
          <Loader2 size={16} className="animate-spin text-brand-400" />
          <div>
            <p className="text-sm text-slate-300">{STAGE_LABEL[run.status] || run.status}</p>
            <p className="text-xs text-slate-500">
              {run.planner_intent
                ? `Understood as ${run.planner_intent.replace(/_/g, ' ').toLowerCase()} — checking the best source(s) for you`
                : 'Figuring out the best source for this search…'}
            </p>
          </div>
        </div>
      )}

      {run?.status === 'FAILED' && (
        <ErrorState
          message={run.error_message || 'The search failed unexpectedly.'}
          onRetry={() => searchMutation.mutate()}
          retrying={searchMutation.isPending}
        />
      )}

      {statusQuery.isError && statusQuery.error?.response?.status !== 404 && (
        <ErrorState
          message="Could not check search status."
          onRetry={() => statusQuery.refetch()}
          retrying={statusQuery.isFetching}
        />
      )}

      {!!run && TERMINAL_STATUSES.has(run.status) && resultsQuery.isLoading && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/30 overflow-hidden">
          <table className="w-full text-sm"><tbody><SkeletonTableRows rows={4} cols={5} /></tbody></table>
        </div>
      )}

      {!!run && TERMINAL_STATUSES.has(run.status) && !resultsQuery.isLoading && leads.length === 0 && run.status !== 'FAILED' && (
        <EmptyState
          icon={XCircle}
          title="No leads found"
          description="Try a broader location or a different search term."
        />
      )}

      {leads.length > 0 && (
        <>
          <SelectionActionBar
            count={selected.size}
            onClear={clearSelection}
            onSend={() => setShowSendModal(true)}
            onResearch={handleResearch}
            researching={researching}
          />
          <p className="text-xs text-slate-500 flex items-center gap-1.5">
            <MapPin size={11} /> Found {leads.length} lead{leads.length === 1 ? '' : 's'}
            {run && !TERMINAL_STATUSES.has(run.status) ? ' so far…' : ''}
          </p>
          <ResultsTable
            leads={leads}
            selected={selected}
            onToggle={toggle}
            onToggleAll={toggleAll}
          />
        </>
      )}

      {showSendModal && (
        <SendToCampaignModal
          leadIds={[...selected]}
          leads={leads.filter((l) => selected.has(l.id))}
          defaultName={
            [query.trim(), location.trim()].filter(Boolean).join(' — ').slice(0, 120)
            || 'Lead Search campaign'
          }
          onClose={() => setShowSendModal(false)}
          onDone={() => { setShowSendModal(false); clearSelection() }}
        />
      )}
    </div>
  )
}
