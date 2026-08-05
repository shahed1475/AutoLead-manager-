import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  History, RefreshCw, Search, Trash2, Copy, Download, Eye, X, Rocket, MapPin,
} from 'lucide-react'
import { campaignApi, leadsApi } from '../api/client'
import SortableHeader from './ui/SortableHeader'
import EmptyState from './ui/EmptyState'
import { CHANNEL_BADGE, RUN_STATUS_CLS } from '../lib/badges'
import toast from 'react-hot-toast'
import clsx from 'clsx'

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

function downloadBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.URL.revokeObjectURL(url)
}

// ── Detail modal ────────────────────────────────────────────────────────────

function RunDetailModal({ run, onClose, onViewLeads }) {
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        role="dialog" aria-modal="true" aria-labelledby="run-detail-title"
        className="card w-full max-w-md p-5 space-y-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <div>
            <h2 id="run-detail-title" className="font-semibold text-slate-100">{run.niche}</h2>
            <p className="text-xs text-slate-500 mt-0.5">{run.city}{run.country ? `, ${run.country}` : ''}</p>
          </div>
          <button onClick={onClose} aria-label="Close" className="p-1.5 rounded hover:bg-slate-700 text-slate-400">
            <X size={16} />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div className="bg-slate-900/60 rounded-lg p-3 border border-slate-800/60">
            <p className="text-slate-600 uppercase tracking-wide text-[10px]">Status</p>
            <span className={clsx('inline-flex mt-1 items-center px-2 py-0.5 rounded-full text-[10px] font-medium', RUN_STATUS_CLS[run.status] || '')}>
              {run.status}
            </span>
          </div>
          <div className="bg-slate-900/60 rounded-lg p-3 border border-slate-800/60">
            <p className="text-slate-600 uppercase tracking-wide text-[10px]">Channel</p>
            <span className={clsx('badge text-[10px] mt-1', CHANNEL_BADGE[run.channel] || 'badge')}>{run.channel}</span>
          </div>
          <div className="bg-slate-900/60 rounded-lg p-3 border border-slate-800/60">
            <p className="text-slate-600 uppercase tracking-wide text-[10px]">Found / Sent</p>
            <p className="text-slate-200 font-mono font-bold mt-1">{run.leads_found} / {run.leads_sent}</p>
          </div>
          <div className="bg-slate-900/60 rounded-lg p-3 border border-slate-800/60">
            <p className="text-slate-600 uppercase tracking-wide text-[10px]">Duration</p>
            <p className="text-slate-200 font-mono mt-1">{fmtDuration(run.started_at, run.finished_at)}</p>
          </div>
          <div className="bg-slate-900/60 rounded-lg p-3 border border-slate-800/60 col-span-2">
            <p className="text-slate-600 uppercase tracking-wide text-[10px]">Sources</p>
            <p className="text-slate-300 mt-1">{run.sources || '—'}</p>
          </div>
        </div>

        <button onClick={() => onViewLeads(run)} className="btn-secondary w-full justify-center text-xs">
          <MapPin size={12} /> View matching leads
        </button>
      </div>
    </div>
  )
}

// ── Main table ────────────────────────────────────────────────────────────────

export default function CampaignHistoryTable({ history, isLoading, onRefresh, onDuplicate }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [search, setSearch]     = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [sortBy, setSortBy]     = useState('started_at')
  const [sortDir, setSortDir]   = useState('desc')
  const [detailRun, setDetailRun] = useState(null)
  const [deleteId, setDeleteId] = useState(null)

  function onSort(field) {
    if (sortBy === field) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortBy(field); setSortDir('desc') }
  }

  const deleteMut = useMutation({
    mutationFn: (runId) => campaignApi.deleteHistory(runId),
    onSuccess: () => {
      toast.success('Campaign run deleted')
      qc.invalidateQueries({ queryKey: ['campaignHistory'] })
      setDeleteId(null)
    },
    onError: (e) => { toast.error(e.message); setDeleteId(null) },
  })

  async function handleExport(run) {
    try {
      const blob = await leadsApi.exportCsv({ niche: run.niche, city: run.city })
      downloadBlob(blob, `campaign-${run.niche}-${run.city}-${run.id}.csv`.replace(/\s+/g, '_'))
      toast.success('Export started')
    } catch (e) {
      toast.error(e.message || 'Export failed')
    }
  }

  function handleViewLeads(run) {
    setDetailRun(null)
    navigate(`/leads?niche=${encodeURIComponent(run.niche || '')}&city=${encodeURIComponent(run.city || '')}`)
  }

  const filtered = useMemo(() => {
    let rows = history || []
    if (statusFilter) rows = rows.filter((r) => r.status === statusFilter)
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      rows = rows.filter((r) =>
        (r.niche || '').toLowerCase().includes(q) || (r.city || '').toLowerCase().includes(q)
      )
    }
    const sorted = [...rows].sort((a, b) => {
      const av = a[sortBy] ?? ''
      const bv = b[sortBy] ?? ''
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    return sorted
  }, [history, search, statusFilter, sortBy, sortDir])

  const statuses = useMemo(
    () => [...new Set((history || []).map((r) => r.status).filter(Boolean))],
    [history],
  )

  return (
    <div className="card overflow-hidden shrink-0">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-700/50 flex-wrap">
        <History size={13} className="text-slate-500" />
        <h3 className="text-sm font-semibold text-slate-300">Recent Campaigns</h3>
        {history?.length > 0 && (
          <span className="text-[10px] text-slate-600 ml-0.5">({filtered.length}/{history.length})</span>
        )}

        <div className="relative ml-3">
          <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-600" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search niche/city…"
            aria-label="Search campaign history"
            className="input text-xs !py-1.5 !pl-7 w-40"
          />
        </div>

        {statuses.length > 1 && (
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Filter by status"
            className="input text-xs !py-1.5 !w-auto"
          >
            <option value="">All statuses</option>
            {statuses.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}

        <button
          onClick={onRefresh}
          title="Refresh history"
          aria-label="Refresh campaign history"
          className="ml-auto p-1 rounded text-slate-600 hover:text-slate-400 transition-colors"
        >
          <RefreshCw size={12} className={clsx(isLoading && 'animate-spin')} />
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-800/60">
              <SortableHeader label="Date"     field="started_at"  sortBy={sortBy} sortDir={sortDir} onSort={onSort} className="!text-[10px]" />
              <SortableHeader label="Niche"    field="niche"       sortBy={sortBy} sortDir={sortDir} onSort={onSort} className="!text-[10px]" />
              <SortableHeader label="City"     field="city"        sortBy={sortBy} sortDir={sortDir} onSort={onSort} className="!text-[10px]" />
              <th className="px-3 py-2 text-left font-medium text-slate-500 text-[10px] uppercase tracking-wide">Channel</th>
              <SortableHeader label="Found"    field="leads_found" sortBy={sortBy} sortDir={sortDir} onSort={onSort} right className="!text-[10px]" />
              <SortableHeader label="Sent"     field="leads_sent"  sortBy={sortBy} sortDir={sortDir} onSort={onSort} right className="!text-[10px]" />
              <th className="px-3 py-2 text-right font-medium text-slate-500 text-[10px] uppercase tracking-wide">Duration</th>
              <SortableHeader label="Status"   field="status"      sortBy={sortBy} sortDir={sortDir} onSort={onSort} className="!text-[10px]" />
              <th className="px-3 py-2 text-right font-medium text-slate-500 text-[10px] uppercase tracking-wide">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/40">
            {filtered.map((run) => (
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
                <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
                  <div className="flex items-center gap-0.5 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => setDetailRun(run)}
                      title="View details" aria-label={`View details for ${run.niche} campaign`}
                      className="p-1.5 rounded text-slate-500 hover:text-brand-400 hover:bg-brand-500/10 transition-all"
                    >
                      <Eye size={12} />
                    </button>
                    <button
                      onClick={() => onDuplicate?.(run)}
                      title="Duplicate — prefill Start form" aria-label={`Duplicate ${run.niche} campaign`}
                      className="p-1.5 rounded text-slate-500 hover:text-emerald-400 hover:bg-emerald-500/10 transition-all"
                    >
                      <Copy size={12} />
                    </button>
                    <button
                      onClick={() => handleExport(run)}
                      title="Export leads (CSV)" aria-label={`Export leads for ${run.niche} campaign`}
                      className="p-1.5 rounded text-slate-500 hover:text-blue-400 hover:bg-blue-500/10 transition-all"
                    >
                      <Download size={12} />
                    </button>
                    <button
                      onClick={() => setDeleteId(run.id)}
                      title="Delete" aria-label={`Delete ${run.niche} campaign run`}
                      className="p-1.5 rounded text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}

            {!isLoading && filtered.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-10 text-center">
                  {history?.length > 0 ? (
                    <p className="text-slate-600 text-xs">No campaigns match your filters</p>
                  ) : (
                    <EmptyState
                      icon={Rocket}
                      title="No campaigns yet"
                      description="Enter a niche & city above, then hit START ENGINE."
                    />
                  )}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {detailRun && (
        <RunDetailModal run={detailRun} onClose={() => setDetailRun(null)} onViewLeads={handleViewLeads} />
      )}

      {deleteId != null && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={() => setDeleteId(null)}>
          <div
            role="dialog" aria-modal="true"
            className="card w-full max-w-sm p-5 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-sm text-slate-200">Delete this campaign run? This only removes the history record — leads it found are not deleted.</p>
            <div className="flex gap-2">
              <button onClick={() => setDeleteId(null)} className="btn-secondary flex-1 justify-center text-xs">Cancel</button>
              <button
                onClick={() => deleteMut.mutate(deleteId)}
                disabled={deleteMut.isPending}
                className="btn-danger flex-1 justify-center text-xs"
              >
                {deleteMut.isPending ? <RefreshCw size={12} className="animate-spin" /> : <Trash2 size={12} />}
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
