import { useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  Trash2, SkipForward, Eye, RotateCcw, CheckCircle2,
  ChevronLeft, ChevronRight,
  Sparkles, Users, Bot,
} from 'lucide-react'
import clsx from 'clsx'
import ScoreBadge from './ScoreBadge'
import { SkeletonTableRows } from './ui/Skeleton'
import EmptyState from './ui/EmptyState'
import SortableHeader from './ui/SortableHeader'
import { useResizableColumns } from '../hooks/useResizableColumns'
import { STATUS_BADGE, CHANNEL_BADGE, SOURCE_BADGE, SOURCE_LABEL, RESEARCH_BADGE, RESEARCH_LABEL } from '../lib/badges'

const TABLE_COLS = 9
const ROW_HEIGHT = 53

const DEFAULT_WIDTHS = {
  business: 220, nicheCity: 140, contact: 170,
  score: 100, status: 130, channel: 110, source: 110, sent: 110,
}

// Phones: one card per lead with its actions always visible (there is no
// hover on a touchscreen). Tablets and up keep the table below.
function MobileLeadCards({ items, selected, toggleOne, onRowClick, handlers }) {
  const { onEnrich, onResearch, onViewMessages, onResend, onMarkReplied, onSkip, onDelete } = handlers
  const act = 'grid place-items-center w-9 h-9 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary disabled:opacity-30'
  return (
    <ul className="md:hidden divide-y divide-border-subtle">
      {items.map((lead) => {
        const busy = ['QUEUED', 'RESEARCHING'].includes(lead.research_status)
        return (
          <li key={lead.id} className={clsx('px-4 py-3', selected.includes(lead.id) && 'bg-primary/5')}>
            <div className="flex items-start gap-3">
              <input type="checkbox" checked={selected.includes(lead.id)} onChange={() => toggleOne(lead.id)}
                aria-label={`Select ${lead.business_name}`}
                className="mt-1 w-4 h-4 rounded accent-[rgb(var(--primary))] shrink-0" />
              <button type="button" onClick={() => onRowClick?.(lead)} className="flex-1 min-w-0 text-left">
                <p className="font-medium text-foreground truncate">{lead.business_name}</p>
                <p className="text-meta truncate">{[lead.niche, lead.city].filter(Boolean).join(' · ') || '—'}</p>
                <p className="text-meta truncate">{lead.email || lead.phone || 'No contact found'}</p>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <ScoreBadge score={lead.score} label={lead.score_label} />
                  <span className={STATUS_BADGE[lead.status] || 'badge'}>{lead.status}</span>
                  {lead.channel && <span className={CHANNEL_BADGE[lead.channel] || 'badge'}>{lead.channel}</span>}
                  {lead.research_status && lead.research_status !== 'NOT_STARTED' && (
                    <span className={clsx('text-2xs', RESEARCH_BADGE[lead.research_status] || 'text-muted-foreground')}>
                      {RESEARCH_LABEL[lead.research_status] || lead.research_status}
                    </span>
                  )}
                </div>
              </button>
            </div>
            <div className="mt-1.5 pl-7 flex items-center gap-0.5">
              <button className={act} onClick={() => onResearch?.(lead.id)} disabled={busy} aria-label={`Research ${lead.business_name}`} title="Research"><Bot size={16} /></button>
              <button className={act} onClick={() => onEnrich?.(lead.id)} aria-label={`Enrich ${lead.business_name}`} title="Enrich with AI"><Sparkles size={16} /></button>
              <button className={act} onClick={() => onViewMessages?.(lead)} aria-label={`Messages for ${lead.business_name}`} title="Messages"><Eye size={16} /></button>
              <button className={act} onClick={() => onResend?.(lead.id, lead.channel || 'EMAIL')} aria-label={`Re-send to ${lead.business_name}`} title="Re-send"><RotateCcw size={16} /></button>
              <button className={act} onClick={() => onMarkReplied?.(lead.id)} aria-label={`Mark ${lead.business_name} as replied`} title="Mark replied"><CheckCircle2 size={16} /></button>
              <button className={act} onClick={() => onSkip?.(lead.id)} aria-label={`Skip ${lead.business_name}`} title="Skip"><SkipForward size={16} /></button>
              <button className={clsx(act, 'ml-auto hover:text-error')} onClick={() => onDelete?.(lead.id)} aria-label={`Delete ${lead.business_name}`} title="Delete"><Trash2 size={16} /></button>
            </div>
          </li>
        )
      })}
    </ul>
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
  onEnrich,
  onResearch,
  onRowClick,
  onSelect,
  selected = [],
  page,
  totalPages,
  onPageChange,
  isLoading,
  sortBy,
  sortDir,
  onSort,
  pageSize,
  onPageSizeChange,
}) {
  const { items = [], total = 0 } = data || {}
  const scrollRef = useRef(null)
  const [colWidths, startResize] = useResizableColumns('autolead:leads-col-widths', DEFAULT_WIDTHS)

  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
  })

  if (isLoading) {
    return (
      <div className="overflow-x-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700/50">
              {Array.from({ length: TABLE_COLS }).map((_, i) => (
                <th key={i} className="py-3 px-4" />
              ))}
            </tr>
          </thead>
          <tbody>
            <SkeletonTableRows rows={10} cols={TABLE_COLS} />
          </tbody>
        </table>
      </div>
    )
  }

  if (!items.length) {
    return (
      <EmptyState
        icon={Users}
        title="No leads found"
        description="Import a CSV or run the scraper to get started."
      />
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

  const virtualRows = rowVirtualizer.getVirtualItems()
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0
  const paddingBottom = virtualRows.length > 0
    ? rowVirtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end
    : 0

  return (
    <div className="flex flex-col h-full">
      <MobileLeadCards items={items} selected={selected} toggleOne={toggleOne} onRowClick={onRowClick}
        handlers={{ onEnrich, onResearch, onViewMessages, onResend, onMarkReplied, onSkip, onDelete }} />
      <div ref={scrollRef} className="hidden md:block overflow-auto flex-1">
        <table className="w-full text-sm" style={{ tableLayout: 'fixed' }}>
          <thead className="sticky top-0 z-10 bg-slate-900">
            <tr className="border-b border-slate-700/50">
              <th className="py-3 px-4 text-left w-10">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleAll}
                  aria-label={allSelected ? 'Deselect all leads on this page' : 'Select all leads on this page'}
                  className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800 accent-brand-500"
                />
              </th>
              <SortableHeader label="Business"     field="business_name" sortBy={sortBy} sortDir={sortDir} onSort={onSort} width={colWidths.business}  onResizeStart={startResize('business')} />
              <SortableHeader label="Niche / City" field="niche"         sortBy={sortBy} sortDir={sortDir} onSort={onSort} width={colWidths.nicheCity} onResizeStart={startResize('nicheCity')} />
              <th style={{ width: colWidths.contact }} className="relative py-3 px-4 text-left font-medium text-slate-400 text-xs uppercase tracking-wide">
                Contact
                <span onMouseDown={startResize('contact')} role="separator" aria-orientation="vertical" aria-label="Resize Contact column" className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-brand-500/40" />
              </th>
              <SortableHeader label="Score"  field="score"   sortBy={sortBy} sortDir={sortDir} onSort={onSort} width={colWidths.score}   onResizeStart={startResize('score')} />
              <SortableHeader label="Status" field="status"  sortBy={sortBy} sortDir={sortDir} onSort={onSort} width={colWidths.status}  onResizeStart={startResize('status')} />
              <th style={{ width: colWidths.channel }} className="relative py-3 px-4 text-left font-medium text-slate-400 text-xs uppercase tracking-wide">
                Channel
                <span onMouseDown={startResize('channel')} role="separator" aria-orientation="vertical" aria-label="Resize Channel column" className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-brand-500/40" />
              </th>
              <th style={{ width: colWidths.source }} className="relative py-3 px-4 text-left font-medium text-slate-400 text-xs uppercase tracking-wide">
                Source
                <span onMouseDown={startResize('source')} role="separator" aria-orientation="vertical" aria-label="Resize Source column" className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-brand-500/40" />
              </th>
              <SortableHeader label="Sent" field="sent_at" sortBy={sortBy} sortDir={sortDir} onSort={onSort} width={colWidths.sent} onResizeStart={startResize('sent')} />
              <th className="py-3 px-4 text-right font-medium text-slate-400 text-xs uppercase tracking-wide w-52">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {paddingTop > 0 && (
              <tr aria-hidden="true"><td style={{ height: paddingTop }} colSpan={TABLE_COLS} /></tr>
            )}

            {virtualRows.map((vRow) => {
              const lead = items[vRow.index]
              return (
                <tr
                  key={lead.id}
                  data-index={vRow.index}
                  ref={rowVirtualizer.measureElement}
                  onClick={() => onRowClick?.(lead)}
                  className={clsx(
                    'group hover:bg-slate-800/40 transition-colors',
                    onRowClick && 'cursor-pointer',
                    selected.includes(lead.id) && 'bg-brand-600/5'
                  )}
                >
                  <td className="py-3 px-4" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selected.includes(lead.id)}
                      onChange={() => toggleOne(lead.id)}
                      aria-label={`Select ${lead.business_name}`}
                      className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800 accent-brand-500"
                    />
                  </td>

                  <td className="py-3 px-4 overflow-hidden">
                    <p className="font-medium text-slate-200 text-sm truncate">{lead.business_name}</p>
                    {lead.website && (
                      <a
                        href={lead.website}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-xs text-slate-500 hover:text-brand-400 transition-colors truncate block"
                      >
                        {lead.website.replace(/^https?:\/\//, '')}
                      </a>
                    )}
                  </td>

                  <td className="py-3 px-4 overflow-hidden">
                    <p className="text-slate-300 text-xs truncate">{lead.niche || '—'}</p>
                    <p className="text-slate-500 text-xs truncate">{lead.city  || '—'}</p>
                  </td>

                  <td className="py-3 px-4 overflow-hidden">
                    {lead.email && <p className="text-xs text-slate-300 truncate">{lead.email}</p>}
                    {lead.phone && <p className="text-xs text-slate-500 truncate">{lead.phone}</p>}
                    {!lead.email && !lead.phone && <p className="text-xs text-slate-600 italic">No contact</p>}
                  </td>

                  <td className="py-3 px-4">
                    <ScoreBadge score={lead.score} label={lead.score_label} />
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
                    <div className="flex flex-col gap-1">
                      {lead.source
                        ? <span className={SOURCE_BADGE[lead.source] || 'badge'}>
                            {SOURCE_LABEL[lead.source] || lead.source}
                          </span>
                        : <span className="text-xs text-slate-600">—</span>
                      }
                      <div className="flex items-center gap-1 text-[10px] text-slate-500">
                        {lead.source_type && (
                          <span className="uppercase tracking-wide">{lead.source_type}</span>
                        )}
                        {lead.research_status && lead.research_status !== 'NOT_STARTED' && (
                          <span className={RESEARCH_BADGE[lead.research_status] || 'text-slate-500'}>
                            · {RESEARCH_LABEL[lead.research_status] || lead.research_status}
                          </span>
                        )}
                      </div>
                    </div>
                  </td>

                  <td className="py-3 px-4 text-xs text-slate-500 whitespace-nowrap">
                    {fmtDate(lead.sent_at)}
                  </td>

                  <td className="py-3 px-4" onClick={(e) => e.stopPropagation()}>
                    <div className="flex items-center gap-1 justify-end opacity-0 group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity">
                      <button
                        onClick={() => onEnrich?.(lead.id)}
                        title="Enrich with AI"
                        aria-label={`Enrich ${lead.business_name} with AI`}
                        className="p-1.5 rounded text-slate-500 hover:text-emerald-400 hover:bg-emerald-500/10 transition-all"
                      >
                        <Sparkles size={13} />
                      </button>
                      <button
                        onClick={() => onResearch?.(lead.id)}
                        disabled={['QUEUED', 'RESEARCHING'].includes(lead.research_status)}
                        title={
                          lead.research_status === 'COMPLETED' ? 'Re-research with the Research Agent'
                          : lead.research_status === 'FAILED' ? 'Retry research'
                          : ['QUEUED', 'RESEARCHING'].includes(lead.research_status) ? 'Research in progress'
                          : 'Send to Research Agent'
                        }
                        aria-label={`Send ${lead.business_name} to the Research Agent`}
                        className={`p-1.5 rounded hover:bg-indigo-500/10 transition-all disabled:opacity-40 ${
                          lead.research_status === 'FAILED' ? 'text-rose-400 hover:text-rose-300' : 'text-slate-500 hover:text-indigo-400'
                        }`}
                      >
                        <Bot size={13} />
                      </button>
                      <button
                        onClick={() => onViewMessages?.(lead)}
                        title="View AI Messages"
                        aria-label={`View AI messages for ${lead.business_name}`}
                        className="p-1.5 rounded text-slate-500 hover:text-brand-400 hover:bg-brand-500/10 transition-all"
                      >
                        <Eye size={13} />
                      </button>
                      <button
                        onClick={() => onResend?.(lead.id, lead.channel || 'EMAIL')}
                        title="Re-send"
                        aria-label={`Re-send message to ${lead.business_name}`}
                        className="p-1.5 rounded text-slate-500 hover:text-blue-400 hover:bg-blue-500/10 transition-all"
                      >
                        <RotateCcw size={13} />
                      </button>
                      <button
                        onClick={() => onMarkReplied?.(lead.id)}
                        title="Mark Replied"
                        aria-label={`Mark ${lead.business_name} as replied`}
                        className="p-1.5 rounded text-slate-500 hover:text-emerald-400 hover:bg-emerald-500/10 transition-all"
                      >
                        <CheckCircle2 size={13} />
                      </button>
                      <button
                        onClick={() => onSkip?.(lead.id)}
                        title="Skip"
                        aria-label={`Skip ${lead.business_name}`}
                        className="p-1.5 rounded text-slate-500 hover:text-amber-400 hover:bg-amber-500/10 transition-all"
                      >
                        <SkipForward size={13} />
                      </button>
                      <button
                        onClick={() => onDelete?.(lead.id)}
                        title="Delete"
                        aria-label={`Delete ${lead.business_name}`}
                        className="p-1.5 rounded text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}

            {paddingBottom > 0 && (
              <tr aria-hidden="true"><td style={{ height: paddingBottom }} colSpan={TABLE_COLS} /></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between px-4 py-3 border-t border-slate-800/60 gap-3 flex-wrap">
        <p className="text-xs text-slate-500">
          {total} total leads
          {selected.length > 0 && (
            <span className="ml-2 text-brand-400">{selected.length} selected</span>
          )}
        </p>
        <div className="flex items-center gap-3">
          {onPageSizeChange && (
            <label className="flex items-center gap-1.5 text-xs text-slate-500">
              Rows
              <select
                value={pageSize}
                onChange={(e) => onPageSizeChange(Number(e.target.value))}
                aria-label="Rows per page"
                className="input !py-1 !w-auto text-xs"
              >
                {[25, 50, 100, 200].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          )}
          <div className="flex items-center gap-2">
            <button
              onClick={() => onPageChange?.(page - 1)}
              disabled={page <= 1}
              aria-label="Previous page"
              className="p-1.5 rounded text-slate-500 hover:text-slate-300 disabled:opacity-30 hover:bg-slate-800 transition-all"
            >
              <ChevronLeft size={14} />
            </button>
            <span className="text-xs text-slate-400 px-2">{page} / {totalPages || 1}</span>
            <button
              onClick={() => onPageChange?.(page + 1)}
              disabled={page >= (totalPages || 1)}
              aria-label="Next page"
              className="p-1.5 rounded text-slate-500 hover:text-slate-300 disabled:opacity-30 hover:bg-slate-800 transition-all"
            >
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
