import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  X, Sparkles, Eye, AlertTriangle, Globe, Target,
  TrendingUp, Lightbulb, Zap, CheckCircle2,
} from 'lucide-react'
import { enrichApi } from '../api/client'
import ScoreBadge from './ScoreBadge'

// ── SVG score ring ────────────────────────────────────────────────────────────

const RING_COLOR = { HOT: '#ef4444', WARM: '#f97316', COLD: '#64748b' }

function ScoreRing({ score = 0, label = 'COLD' }) {
  const r    = 26
  const circ = 2 * Math.PI * r
  const pct  = Math.min(100, Math.max(0, score))
  const dash = (pct / 100) * circ
  const color = RING_COLOR[label?.toUpperCase()] || RING_COLOR.COLD

  return (
    <div className="relative w-[68px] h-[68px] shrink-0">
      <svg className="w-full h-full -rotate-90" viewBox="0 0 68 68">
        <circle cx="34" cy="34" r={r} fill="none" stroke="#1e293b"    strokeWidth="6" />
        <circle cx="34" cy="34" r={r} fill="none" stroke={color}
          strokeWidth="6" strokeLinecap="round"
          strokeDasharray={`${dash} ${circ - dash}`}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        <span className="text-xl font-extrabold text-slate-100">{Math.round(pct)}</span>
        <span className="text-[8px] font-bold uppercase tracking-widest mt-0.5"
          style={{ color }}>{label}</span>
      </div>
    </div>
  )
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function Skeleton({ w = 'w-full', h = 'h-3' }) {
  return <div className={`${w} ${h} rounded bg-slate-700/60 animate-pulse`} />
}

function DrawerSkeleton() {
  return (
    <div className="p-5 space-y-5">
      <div className="flex items-center gap-3">
        <Skeleton w="w-[68px]" h="h-[68px]" />
        <div className="flex-1 space-y-2">
          <Skeleton w="w-3/4" h="h-4" />
          <Skeleton w="w-1/2" />
        </div>
      </div>
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="space-y-2">
          <Skeleton w="w-24" h="h-2.5" />
          <Skeleton />
          <Skeleton w="w-4/5" />
        </div>
      ))}
    </div>
  )
}

// ── Section label ─────────────────────────────────────────────────────────────

function SLabel({ icon: Icon, children, color = 'text-slate-500' }) {
  return (
    <div className={`flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest ${color}`}>
      {Icon && <Icon size={9} />}
      {children}
    </div>
  )
}

// ── Progress bar ──────────────────────────────────────────────────────────────

function QualityBar({ value }) {
  const pct   = Math.round(Math.min(100, Math.max(0, value || 0)))
  const color = pct >= 70 ? 'bg-emerald-500' : pct >= 40 ? 'bg-amber-500' : 'bg-red-500'
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <SLabel icon={Globe} color="text-sky-400">Website Quality</SLabel>
        <span className="text-[10px] font-bold text-slate-300 tabular-nums">{pct}/100</span>
      </div>
      <div className="h-1.5 rounded-full bg-slate-700 overflow-hidden">
        <div
          className={`h-full rounded-full ${color} transition-all duration-700`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

// ── Tag chips ─────────────────────────────────────────────────────────────────

function TagCloud({ items, color = 'amber' }) {
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

// ── Bullet list ───────────────────────────────────────────────────────────────

function BulletList({ items, dotColor = 'text-red-400' }) {
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

// ── Main drawer ───────────────────────────────────────────────────────────────

export default function EnrichmentDrawer({ lead, onClose, onEnrich, onViewMessages }) {
  const isOpen = Boolean(lead)

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return
    const handler = (e) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [isOpen, onClose])

  const { data, isLoading } = useQuery({
    queryKey: ['enrichment', lead?.id],
    queryFn:  () => enrichApi.getEnrichment(lead.id),
    enabled:  Boolean(lead?.id),
    staleTime: 2 * 60 * 1000,
  })

  const hasEnrichment = Boolean(
    data?.business_summary || data?.marketing_gaps?.length || data?.key_problems?.length
  )

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-40 bg-black/30 backdrop-blur-[1px] transition-opacity duration-300 ${
          isOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onClick={onClose}
      />

      {/* Drawer panel */}
      <div
        className={`fixed top-0 right-0 h-full w-[380px] bg-slate-900 border-l border-slate-700/60
                    shadow-2xl z-50 flex flex-col overflow-hidden
                    transition-transform duration-300 ease-out
                    ${isOpen ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div className="flex items-start justify-between px-5 pt-5 pb-4 border-b border-slate-800 shrink-0">
          <div className="flex-1 min-w-0 pr-3">
            <h2 className="font-bold text-slate-100 text-sm truncate leading-tight">
              {lead?.business_name || '—'}
            </h2>
            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              {lead?.niche && <span className="text-[10px] text-slate-500">{lead.niche}</span>}
              {lead?.city  && <span className="text-[10px] text-slate-600">· {lead.city}</span>}
              {lead?.score_label && (
                <ScoreBadge score={lead.score} label={lead.score_label} size="xs" />
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-all shrink-0"
          >
            <X size={15} />
          </button>
        </div>

        {/* ── Scrollable body ─────────────────────────────────────────────── */}
        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <DrawerSkeleton />
          ) : !data ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-600 gap-2">
              <Globe size={24} />
              <p className="text-sm">No data available</p>
            </div>
          ) : (
            <div className="p-5 space-y-5">

              {/* Score ring + meta */}
              <div className="flex items-center gap-4 p-4 rounded-xl bg-slate-800/50 border border-slate-700/40">
                <ScoreRing
                  score={data.final_score || data.score || 0}
                  label={data.score_category || data.score_label || 'COLD'}
                />
                <div className="flex-1 min-w-0 space-y-1.5">
                  {data.score_category && (
                    <div className="flex items-center gap-1.5">
                      <span className="text-[10px] text-slate-500 uppercase tracking-wide font-semibold">Category</span>
                      <ScoreBadge score={null} label={data.score_category} showScore={false} size="xs" />
                    </div>
                  )}
                  {data.growth_potential && (
                    <div>
                      <span className="text-[10px] text-slate-500 uppercase tracking-wide font-semibold">Growth</span>
                      <p className="text-xs text-slate-300 mt-0.5">{data.growth_potential}</p>
                    </div>
                  )}
                  {data.service_level && (
                    <div>
                      <span className="text-[10px] text-slate-500 uppercase tracking-wide font-semibold">Tier</span>
                      <p className="text-xs text-slate-400 mt-0.5">{data.service_level}</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Not enriched yet prompt */}
              {!hasEnrichment && (
                <div className="flex flex-col items-center gap-3 py-6 text-center">
                  <div className="w-10 h-10 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center">
                    <Sparkles size={16} className="text-slate-500" />
                  </div>
                  <div>
                    <p className="text-sm text-slate-400 font-medium">Not enriched yet</p>
                    <p className="text-xs text-slate-600 mt-0.5">Run AI enrichment to unlock insights</p>
                  </div>
                  <button
                    onClick={() => { onEnrich?.(lead?.id); onClose?.() }}
                    className="btn-primary text-xs py-1.5 px-4"
                  >
                    <Sparkles size={12} /> Enrich Now
                  </button>
                </div>
              )}

              {/* Business Summary */}
              {data.business_summary && (
                <div className="space-y-1.5">
                  <SLabel icon={Globe} color="text-sky-400">Business Summary</SLabel>
                  <p className="text-xs text-slate-300 leading-relaxed">{data.business_summary}</p>
                </div>
              )}

              {/* Target Audience */}
              {data.target_audience && (
                <div className="space-y-1.5">
                  <SLabel icon={Target} color="text-purple-400">Target Audience</SLabel>
                  <p className="text-xs text-slate-300 leading-relaxed">{data.target_audience}</p>
                </div>
              )}

              {/* Marketing Gaps */}
              {data.marketing_gaps?.length > 0 && (
                <div className="space-y-1">
                  <SLabel icon={AlertTriangle} color="text-amber-400">Marketing Gaps</SLabel>
                  <TagCloud items={data.marketing_gaps} color="amber" />
                </div>
              )}

              {/* Conversion + SEO gaps */}
              {data.conversion_gaps?.length > 0 && (
                <div className="space-y-1">
                  <SLabel icon={TrendingUp} color="text-orange-400">Conversion Gaps</SLabel>
                  <TagCloud items={data.conversion_gaps} color="red" />
                </div>
              )}

              {data.seo_gaps?.length > 0 && (
                <div className="space-y-1">
                  <SLabel icon={Zap} color="text-violet-400">SEO Gaps</SLabel>
                  <TagCloud items={data.seo_gaps} color="violet" />
                </div>
              )}

              {/* Website quality bar */}
              {data.website_quality_score > 0 && (
                <QualityBar value={data.website_quality_score} />
              )}

              {/* Key Problems */}
              {data.key_problems?.length > 0 && (
                <div className="space-y-1">
                  <SLabel icon={AlertTriangle} color="text-red-400">Key Problems</SLabel>
                  <BulletList items={data.key_problems} dotColor="text-red-400" />
                </div>
              )}

              {/* Pitch Angles */}
              {data.pitch_angles?.length > 0 && (
                <div className="space-y-1">
                  <SLabel icon={Lightbulb} color="text-yellow-400">Pitch Angles</SLabel>
                  <BulletList items={data.pitch_angles} dotColor="text-yellow-400" />
                </div>
              )}

              {/* Best pitch strategy */}
              {data.best_pitch_strategy && (
                <div className="space-y-1.5">
                  <SLabel icon={TrendingUp} color="text-emerald-400">AI Pitch Strategy</SLabel>
                  <p className="text-xs text-slate-300 leading-relaxed">{data.best_pitch_strategy}</p>
                </div>
              )}

              {/* Opportunity Summary */}
              {data.opportunity_summary && (
                <div className="space-y-1.5">
                  <SLabel icon={CheckCircle2} color="text-teal-400">Opportunity</SLabel>
                  <p className="text-xs text-slate-300 leading-relaxed">{data.opportunity_summary}</p>
                </div>
              )}

              {/* Personalization Hook */}
              {data.personalization_hook && (
                <div className="p-3 rounded-lg bg-brand-600/10 border border-brand-500/20">
                  <SLabel icon={Sparkles} color="text-brand-400">Personalization Hook</SLabel>
                  <p className="text-xs text-slate-300 leading-relaxed mt-1.5">{data.personalization_hook}</p>
                </div>
              )}

              {/* Enriched timestamp */}
              {data.enriched_at && (
                <p className="text-[10px] text-slate-600 pt-1">
                  Enriched {new Date(data.enriched_at).toLocaleString()}
                </p>
              )}
            </div>
          )}
        </div>

        {/* ── Footer actions ──────────────────────────────────────────────── */}
        <div className="px-5 py-4 border-t border-slate-800 flex gap-2 shrink-0">
          <button
            onClick={() => { onEnrich?.(lead?.id); onClose?.() }}
            className="btn-secondary text-xs flex-1 justify-center"
          >
            <Sparkles size={12} /> Enrich
          </button>
          <button
            onClick={() => onViewMessages?.(lead)}
            className="btn-secondary text-xs flex-1 justify-center"
          >
            <Eye size={12} /> Messages
          </button>
          <button
            onClick={onClose}
            className="p-2 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-all"
          >
            <X size={14} />
          </button>
        </div>
      </div>
    </>
  )
}
