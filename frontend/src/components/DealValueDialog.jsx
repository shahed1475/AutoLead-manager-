import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { useFocusTrap } from '../hooks/useFocusTrap'

// Ask what a deal is worth: when a lead is moved to Won, or from "Add value"
// on a pipeline card. This value is the only thing revenue figures add up.
export default function DealValueDialog({ open, lead, won, currency = 'USD', typical, pending, onCancel, onSave }) {
  const [value, setValue] = useState('')
  const ref = useFocusTrap(open)

  useEffect(() => {
    if (open) setValue(lead?.deal_value != null ? String(lead.deal_value) : typical ? String(typical) : '')
  }, [open, lead, typical])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => e.key === 'Escape' && !pending && onCancel()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, pending, onCancel])

  if (!open) return null
  const n = value.trim() === '' ? null : Number(value)
  const invalid = n != null && (Number.isNaN(n) || n < 0)

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={() => !pending && onCancel()}>
      <form ref={ref} role="dialog" aria-modal="true" aria-label="Deal value"
        className="card w-full max-w-sm p-5 space-y-4" onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => { e.preventDefault(); if (!invalid) onSave(n) }}>
        <div>
          <p className="text-sm font-semibold text-slate-100">{won ? `Won: ${lead?.business_name}` : `Deal value: ${lead?.business_name}`}</p>
          <p className="text-xs text-slate-500 mt-1">
            {won ? 'What is this deal worth? It is added to your revenue.' : 'What is this deal worth if it closes? It shows in your open pipeline.'}
          </p>
        </div>
        <div>
          <label htmlFor="deal-value" className="label">Amount ({currency})</label>
          <input id="deal-value" type="number" min="0" step="any" inputMode="decimal" autoFocus
            className="input" value={value} onChange={(e) => setValue(e.target.value)} />
          {invalid && <p className="text-xs text-error mt-1.5">Enter 0 or more.</p>}
          {!won && <p className="text-xs text-slate-500 mt-1.5">Leave empty to remove the value.</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={onCancel} disabled={pending} className="btn-secondary flex-1 justify-center text-xs">Cancel</button>
          {won && (
            <button type="button" onClick={() => onSave(null)} disabled={pending} className="btn-secondary flex-1 justify-center text-xs">Won, no value</button>
          )}
          <button type="submit" disabled={pending || invalid || (won && n == null)} className="btn-primary flex-1 justify-center text-xs">
            {pending && <Loader2 size={12} className="animate-spin" />} {won ? 'Save as won' : 'Save'}
          </button>
        </div>
      </form>
    </div>
  )
}
