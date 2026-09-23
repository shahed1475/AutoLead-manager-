import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X, Plus } from 'lucide-react'
import { researchAgentApi } from '../api/client'

// Decision-maker titles the agent should hunt for. Empty = the niche's
// defaults (fetched from the backend so the hint matches what actually runs).
export default function TargetTitlesInput({ titles, onChange, niche, disabled, inputId = 'research-target-title' }) {
  const [draft, setDraft] = useState('')
  const [debouncedNiche, setDebouncedNiche] = useState(niche)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedNiche(niche), 400)
    return () => clearTimeout(t)
  }, [niche])
  const { data } = useQuery({
    queryKey: ['research-titles', debouncedNiche],
    queryFn: () => researchAgentApi.titles(debouncedNiche),
    staleTime: 5 * 60 * 1000,
  })
  const max = data?.max_titles || 10
  const lower = new Set(titles.map((t) => t.toLowerCase()))
  const suggestions = (data?.suggestions || []).filter((t) => !lower.has(t.toLowerCase())).slice(0, 12)

  function add(raw) {
    const parts = raw.split(',').map((t) => t.trim()).filter(Boolean)
    const next = [...titles]
    for (const t of parts) {
      if (next.length >= max) break
      if (!next.some((x) => x.toLowerCase() === t.toLowerCase())) next.push(t.slice(0, 60))
    }
    onChange(next)
    setDraft('')
  }

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-slate-400">Decision-maker titles</span>
        <span className="text-[10px] text-slate-600">{titles.length}/{max} · first title = highest priority</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 focus-within:border-brand-500">
        {titles.map((t, i) => (
          <span key={t} className="inline-flex items-center gap-1 rounded-full border border-brand-500/40 bg-brand-600/15 pl-2 pr-1 py-0.5 text-xs text-brand-200">
            {i === 0 && <span className="text-[9px] font-bold uppercase text-brand-400">1st</span>}
            {t}
            <button type="button" disabled={disabled} onClick={() => onChange(titles.filter((x) => x !== t))}
              aria-label={`Remove ${t}`} className="rounded-full p-0.5 hover:bg-brand-500/30">
              <X size={10} />
            </button>
          </span>
        ))}
        <input
          id={inputId}
          type="text" value={draft} disabled={disabled || titles.length >= max}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if ((e.key === 'Enter' || e.key === ',') && draft.trim()) { e.preventDefault(); add(draft) }
            else if (e.key === 'Backspace' && !draft && titles.length) onChange(titles.slice(0, -1))
          }}
          onBlur={() => draft.trim() && add(draft)}
          placeholder={titles.length ? 'Add another title…' : 'Type a title and press Enter — e.g. Head of Marketing'}
          className="flex-1 min-w-[180px] bg-transparent px-1 py-0.5 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none"
        />
      </div>
      {titles.length === 0 && data?.default_titles?.length > 0 && (
        <p className="text-[11px] text-slate-500">
          Empty uses the defaults for this niche: <span className="text-slate-400">{data.default_titles.join(', ')}</span>
        </p>
      )}
      {suggestions.length > 0 && titles.length < max && (
        <div className="flex flex-wrap gap-1">
          {suggestions.map((t) => (
            <button key={t} type="button" disabled={disabled} onClick={() => add(t)}
              className="inline-flex items-center gap-0.5 rounded-full border border-slate-700 px-2 py-0.5 text-[10px] text-slate-400 hover:border-slate-500 hover:text-slate-200">
              <Plus size={9} />{t}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
