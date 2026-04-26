import { useState } from 'react'
import { Globe, ChevronDown, ChevronUp, Sparkles, AlertTriangle, Zap } from 'lucide-react'
import clsx from 'clsx'

function Section({ icon: Icon, label, value, color = 'text-slate-400' }) {
  if (!value) return null
  return (
    <div className="space-y-1">
      <div className={clsx('flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide', color)}>
        <Icon size={10} />
        {label}
      </div>
      <p className="text-xs text-slate-300 leading-relaxed">{value}</p>
    </div>
  )
}

export default function EnrichmentCard({ lead }) {
  const [open, setOpen] = useState(false)

  const hasEnrichment = lead?.website_summary || lead?.business_gaps || lead?.pain_points

  if (!hasEnrichment) {
    return (
      <div className="flex items-center gap-1.5 text-[10px] text-slate-600 italic">
        <Globe size={10} />
        Not enriched
      </div>
    )
  }

  return (
    <div className="space-y-1">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-[10px] text-emerald-400 font-medium hover:text-emerald-300 transition-colors"
      >
        <Sparkles size={10} />
        Enriched
        {open ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
      </button>

      {open && (
        <div className="mt-2 p-3 rounded-lg bg-slate-900/60 border border-slate-700/40 space-y-3">
          <Section
            icon={Globe}
            label="Summary"
            value={lead.website_summary}
            color="text-sky-400"
          />
          <Section
            icon={AlertTriangle}
            label="Gaps Found"
            value={lead.business_gaps}
            color="text-amber-400"
          />
          <Section
            icon={Zap}
            label="Pain Point"
            value={lead.pain_points}
            color="text-violet-400"
          />
          {lead.personalization_hook && (
            <Section
              icon={Sparkles}
              label="Hook"
              value={lead.personalization_hook}
              color="text-emerald-400"
            />
          )}
        </div>
      )}
    </div>
  )
}
