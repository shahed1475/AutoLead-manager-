import { useEffect, useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { researchSentToast } from '../lib/researchToast'
import { Plus, Upload, Download, Search, RefreshCw, Sparkles, Trash2, X, Bot, SlidersHorizontal, ClipboardCheck } from 'lucide-react'
import { leadsApi, aiApi, campaignApi, enrichApi, auditApi } from '../api/client'
import LeadTable from '../components/LeadTable'
import CampaignControls from '../components/CampaignControls'
import ViewMessagesModal from '../components/ViewMessagesModal'
import EnrichmentDrawer from '../components/EnrichmentDrawer'
import ErrorState from '../components/ui/ErrorState'
import ResearchTitlesDialog from '../components/ResearchTitlesDialog'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { useFocusTrap } from '../hooks/useFocusTrap'
import toast from 'react-hot-toast'
import clsx from 'clsx'

const STATUSES = ['', 'PENDING', 'SENT', 'REPLIED', 'SKIPPED', 'DO_NOT_CONTACT']
const CHANNELS = ['', 'EMAIL', 'WHATSAPP', 'BOTH']
const SCORES   = ['', 'HOT', 'WARM', 'COLD']

const SCORE_STYLES = {
  HOT:  'border-red-500/50 bg-red-500/15 text-red-300',
  WARM: 'border-amber-500/50 bg-amber-500/15 text-amber-300',
  COLD: 'border-blue-500/50 bg-blue-500/15 text-blue-300',
}

const STATUS_BTN = {
  PENDING: {
    base: 'border-slate-700/50 text-slate-500',
    active: 'border-slate-500/50 bg-slate-500/15 text-slate-300',
  },
  SENT: {
    base: 'border-slate-700/50 text-slate-500',
    active: 'border-blue-500/50 bg-blue-500/15 text-blue-300',
  },
  REPLIED: {
    base: 'border-slate-700/50 text-slate-500',
    active: 'border-emerald-500/50 bg-emerald-500/15 text-emerald-300',
  },
  SKIPPED: {
    base: 'border-slate-700/50 text-slate-500',
    active: 'border-red-500/50 bg-red-500/15 text-red-300',
  },
  DO_NOT_CONTACT: {
    base: 'border-slate-700/50 text-slate-500',
    active: 'border-rose-600/50 bg-rose-600/15 text-rose-300',
  },
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

// CSV-escape a single field for client-side export (selected rows already in memory).
function csvField(v) {
  const s = v == null ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export default function Leads() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const fileRef = useRef()
  const [searchParams, setSearchParams] = useSearchParams()
  const [selected, setSelected] = useState([])
  const [showAdd, setShowAdd] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)   // phones: filters fold away
  const [viewLead, setViewLead] = useState(null)
  const [drawerLead, setDrawerLead] = useState(null)
  const [bulkDeleteConfirm, setBulkDeleteConfirm] = useState(false)
  const [form, setForm] = useState({
    business_name: '', email: '', phone: '', website: '', niche: '', city: '',
  })

  // ── URL is the source of truth for filters/sort/pagination ────────────────
  const page      = Number(searchParams.get('page') || 1)
  const pageSize  = Number(searchParams.get('page_size') || 50)
  const sortBy    = searchParams.get('sort_by') || 'score'
  const sortDir   = searchParams.get('sort_dir') || 'desc'
  const filters = {
    status:      searchParams.get('status') || '',
    channel:     searchParams.get('channel') || '',
    search:      searchParams.get('search') || '',
    niche:       searchParams.get('niche') || '',
    city:        searchParams.get('city') || '',
    date_from:   searchParams.get('date_from') || '',
    date_to:     searchParams.get('date_to') || '',
    score_label: searchParams.get('score_label') || '',
    source_type:     searchParams.get('source_type') || '',
    research_status: searchParams.get('research_status') || '',
    run_id:          searchParams.get('run_id') || '',   // one Find-leads search (LeadRun "See these leads")
  }

  function updateParams(patch) {
    const next = new URLSearchParams(searchParams)
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, String(v))
      else next.delete(k)
    }
    setSearchParams(next, { replace: true })
  }

  // Free-text inputs get their own local state + debounce before hitting the URL/API.
  const [searchInput, setSearchInput] = useState(filters.search)
  const [nicheInput, setNicheInput]   = useState(filters.niche)
  const [cityInput, setCityInput]     = useState(filters.city)
  const debouncedSearch = useDebouncedValue(searchInput)
  const debouncedNiche  = useDebouncedValue(nicheInput)
  const debouncedCity   = useDebouncedValue(cityInput)

  useEffect(() => {
    if (debouncedSearch !== filters.search) updateParams({ search: debouncedSearch, page: null })
  }, [debouncedSearch]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (debouncedNiche !== filters.niche) updateParams({ niche: debouncedNiche, page: null })
  }, [debouncedNiche]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (debouncedCity !== filters.city) updateParams({ city: debouncedCity, page: null })
  }, [debouncedCity]) // eslint-disable-line react-hooks/exhaustive-deps

  function setFilter(k, v) {
    updateParams({ [k]: v, page: null })
  }
  function setPage(p) { updateParams({ page: p > 1 ? p : null }) }
  function setPageSize(n) { updateParams({ page_size: n !== 50 ? n : null, page: null }) }
  function handleSort(field) {
    if (sortBy === field) updateParams({ sort_dir: sortDir === 'asc' ? 'desc' : 'asc' })
    else updateParams({ sort_by: field, sort_dir: 'desc' })
  }
  const toggleStatusFilter = (s) => setFilter('status', filters.status === s ? '' : s)

  const params = {
    page,
    page_size: pageSize,
    sort_by: sortBy,
    sort_dir: sortDir,
    ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)),
  }

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['leads', params],
    queryFn: () => leadsApi.list(params),
    // Find-leads runs add leads in the background: always reload on arrival
    // and keep the list current while the page is open.
    staleTime: 0,
    refetchOnMount: 'always',
    refetchInterval: 20_000,
  })

  // Audit the selected leads (background batch; progress polled while running).
  const auditProgress = useQuery({
    queryKey: ['audit-batch'],
    queryFn: auditApi.batchStatus,
    refetchInterval: (q) => (q.state.data?.running ? 2_000 : false),
  })
  const auditRunning = !!auditProgress.data?.running
  const wasAuditing = useRef(false)
  useEffect(() => {
    if (wasAuditing.current && !auditRunning) {
      toast.success(`Audited ${auditProgress.data?.done ?? 0} lead(s)`)
      qc.invalidateQueries({ queryKey: ['leads'] })
    }
    wasAuditing.current = auditRunning
  }, [auditRunning]) // eslint-disable-line react-hooks/exhaustive-deps
  const auditMut = useMutation({
    mutationFn: () => auditApi.batch(selected),
    onSuccess: () => { setSelected([]); auditProgress.refetch() },
    onError: (e) => toast.error(e.message || 'Could not start the audit'),
  })

  const statusCounts = (data?.items ?? []).reduce(
    (acc, item) => { acc[item.status] = (acc[item.status] || 0) + 1; return acc },
    { PENDING: 0, SENT: 0, REPLIED: 0, SKIPPED: 0 }
  )

  const invalidate = () => qc.invalidateQueries({ queryKey: ['leads'] })

  const enrichMut = useMutation({
    mutationFn: (id) => enrichApi.enrichLead(id),
    onSuccess: (d) => {
      invalidate()
      toast.success(`Enriched — Score: ${d.score} (${d.score_label})`)
    },
    onError: (e) => toast.error(e.message),
  })

  const scoreAllMut = useMutation({
    mutationFn: () => enrichApi.scoreAll(),
    onSuccess: () => { invalidate(); toast.success('Scoring queued for all leads') },
    onError: (e) => toast.error(e.message),
  })

  // Research: both the row action and the bulk button open the titles
  // dialog first; `researchTarget` is { ids, bulk } for the pending send.
  const [researchTarget, setResearchTarget] = useState(null)
  const researchMut = useMutation({
    mutationFn: ({ ids, bulk, titles }) => (bulk
      ? leadsApi.research(ids, 'manual', titles)
      : leadsApi.researchOne(ids[0], titles)),
    onSuccess: (res, { bulk }) => {
      if (bulk) setSelected([])
      setResearchTarget(null)
      invalidate()
      qc.invalidateQueries({ queryKey: ['research-sessions'] })
      researchSentToast(res, navigate)
    },
    onError: (e) => toast.error(e?.response?.data?.detail || 'Could not start research'),
  })
  function researchNiche(ids) {
    // Same rule the backend uses to name the session: the most common niche.
    const counts = {}
    for (const l of data?.items ?? []) {
      if (ids.includes(l.id) && l.niche) counts[l.niche] = (counts[l.niche] || 0) + 1
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] || ''
  }

  const deleteMut = useMutation({
    mutationFn: leadsApi.delete,
    onSuccess: () => { invalidate(); toast.success('Lead deleted') },
    onError: (e) => toast.error(e.message),
  })

  const skipMut = useMutation({
    mutationFn: leadsApi.skip,
    onSuccess: () => { invalidate(); toast.success('Lead skipped') },
    onError: (e) => toast.error(e.message),
  })

  const markRepliedMut = useMutation({
    mutationFn: (id) => leadsApi.updateStatus(id, 'REPLIED'),
    onSuccess: () => { invalidate(); toast.success('Marked as replied') },
    onError: (e) => toast.error(e.message),
  })

  const resendMut = useMutation({
    mutationFn: ({ id, channel }) => leadsApi.resend(id, channel),
    onSuccess: () => { invalidate(); toast.success('Message resent') },
    onError: (e) => toast.error(e.message),
  })

  const createMut = useMutation({
    mutationFn: leadsApi.create,
    onSuccess: () => {
      invalidate()
      toast.success('Lead added')
      setShowAdd(false)
      setForm({ business_name: '', email: '', phone: '', website: '', niche: '', city: '' })
    },
    onError: (e) => toast.error(e.message),
  })

  const importMut = useMutation({
    mutationFn: leadsApi.importCsv,
    onSuccess: (d) => {
      invalidate()
      toast.success(`Imported ${d.created} leads${d.errors.length ? ` (${d.errors.length} errors)` : ''}`)
    },
    onError: (e) => toast.error(e.message),
  })

  const [bulkDeleting, setBulkDeleting] = useState(false)
  async function handleBulkDelete() {
    setBulkDeleting(true)
    setBulkDeleteConfirm(false)
    const ids = [...selected]
    let ok = 0, fail = 0
    for (const id of ids) {
      try { await leadsApi.delete(id); ok++ } catch { fail++ }
    }
    setBulkDeleting(false)
    setSelected([])
    invalidate()
    if (fail) toast.error(`Deleted ${ok}, ${fail} failed`)
    else toast.success(`Deleted ${ok} lead${ok === 1 ? '' : 's'}`)
  }


  const [bulkExporting, setBulkExporting] = useState(false)
  async function handleExportSelected() {
    setBulkExporting(true)
    try {
      const leads = await Promise.all(selected.map((id) => leadsApi.get(id)))
      const cols = ['id', 'business_name', 'phone', 'email', 'website', 'niche', 'city', 'country', 'source', 'score', 'score_label', 'status', 'channel']
      const rows = [cols.join(',')]
      for (const l of leads) rows.push(cols.map((c) => csvField(l[c])).join(','))
      downloadBlob(new Blob([rows.join('\n')], { type: 'text/csv' }), `leads-selected-${selected.length}.csv`)
      toast.success(`Exported ${leads.length} lead${leads.length === 1 ? '' : 's'}`)
    } catch (e) {
      toast.error(e.message || 'Export failed')
    } finally {
      setBulkExporting(false)
    }
  }

  const handleExportCsv = async () => {
    try {
      const p = {}
      if (filters.status)      p.status      = filters.status
      if (filters.channel)     p.channel     = filters.channel
      if (filters.niche)       p.niche       = filters.niche
      if (filters.city)        p.city        = filters.city
      if (filters.search)      p.search      = filters.search
      if (filters.date_from)   p.date_from   = filters.date_from
      if (filters.date_to)     p.date_to     = filters.date_to
      if (filters.score_label) p.score_label = filters.score_label
      const blob = await leadsApi.exportCsv(p)
      downloadBlob(blob, 'leads-export.csv')
      toast.success('Export started')
    } catch (e) {
      toast.error(e.message)
    }
  }

  const addModalRef = useFocusTrap(showAdd)
  useEffect(() => {
    if (!showAdd) return
    function onKey(e) { if (e.key === 'Escape') setShowAdd(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [showAdd])

  return (
    <div className="px-4 py-5 sm:p-6 space-y-4 lg:h-full flex flex-col">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Leads</h1>
          <p className="text-sm text-slate-500 mt-0.5">{data?.total ?? 0} total records</p>
          {filters.run_id && (
            <p className="text-xs text-slate-400 mt-1.5">
              Showing the leads from one Find leads search.{' '}
              <button type="button" className="text-brand-400 hover:underline" onClick={() => setFilter('run_id', '')}>Show all leads</button>
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => refetch()} className="btn-secondary text-xs">
            <RefreshCw size={13} className={clsx(isFetching && 'animate-spin')} /> Refresh
          </button>
          <button onClick={handleExportCsv} className="btn-secondary text-xs">
            <Download size={13} /> Export CSV
          </button>
          <button onClick={() => fileRef.current?.click()} className="btn-secondary text-xs">
            <Upload size={13} /> Import CSV
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            className="hidden"
            onChange={(e) => { if (e.target.files[0]) importMut.mutate(e.target.files[0]) }}
          />
          <button
            onClick={() => auditMut.mutate()}
            disabled={auditRunning || auditMut.isPending || !selected.length}
            title={selected.length ? 'Audit the selected leads' : 'Select leads to audit them'}
            className="btn-secondary text-xs"
          >
            {auditRunning
              ? <><RefreshCw size={12} className="animate-spin" /> Auditing {auditProgress.data.done}/{auditProgress.data.total}</>
              : <><ClipboardCheck size={12} /> Audit{selected.length ? ` ${selected.length}` : ''}</>}
          </button>
          <button
            onClick={() => scoreAllMut.mutate()}
            disabled={scoreAllMut.isPending}
            className="btn-secondary text-xs"
          >
            {scoreAllMut.isPending
              ? <><RefreshCw size={12} className="animate-spin" /> Scoring...</>
              : <><Sparkles size={12} /> Score All</>}
          </button>
          <button onClick={() => setShowAdd(true)} className="btn-primary text-xs">
            <Plus size={13} /> Add Lead
          </button>
        </div>
      </div>

      {/* Status + Score filter bar — one swipeable row on phones */}
      <div className="flex items-center gap-2 overflow-x-auto md:flex-wrap md:overflow-visible -mx-4 px-4 md:mx-0 md:px-0 pb-1 md:pb-0 [&>*]:shrink-0">
        {STATUSES.slice(1).map((s) => {
          const cls = STATUS_BTN[s]
          const isActive = filters.status === s
          return (
            <button
              key={s}
              onClick={() => toggleStatusFilter(s)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium transition-all ${
                isActive ? cls.active : cls.base + ' hover:border-slate-600 hover:text-slate-400'
              }`}
            >
              {s}
              <span className={`font-bold tabular-nums ${isActive ? '' : 'text-slate-600'}`}>
                {statusCounts[s]}
              </span>
            </button>
          )
        })}

        <div className="w-px h-5 bg-slate-700/60 mx-1" />

        {SCORES.slice(1).map((s) => {
          const isActive = filters.score_label === s
          return (
            <button
              key={s}
              onClick={() => setFilter('score_label', isActive ? '' : s)}
              className={clsx(
                'px-3 py-1.5 rounded-lg border text-xs font-medium transition-all',
                isActive ? SCORE_STYLES[s] : 'border-slate-700/50 text-slate-500 hover:border-slate-600 hover:text-slate-400',
              )}
            >
              {s}
            </button>
          )
        })}

        {(filters.status || filters.score_label) && (
          <button
            onClick={() => { setFilter('status', ''); setFilter('score_label', '') }}
            className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px] max-w-xs">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            className="input pl-8 text-xs h-9"
            placeholder="Search name, email, phone…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        {(() => {
          const active = ['status', 'channel', 'niche', 'city', 'date_from', 'date_to', 'source_type', 'research_status']
            .filter((k) => filters[k]).length
          return (
            <button type="button" onClick={() => setFiltersOpen((v) => !v)} aria-expanded={filtersOpen}
              className="md:hidden btn-secondary text-xs h-9">
              <SlidersHorizontal size={13} /> Filters{active ? ` (${active})` : ''}
            </button>
          )
        })()}
        <div className={filtersOpen ? 'contents' : 'hidden md:contents'}>
        <select
          className="input text-xs h-9 w-36"
          value={filters.status}
          onChange={(e) => setFilter('status', e.target.value)}
        >
          {STATUSES.map((s) => <option key={s} value={s}>{s || 'All Status'}</option>)}
        </select>
        <select
          className="input text-xs h-9 w-36"
          value={filters.channel}
          onChange={(e) => setFilter('channel', e.target.value)}
        >
          {CHANNELS.map((c) => <option key={c} value={c}>{c || 'All Channels'}</option>)}
        </select>
        <input
          className="input text-xs h-9 w-28"
          placeholder="Niche…"
          value={nicheInput}
          onChange={(e) => setNicheInput(e.target.value)}
        />
        <input
          className="input text-xs h-9 w-28"
          placeholder="City…"
          value={cityInput}
          onChange={(e) => setCityInput(e.target.value)}
        />
        <input
          type="date"
          title="Date from"
          className="input text-xs h-9 w-32"
          value={filters.date_from}
          onChange={(e) => setFilter('date_from', e.target.value)}
        />
        <input
          type="date"
          title="Date to"
          className="input text-xs h-9 w-32"
          value={filters.date_to}
          onChange={(e) => setFilter('date_to', e.target.value)}
        />
        <select
          className="input text-xs h-9 w-36"
          title="Discovery source"
          value={filters.source_type}
          onChange={(e) => setFilter('source_type', e.target.value)}
        >
          <option value="">All Sources</option>
          <option value="automation">Automation</option>
          <option value="manual">Manual</option>
        </select>
        <select
          className="input text-xs h-9 w-40"
          title="Research status"
          value={filters.research_status}
          onChange={(e) => setFilter('research_status', e.target.value)}
        >
          <option value="">All Research</option>
          <option value="NOT_STARTED">Not Started</option>
          <option value="QUEUED">Queued</option>
          <option value="RESEARCHING">Researching</option>
          <option value="COMPLETED">Completed</option>
          <option value="FAILED">Failed</option>
          <option value="EXCLUDED">Excluded</option>
        </select>
        </div>
      </div>

      {/* Bulk action bar — only when something is selected */}
      {selected.length > 0 && (
        <div className="flex items-center gap-3 px-3 py-2 rounded-lg bg-brand-600/10 border border-brand-600/30">
          <span className="text-xs font-medium text-brand-300">{selected.length} selected</span>
          <button
            onClick={handleExportSelected}
            disabled={bulkExporting}
            className="btn-secondary text-[11px] !py-1"
          >
            {bulkExporting ? <RefreshCw size={11} className="animate-spin" /> : <Download size={11} />} Export selected
          </button>
          <button
            onClick={() => setResearchTarget({ ids: [...selected], bulk: true })}
            disabled={researchMut.isPending}
            className="btn-secondary text-[11px] !py-1"
          >
            {researchMut.isPending ? <RefreshCw size={11} className="animate-spin" /> : <Bot size={11} />} Send to Research Agent
          </button>
          <button
            onClick={() => setBulkDeleteConfirm(true)}
            disabled={bulkDeleting}
            className="btn-danger text-[11px] !py-1"
          >
            {bulkDeleting ? <RefreshCw size={11} className="animate-spin" /> : <Trash2 size={11} />} Delete selected
          </button>
          <button
            onClick={() => setSelected([])}
            className="ml-auto text-[11px] text-slate-500 hover:text-slate-300"
          >
            Clear selection
          </button>
        </div>
      )}

      {/* Main content */}
      <div className="flex flex-col lg:flex-row gap-4 lg:flex-1 lg:min-h-0">
        <div className="card lg:flex-1 overflow-hidden flex flex-col">
          {isError ? (
            <ErrorState message="Couldn't load leads." onRetry={refetch} retrying={isFetching} />
          ) : (
            <LeadTable
              data={data}
              isLoading={isLoading}
              onDelete={(id) => deleteMut.mutate(id)}
              onSkip={(id) => skipMut.mutate(id)}
              onMarkReplied={(id) => markRepliedMut.mutate(id)}
              onResend={(id, channel) => resendMut.mutate({ id, channel })}
              onViewMessages={(lead) => setViewLead(lead)}
              onEnrich={(id) => enrichMut.mutate(id)}
              onResearch={(id) => setResearchTarget({ ids: [id], bulk: false })}
              onRowClick={(lead) => setDrawerLead(lead)}
              selected={selected}
              onSelect={setSelected}
              page={page}
              totalPages={data?.total_pages}
              onPageChange={setPage}
              pageSize={pageSize}
              onPageSizeChange={setPageSize}
              sortBy={sortBy}
              sortDir={sortDir}
              onSort={handleSort}
            />
          )}
        </div>

        <div className="lg:w-64 lg:shrink-0">
          <CampaignControls
            selectedIds={selected}
            onComplete={() => {
              invalidate()
              setSelected([])
            }}
          />
        </div>
      </div>

      {/* Enrichment Drawer — doubles as the Lead Details view */}
      <EnrichmentDrawer
        lead={drawerLead}
        onClose={() => setDrawerLead(null)}
        onEnrich={(id) => { enrichMut.mutate(id); setDrawerLead(null) }}
        onViewMessages={(lead) => { setViewLead(lead); setDrawerLead(null) }}
      />

      {/* View Messages Modal */}
      {viewLead && (
        <ViewMessagesModal lead={viewLead} onClose={() => setViewLead(null)} />
      )}

      {/* Add Lead Modal */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={() => setShowAdd(false)}>
          <div
            ref={addModalRef}
            role="dialog" aria-modal="true" aria-labelledby="add-lead-title"
            className="card w-full max-w-md p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h2 id="add-lead-title" className="font-semibold text-slate-100">Add New Lead</h2>
              <button onClick={() => setShowAdd(false)} aria-label="Close" className="p-1.5 rounded hover:bg-slate-700 text-slate-400">
                <X size={16} />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              {Object.keys(form).map((k) => (
                <div key={k} className={k === 'business_name' || k === 'website' ? 'col-span-2' : ''}>
                  <label className="label">{k.replace(/_/g, ' ')}</label>
                  <input
                    className="input text-xs"
                    value={form[k]}
                    onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))}
                    placeholder={k === 'business_name' ? 'Required' : ''}
                  />
                </div>
              ))}
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => createMut.mutate(form)}
                disabled={!form.business_name || createMut.isPending}
                className="btn-primary flex-1 justify-center text-xs"
              >
                {createMut.isPending ? <RefreshCw size={13} className="animate-spin" /> : 'Save Lead'}
              </button>
              <button onClick={() => setShowAdd(false)} className="btn-secondary text-xs px-4">
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      <ResearchTitlesDialog
        open={!!researchTarget}
        leadCount={researchTarget?.ids.length || 0}
        niche={researchTarget ? researchNiche(researchTarget.ids) : ''}
        pending={researchMut.isPending}
        onCancel={() => setResearchTarget(null)}
        onConfirm={(titles) => researchMut.mutate({ ...researchTarget, titles })}
      />

      {/* Bulk delete confirm */}
      {bulkDeleteConfirm && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={() => setBulkDeleteConfirm(false)}>
          <div role="dialog" aria-modal="true" className="card w-full max-w-sm p-5 space-y-4" onClick={(e) => e.stopPropagation()}>
            <p className="text-sm text-slate-200">Delete {selected.length} selected lead{selected.length === 1 ? '' : 's'}? This cannot be undone.</p>
            <div className="flex gap-2">
              <button onClick={() => setBulkDeleteConfirm(false)} className="btn-secondary flex-1 justify-center text-xs">Cancel</button>
              <button onClick={handleBulkDelete} className="btn-danger flex-1 justify-center text-xs">
                <Trash2 size={12} /> Delete {selected.length}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
