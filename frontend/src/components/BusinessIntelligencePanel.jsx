import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Brain, AlertTriangle, Lightbulb, ShieldCheck, HelpCircle,
  ChevronDown, ChevronUp, Loader2, Sparkles,
} from 'lucide-react'
import { intelligenceApi } from '../api/client'
import { SLabel, TagCloud } from './ui/DrawerPrimitives'

const SEVERITY_COLOR = { high: 'text-red-400', medium: 'text-amber-400', low: 'text-slate-400' }
const CLASS_BADGE = {
  observed: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/20',
  inferred: 'bg-amber-500/15 text-amber-400 border-amber-500/20',
}

function ClassificationBadge({ classification }) {
  const cls = CLASS_BADGE[classification] || 'bg-slate-500/15 text-slate-400 border-slate-500/20'
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-semibold border ${cls}`}>
      {classification === 'observed' ? 'Observed' : classification === 'inferred' ? 'Inferred' : 'Unknown'}
    </span>
  )
}

function ConfidenceBar({ value }) {
  const pct = Math.round((value || 0) * 100)
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-14 h-1 rounded-full bg-slate-700 overflow-hidden">
        <div className="h-full bg-sky-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[9px] text-slate-500 tabular-nums">{pct}%</span>
    </div>
  )
}

function PainPointRow({ pp }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border border-slate-700/40 bg-slate-900/40 p-2.5">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-start justify-between gap-2 text-left"
      >
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium text-slate-200">{pp.title}</p>
          <div className="flex items-center gap-2 mt-1">
            <ClassificationBadge classification={pp.classification} />
            <span className={`text-[9px] font-semibold uppercase ${SEVERITY_COLOR[pp.severity] || 'text-slate-400'}`}>
              {pp.severity || 'medium'} severity
            </span>
            <ConfidenceBar value={pp.confidence} />
          </div>
        </div>
        {open ? <ChevronUp size={12} className="text-slate-500 shrink-0 mt-0.5" /> : <ChevronDown size={12} className="text-slate-500 shrink-0 mt-0.5" />}
      </button>
      {open && (
        <div className="mt-2 pt-2 border-t border-slate-800 space-y-1.5">
          {pp.description && <p className="text-xs text-slate-300 leading-relaxed">{pp.description}</p>}
          {pp.evidence_snippet && (
            <p className="text-[10px] text-slate-500 italic">
              Evidence: &ldquo;{pp.evidence_snippet}&rdquo;
              {pp.source_url && (
                <a href={pp.source_url} target="_blank" rel="noreferrer" className="ml-1 text-sky-500 not-italic hover:underline">
                  source
                </a>
              )}
            </p>
          )}
          {(pp.operational_impact || pp.customer_impact) && (
            <div className="grid grid-cols-1 gap-1 pt-1">
              {pp.operational_impact && (
                <p className="text-[10px] text-slate-400"><span className="text-slate-500 font-semibold">Operational impact:</span> {pp.operational_impact}</p>
              )}
              {pp.customer_impact && (
                <p className="text-[10px] text-slate-400"><span className="text-slate-500 font-semibold">Customer impact:</span> {pp.customer_impact}</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function OpportunityRow({ opp }) {
  return (
    <div className="rounded-lg border border-violet-500/20 bg-violet-500/5 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[9px] font-bold uppercase tracking-wide text-violet-400">{opp.area}</span>
        <ClassificationBadge classification={opp.classification} />
      </div>
      <p className="text-xs font-medium text-slate-200 mt-1">{opp.title}</p>
      {opp.description && <p className="text-[10px] text-slate-400 mt-0.5 leading-relaxed">{opp.description}</p>}
    </div>
  )
}

function recommendedNextAction({ hasProfile, painPoints }) {
  if (!hasProfile) {
    return { label: 'Run research first', actionable: false }
  }
  if (!painPoints || painPoints.length === 0) {
    return { label: 'Run pain point analysis', actionable: true }
  }
  const allObserved = painPoints.every((pp) => pp.classification === 'observed')
  if (allObserved) {
    return { label: 'Business understanding looks complete', actionable: false }
  }
  return { label: 'Consider manually verifying inferred pain points', actionable: false }
}

export default function BusinessIntelligencePanel({ leadId }) {
  const queryClient = useQueryClient()

  const { data, isLoading, error } = useQuery({
    queryKey: ['intelligence', leadId],
    queryFn: () => intelligenceApi.getIntelligence(leadId),
    enabled: Boolean(leadId),
    retry: false,
    staleTime: 60 * 1000,
  })

  const analyzeMutation = useMutation({
    mutationFn: () => intelligenceApi.analyzePainPoints(leadId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['intelligence', leadId] }),
  })

  // 404 (no research yet) is an expected, non-error state here — not a fetch failure to surface.
  const notResearchedYet = error?.message?.toLowerCase().includes('research available') || error?.response?.status === 404

  if (isLoading) {
    return (
      <div className="pt-2 pb-1 flex items-center gap-2 text-[10px] text-slate-600">
        <Loader2 size={11} className="animate-spin" /> Checking business intelligence…
      </div>
    )
  }

  if (!data || notResearchedYet) {
    return (
      <div className="mt-2 p-3 rounded-lg border border-dashed border-slate-700/50 text-center">
        <Brain size={16} className="text-slate-600 mx-auto mb-1.5" />
        <p className="text-xs text-slate-500">No business intelligence yet</p>
        <p className="text-[10px] text-slate-600 mt-0.5">Enable Sales Intelligence and run research for this lead first.</p>
      </div>
    )
  }

  const { profile, pain_points: painPoints = [], business_opportunities: opportunities = [] } = data
  const next = recommendedNextAction({ hasProfile: Boolean(profile), painPoints })

  return (
    <div className="mt-1 pt-4 border-t border-slate-800 space-y-4">
      <div className="flex items-center gap-1.5">
        <Brain size={12} className="text-brand-400" />
        <span className="text-[10px] font-bold uppercase tracking-widest text-brand-400">Business Intelligence</span>
      </div>

      {/* Business Understanding */}
      {(profile?.industry || profile?.company_description) && (
        <div className="space-y-1.5">
          <SLabel icon={ShieldCheck} color="text-teal-400">Business Understanding</SLabel>
          {profile.industry && <p className="text-xs text-slate-300"><span className="text-slate-500">Industry:</span> {profile.industry}</p>}
          {profile.company_description && <p className="text-xs text-slate-300 leading-relaxed mt-1">{profile.company_description}</p>}
          {Array.isArray(profile.services) && profile.services.length > 0 && (
            <TagCloud items={profile.services} color="sky" />
          )}
        </div>
      )}

      {/* Pain Points */}
      <div className="space-y-1.5">
        <SLabel icon={AlertTriangle} color="text-red-400">Pain Points ({painPoints.length})</SLabel>
        {painPoints.length === 0 ? (
          <p className="text-[10px] text-slate-600 italic">None identified yet.</p>
        ) : (
          <div className="space-y-1.5">
            {painPoints.map((pp) => <PainPointRow key={pp.id} pp={pp} />)}
          </div>
        )}
      </div>

      {/* Business Ease Opportunity */}
      {opportunities.length > 0 && (
        <div className="space-y-1.5">
          <SLabel icon={Lightbulb} color="text-violet-400">Business Ease Opportunity</SLabel>
          <div className="space-y-1.5">
            {opportunities.map((opp) => <OpportunityRow key={opp.id} opp={opp} />)}
          </div>
        </div>
      )}

      {/* Recommended next analysis */}
      <div className="flex items-center justify-between gap-2 p-2.5 rounded-lg bg-slate-800/40 border border-slate-700/30">
        <div className="flex items-center gap-1.5 min-w-0">
          <HelpCircle size={11} className="text-slate-500 shrink-0" />
          <span className="text-[10px] text-slate-400 truncate">{next.label}</span>
        </div>
        {next.actionable && (
          <button
            onClick={() => analyzeMutation.mutate()}
            disabled={analyzeMutation.isPending}
            className="btn-primary text-[10px] py-1 px-2.5 shrink-0"
          >
            {analyzeMutation.isPending ? <Loader2 size={10} className="animate-spin" /> : <Sparkles size={10} />}
            Run pain point analysis
          </button>
        )}
      </div>
      {analyzeMutation.isError && (
        <p className="text-[10px] text-red-400">{analyzeMutation.error?.message || 'Analysis failed.'}</p>
      )}
    </div>
  )
}
