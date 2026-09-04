import { Link } from 'react-router-dom'
import { Search, Zap, ArrowRight } from 'lucide-react'

const OPTIONS = [
  {
    to: '/lead-search/automation',
    icon: Zap,
    title: 'Lead Search Automation',
    desc: 'Automatically discover leads using multiple search engines and process them through the '
        + 'lead-generation pipeline — enrichment, scoring, and optional Research Agent handoff — on a '
        + 'daily schedule.',
    cta: 'Open Automation',
  },
  {
    to: '/lead-search/manual',
    icon: Search,
    title: 'Manual Lead Search',
    desc: 'Search manually using selected search engines, review results, and choose leads yourself. '
        + 'Every lead you keep becomes a persistent lead record.',
    cta: 'Open Manual Search',
  },
]

export default function LeadSearch() {
  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <div className="flex items-center gap-2">
        <Search size={18} className="text-brand-400" />
        <h1 className="text-lg font-bold text-slate-100">Lead Search</h1>
      </div>
      <p className="text-sm text-slate-500 -mt-3">
        Two ways to find leads. Both feed the same Lead Database, and both can send leads to the Research Agent.
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        {OPTIONS.map(({ to, icon: Icon, title, desc, cta }) => (
          <Link
            key={to}
            to={to}
            className="group flex flex-col rounded-xl border border-slate-800 bg-slate-900/40 p-5 transition-colors hover:border-brand-600/50 hover:bg-slate-900/70"
          >
            <div className="mb-3 flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-brand-600/25 bg-brand-600/15">
                <Icon size={16} className="text-brand-400" />
              </div>
              <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
            </div>
            <p className="flex-1 text-xs leading-relaxed text-slate-500">{desc}</p>
            <span className="mt-4 inline-flex items-center gap-1.5 text-xs font-semibold text-brand-400 group-hover:text-brand-300">
              {cta} <ArrowRight size={13} />
            </span>
          </Link>
        ))}
      </div>
    </div>
  )
}
