import { useEffect, useState } from 'react'
import { Bot, RefreshCw } from 'lucide-react'
import TargetTitlesInput from './TargetTitlesInput'
import { useFocusTrap } from '../hooks/useFocusTrap'

// Last-used titles are a per-browser convenience so repeat sends don't mean
// retyping the same list. The research itself never depends on this.
const LS_KEY = 'autolead_research_target_titles'
function loadTitles() {
  try {
    const v = JSON.parse(localStorage.getItem(LS_KEY) || '[]')
    return Array.isArray(v) ? v.filter((t) => typeof t === 'string') : []
  } catch { return [] }
}
function saveTitles(titles) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(titles)) } catch { /* private mode */ }
}

// Asks which decision-maker titles to look for before sending leads to the
// Research Agent. Empty titles = the niche's defaults.
export default function ResearchTitlesDialog({ open, leadCount, niche, pending, onCancel, onConfirm }) {
  const [titles, setTitles] = useState(loadTitles)
  const ref = useFocusTrap(open)

  useEffect(() => {
    if (open) setTitles(loadTitles())
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => e.key === 'Escape' && !pending && onCancel()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, pending, onCancel])

  if (!open) return null

  function confirm() {
    saveTitles(titles)
    onConfirm(titles)
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={() => !pending && onCancel()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label="Send to Research Agent"
        className="card w-full max-w-lg p-5 space-y-4" onClick={(e) => e.stopPropagation()}>
        <div>
          <p className="text-sm font-semibold text-slate-100">
            Research {leadCount} lead{leadCount === 1 ? '' : 's'}
          </p>
          <p className="text-xs text-slate-500 mt-1">
            The Research Agent looks for people with these titles on each business's public pages.
            It keeps everyone it finds with a role, and the first title here decides the primary contact.
          </p>
        </div>
        <TargetTitlesInput titles={titles} onChange={setTitles} niche={niche} disabled={pending}
          inputId="leads-research-target-title" />
        <div className="flex gap-2">
          <button onClick={onCancel} disabled={pending} className="btn-secondary flex-1 justify-center text-xs">Cancel</button>
          <button onClick={confirm} disabled={pending} className="btn-primary flex-1 justify-center text-xs">
            {pending ? <RefreshCw size={12} className="animate-spin" /> : <Bot size={12} />}
            Start research
          </button>
        </div>
      </div>
    </div>
  )
}
