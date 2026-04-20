import {
  Trash2, SkipForward, Eye, RotateCcw, CheckCircle2,
  ChevronLeft, ChevronRight, ArrowUp, ArrowDown, ArrowUpDown,
} from 'lucide-react'
import clsx from 'clsx'

const STATUS_BADGE = {
  PENDING: 'badge-pending',
  SENT:    'badge-sent',
  REPLIED: 'badge-replied',
  SKIPPED: 'badge-skipped',
}

const CHANNEL_BADGE = {
  EMAIL:    'badge-email',
  WHATSAPP: 'badge-whatsapp',
  BOTH:     'badge bg-teal-500/20 text-teal-400 border border-teal-500/30',
}

const SOURCE_BADGE = {
  GOOGLE_MAPS:  'badge bg-blue-500/20 text-blue-400 border border-blue-500/30',
  YELP:         'badge bg-red-500/20 text-red-400 border border-red-500/30',
  YELLOW_PAGES: 'badge bg-yellow-500/20 text-yellow-400 border border-yellow-500/30',
}

const SOURCE_LABEL = {
  GOOGLE_MAPS:  '🗺 Maps',
  YELP:         '⭐ Yelp',
  YELLOW_PAGES: '📒 YP',
}

function SortTh({ label, field, sortBy, sortDir, onSort, className = '' }) {
  const active = sortBy === field
  return (
    <th
      onClick={() => onSort?.(field)}
      className={`py-3 px-4 text-left font-medium text-slate-400 text-xs uppercase tracking-wide cursor-pointer select-none hover:text-slate-200 transition-colors ${className}`}
    >
      <span className="flex items-center gap-1">
        {label}
        {active
          ? sortDir === 'asc'
            ? <ArrowUp size={11} className="text-brand-400" />
            : <ArrowDown size={11} className="text-brand-400" />
          : <ArrowUpDown size={11} className="text-slate-600" />
        }
      </span>
    </th>
  )
}

function fmtDate(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: '2-digit' })
}

export default function LeadTable({
  data,
  onDelete,
  onSkip,
  onResend,
  onMarkReplied,
  onViewMessages,
  onSelect,
  selected = [],
  page,
  totalPages,
  onPageChange,
  isLoading,
  sortBy,
  sortDir,
  onSort,
}) {
  const { items = [], total = 0 } = data || {}

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-500 text-sm">
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
          Loading leads…
        </div>
      </div>
    )
  }

  if (!items.length) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-slate-500">
        <p className="text-sm">No leads found</p>
        <p className="text-xs mt-1 text-slate-600">Import a CSV or run the scraper to get started</p>
      </div>
    )
  }

  const allSelected = items.length > 0 && items.every((i) => selected.includes(i.id))

  const toggleAll = () => {
    if (allSelected) {
      onSelect?.(selected.filter((id) => !items.map((i) => i.id).includes(id)))
    } else {
      onSelect?.([...new Set([...selected, ...items.map((i) => i.id)])])
    }
  }

  const toggleOne = (id) => {
    onSelect?.(selected.includes(id)
      ? selected.filter((s) => s !== id)
      : [...selected, id]
    )
  }

  return (
    <div className="flex flex-col h-full">
      <div className="overflow-x-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700/50">
              <th className="py-3 px-4 text-left w-10">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleAll}
                  className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800 accent-brand-500"
                />
              </th>
              <SortTh label="Business"     field="business_name" sortBy={sortBy} sortDir={sortDir} onSort={onSort} />
              <SortTh label="Niche / City" field="niche"         sortBy={sortBy} sortDir={sortDir} onSort={onSort} />
              <th className="py-3 px-4 text-left font-medium text-slate-400 text-xs uppercase tracking-wide">Contact</th>
              <SortTh label="Status"       field="status"        sortBy={sortBy} sortDir={sortDir} onSort={onSort} />
              <th className="py-3 px-4 text-left font-medium text-slate-400 text-xs uppercase tracking-wide">Channel</th>
              <th className="py-3 px-4 text-left font-medium text-slate-400 text-xs uppercase tracking-wide">Source</th>
              <SortTh label="Sent"         field="sent_at"       sortBy={sortBy} sortDir={sortDir} onSort={onSort} />
              <th className="py-3 px-4 text-right font-medium text-slate-400 text-xs uppercase tracking-wide w-48">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {items.map((lead) => (
              <tr
                key={lead.id}
                className={clsx(
                  'group hover:bg-slate-800/40 transition-colors',
                  selected.includes(lead.id) && 'bg-brand-600/5'
                )}
              >
                <td className="py-3 px-4">
                  <input
                    type="checkbox"
                    checked={selected.includes(lead.id)}
                    onChange={() => toggleOne(lead.id)}
                    className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800 accent-brand-500"
                  />
                </td>

                <td className="py-3 px-4 max-w-[180px]">
                  <p className="font-medium text-slate-200 text-sm truncate">{lead.business_name}</p>
                  {lead.website && (
                    <a
                      href={lead.website}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs text-slate-500 hover:text-brand-400 transition-colors truncate block"
                    >
                      {lead.website.replace(/^https?:\/\//, '')}
                    </a>
                  )}
                </td>

                <td className="py-3 px-4">
                  <p className="text-slate-300 text-xs">{lead.niche || '—'}</p>
                  <p className="text-slate-500 text-xs">{lead.city  || '—'}</p>
                </td>

                <td className="py-3 px-4 max-w-[160px]">
                  {lead.email && <p className="text-xs text-slate-300 truncate">{lead.email}</p>}
                  {lead.phone && <p className="text-xs text-slate-500">{lead.phone}</p>}
                  {!lead.email && !lead.phone && <p className="text-xs text-slate-600 italic">No contact</p>}
                </td>

                <td className="py-3 px-4">
                  <span className={STATUS_BADGE[lead.status] || 'badge'}>{lead.status}</span>
                </td>

                <td className="py-3 px-4">
                  {lead.channel
                    ? <span className={CHANNEL_BADGE[lead.channel] || 'badge'}>{lead.channel}</span>
                    : <span className="text-xs text-slate-600">—</span>
                  }
                </td>

                <td className="py-3 px-4">
                  {lead.source
                    ? <span className={SOURCE_BADGE[lead.source] || 'badge'}>
                        {SOURCE_LABEL[lead.source] || lead.source}
                      </span>
                    : <span className="text-xs text-slate-600">—</span>
                  }
                </td>

                <td className="py-3 px-4 text-xs text-slate-500 whitespace-nowrap">
                  {fmtDate(lead.sent_at)}
                </td>

                <td className="py-3 px-4">
                  <div className="flex items-center gap-1 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => onViewMessages?.(lead)}
                      title="View AI Messages"
                      className="p-1.5 rounded text-slate-500 hover:text-brand-400 hover:bg-brand-500/10 transition-all"
                    >
                      <Eye size={13} />
                    </button>
                    <button
                      onClick={() => onResend?.(lead.id, lead.channel || 'EMAIL')}
                      title="Re-send"
                      className="p-1.5 rounded text-slate-500 hover:text-blue-400 hover:bg-blue-500/10 transition-all"
                    >
                      <RotateCcw size={13} />
                    </button>
                    <button
                      onClick={() => onMarkReplied?.(lead.id)}
                      title="Mark Replied"
                      className="p-1.5 rounded text-slate-500 hover:text-emerald-400 hover:bg-emerald-500/10 transition-all"
                    >
                      <CheckCircle2 size={13} />
                    </button>
                    <button
                      onClick={() => onSkip?.(lead.id)}
                      title="Skip"
                      className="p-1.5 rounded text-slate-500 hover:text-amber-400 hover:bg-amber-500/10 transition-all"
                    >
                      <SkipForward size={13} />
                    </button>
                    <button
                      onClick={() => onDelete?.(lead.id)}
                      title="Delete"
                      className="p-1.5 rounded text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between px-4 py-3 border-t border-slate-800/60">
        <p className="text-xs text-slate-500">
          {total} total leads
          {selected.length > 0 && (
            <span className="ml-2 text-brand-400">{selected.length} selected</span>
          )}
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onPageChange?.(page - 1)}
            disabled={page <= 1}
            className="p-1.5 rounded text-slate-500 hover:text-slate-300 disabled:opacity-30 hover:bg-slate-800 transition-all"
          >
            <ChevronLeft size={14} />
          </button>
          <span className="text-xs text-slate-400 px-2">{page} / {totalPages || 1}</span>
          <button
            onClick={() => onPageChange?.(page + 1)}
            disabled={page >= (totalPages || 1)}
            className="p-1.5 rounded text-slate-500 hover:text-slate-300 disabled:opacity-30 hover:bg-slate-800 transition-all"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>
    </div>
  )
}
