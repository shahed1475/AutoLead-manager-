import { ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react'

// Shared sortable <th> — click or Enter/Space to toggle sort on `field`.
// Used by LeadTable and CampaignHistoryTable so sort UX stays consistent.
// `width` + `onResizeStart` are optional — pass both to get a drag handle
// (LeadTable uses this; CampaignHistoryTable doesn't).
export default function SortableHeader({
  label, field, sortBy, sortDir, onSort, className = '', right = false,
  width, onResizeStart,
}) {
  const active = sortBy === field
  return (
    <th
      onClick={() => onSort?.(field)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSort?.(field) } }}
      role="columnheader"
      tabIndex={0}
      aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
      style={width ? { width, minWidth: width, maxWidth: width } : undefined}
      className={`relative py-3 px-4 font-medium text-slate-400 text-xs uppercase tracking-wide cursor-pointer select-none hover:text-slate-200 transition-colors focus:outline-none focus:ring-1 focus:ring-inset focus:ring-brand-500/60 ${right ? 'text-right' : 'text-left'} ${className}`}
    >
      <span className={`flex items-center gap-1 ${right ? 'justify-end' : ''}`}>
        {label}
        {active
          ? sortDir === 'asc'
            ? <ArrowUp size={11} className="text-brand-400" />
            : <ArrowDown size={11} className="text-brand-400" />
          : <ArrowUpDown size={11} className="text-slate-600" />
        }
      </span>
      {onResizeStart && (
        <span
          onMouseDown={onResizeStart}
          onClick={(e) => e.stopPropagation()}
          role="separator"
          aria-orientation="vertical"
          aria-label={`Resize ${label} column`}
          className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-brand-500/40 active:bg-brand-500/60"
        />
      )}
    </th>
  )
}
