import { useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Upload, Download, Search, RefreshCw, Sparkles } from 'lucide-react'
import { leadsApi, aiApi, campaignApi, enrichApi } from '../api/client'
import LeadTable from '../components/LeadTable'
import CampaignControls from '../components/CampaignControls'
import ViewMessagesModal from '../components/ViewMessagesModal'
import toast from 'react-hot-toast'
import clsx from 'clsx'

const STATUSES = ['', 'PENDING', 'SENT', 'REPLIED', 'SKIPPED']
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
}

export default function Leads() {
  const qc = useQueryClient()
  const fileRef = useRef()
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState([])
  const [filters, setFilters] = useState({
    status: '', channel: '', search: '', niche: '', city: '',
    date_from: '', date_to: '', score_label: '',
  })
  const [sortBy, setSortBy] = useState('created_at')
  const [sortDir, setSortDir] = useState('desc')
  const [showAdd, setShowAdd] = useState(false)
  const [viewLead, setViewLead] = useState(null)
  const [form, setForm] = useState({
    business_name: '', email: '', phone: '', website: '', niche: '', city: '',
  })

  const params = {
    page,
    page_size: 50,
    sort_by: sortBy,
    sort_dir: sortDir,
    ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)),
  }

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['leads', params],
    queryFn: () => leadsApi.list(params),
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

  const handleExportCsv = async () => {
    try {
      const p = {}
      if (filters.status)  p.status  = filters.status
      if (filters.channel) p.channel = filters.channel
      if (filters.niche)   p.niche   = filters.niche
      if (filters.city)    p.city    = filters.city
      const blob = await leadsApi.exportCsv(p)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'leads-export.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      toast.error(e.message)
    }
  }

  const handleSort = (field) => {
    if (sortBy === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortBy(field)
      setSortDir('desc')
    }
    setPage(1)
  }

  const setFilter = (k, v) => {
    setFilters((f) => ({ ...f, [k]: v }))
    setPage(1)
  }

  const toggleStatusFilter = (s) => setFilter('status', filters.status === s ? '' : s)

  return (
    <div className="p-6 space-y-4 h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Leads</h1>
          <p className="text-sm text-slate-500 mt-0.5">{data?.total ?? 0} total records</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => refetch()} className="btn-secondary text-xs">
            <RefreshCw size={13} /> Refresh
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

      {/* Status + Score filter bar */}
      <div className="flex items-center gap-2 flex-wrap">
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
            value={filters.search}
            onChange={(e) => setFilter('search', e.target.value)}
          />
        </div>
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
          value={filters.niche}
          onChange={(e) => setFilter('niche', e.target.value)}
        />
        <input
          className="input text-xs h-9 w-28"
          placeholder="City…"
          value={filters.city}
          onChange={(e) => setFilter('city', e.target.value)}
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
      </div>

      {/* Main content */}
      <div className="flex gap-4 flex-1 min-h-0">
        <div className="card flex-1 overflow-hidden flex flex-col">
          <LeadTable
            data={data}
            isLoading={isLoading}
            onDelete={(id) => deleteMut.mutate(id)}
            onSkip={(id) => skipMut.mutate(id)}
            onMarkReplied={(id) => markRepliedMut.mutate(id)}
            onResend={(id, channel) => resendMut.mutate({ id, channel })}
            onViewMessages={(lead) => setViewLead(lead)}
            onEnrich={(id) => enrichMut.mutate(id)}
            selected={selected}
            onSelect={setSelected}
            page={page}
            totalPages={data?.total_pages}
            onPageChange={setPage}
            sortBy={sortBy}
            sortDir={sortDir}
            onSort={handleSort}
          />
        </div>

        <div className="w-64 shrink-0">
          <CampaignControls
            selectedIds={selected}
            onComplete={() => {
              invalidate()
              setSelected([])
            }}
          />
        </div>
      </div>

      {/* View Messages Modal */}
      {viewLead && (
        <ViewMessagesModal lead={viewLead} onClose={() => setViewLead(null)} />
      )}

      {/* Add Lead Modal */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="card w-full max-w-md p-6 space-y-4">
            <h2 className="font-semibold text-slate-100">Add New Lead</h2>
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
    </div>
  )
}
