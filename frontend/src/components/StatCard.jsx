import clsx from 'clsx'

const PALETTE = {
  brand:   { bg: 'bg-brand-500/10',   icon: 'text-brand-400',   border: 'border-brand-500/20' },
  amber:   { bg: 'bg-amber-500/10',   icon: 'text-amber-400',   border: 'border-amber-500/20' },
  emerald: { bg: 'bg-emerald-500/10', icon: 'text-emerald-400', border: 'border-emerald-500/20' },
  blue:    { bg: 'bg-blue-500/10',    icon: 'text-blue-400',    border: 'border-blue-500/20' },
  slate:   { bg: 'bg-slate-500/10',   icon: 'text-slate-400',   border: 'border-slate-500/20' },
  purple:  { bg: 'bg-purple-500/10',  icon: 'text-purple-400',  border: 'border-purple-500/20' },
  green:   { bg: 'bg-green-500/10',   icon: 'text-green-400',   border: 'border-green-500/20' },
}

// Single shared stat tile. `variant="default"` (Dashboard) is a stacked card
// with a trend indicator; `variant="compact"` (Inbox) is a horizontal row —
// same component, two layouts, so palette/styling only lives in one place.
export default function StatCard({
  label, value, icon: Icon, color = 'brand', trend, sub,
  variant = 'default',
  valueColor,   // compact-only: raw text-color className for the value
  iconBg,       // compact-only: raw bg className for the icon chip
}) {
  const palette = PALETTE[color] || PALETTE.brand

  if (variant === 'compact') {
    return (
      <div className="card p-4 flex items-center gap-3">
        <div className={clsx(
          'w-9 h-9 rounded-lg flex items-center justify-center shrink-0',
          iconBg || 'bg-slate-700/60',
        )}>
          {Icon && <Icon size={15} className={valueColor || 'text-slate-300'} />}
        </div>
        <div className="min-w-0">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider font-medium truncate">{label}</p>
          <p className={clsx('text-xl font-bold tabular-nums leading-tight', valueColor || 'text-slate-200')}>
            {value ?? '—'}
          </p>
          {sub && <p className="text-[10px] text-slate-600 mt-0.5">{sub}</p>}
        </div>
      </div>
    )
  }

  return (
    <div className={clsx('card p-5 border', palette.border)}>
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs text-slate-500 uppercase tracking-wider font-medium">{label}</p>
          <p className="text-2xl font-bold text-slate-100 mt-1.5">{value ?? '—'}</p>
          {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
        </div>
        {Icon && (
          <div className={clsx('w-10 h-10 rounded-lg flex items-center justify-center', palette.bg)}>
            <Icon size={18} className={palette.icon} />
          </div>
        )}
      </div>
      {trend !== undefined && (
        <div className="mt-3 pt-3 border-t border-slate-700/50">
          <span className={clsx('text-xs font-medium', trend >= 0 ? 'text-emerald-400' : 'text-red-400')}>
            {trend >= 0 ? '↑' : '↓'} {Math.abs(trend)}%
          </span>
          <span className="text-xs text-slate-500 ml-1">vs yesterday</span>
        </div>
      )}
    </div>
  )
}
