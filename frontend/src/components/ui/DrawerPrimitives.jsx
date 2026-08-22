// Small shared visual primitives used by the Lead Details drawer and its
// sub-panels (EnrichmentDrawer.jsx, BusinessIntelligencePanel.jsx). Extracted
// to their own module so those two components can both import from here
// without importing from each other (which would be a circular import).

export function SLabel({ icon: Icon, children, color = 'text-slate-500' }) {
  return (
    <div className={`flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest ${color}`}>
      {Icon && <Icon size={9} />}
      {children}
    </div>
  )
}

export function TagCloud({ items, color = 'amber' }) {
  if (!items?.length) return null
  const cls = {
    amber:  'bg-amber-500/15 text-amber-400 border-amber-500/20',
    red:    'bg-red-500/15 text-red-400 border-red-500/20',
    violet: 'bg-violet-500/15 text-violet-400 border-violet-500/20',
    sky:    'bg-sky-500/15 text-sky-400 border-sky-500/20',
  }[color] || 'bg-slate-500/15 text-slate-400 border-slate-500/20'

  return (
    <div className="flex flex-wrap gap-1.5 mt-1.5">
      {items.map((tag, i) => (
        <span key={i} className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium border ${cls}`}>
          {tag}
        </span>
      ))}
    </div>
  )
}

export function BulletList({ items, dotColor = 'text-red-400' }) {
  if (!items?.length) return null
  return (
    <ul className="mt-1.5 space-y-1">
      {items.map((item, i) => (
        <li key={i} className="flex items-start gap-2 text-xs text-slate-300 leading-relaxed">
          <span className={`${dotColor} mt-0.5 shrink-0 font-bold`}>•</span>
          {item}
        </li>
      ))}
    </ul>
  )
}
