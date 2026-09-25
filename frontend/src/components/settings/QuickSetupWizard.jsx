import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Wand2, Loader2, ChevronDown, ChevronUp } from 'lucide-react'
import toast from 'react-hot-toast'
import { settingsApi } from '../../api/client'

// Quick setup: pick the kind of business, add the name, city and services, and
// get a Company DNA draft in the editor below. Nothing is saved until Save.
export default function QuickSetupWizard({ onDraft, hasContent }) {
  // null = follow the DNA (open only when it's empty); decided after it loads.
  const [openPref, setOpen] = useState(null)
  const open = openPref ?? !hasContent
  const [f, setF] = useState({ preset: '', business_name: '', city: '', services: '' })
  const presets = useQuery({ queryKey: ['industry-presets'], queryFn: settingsApi.industryPresets, enabled: open, staleTime: Infinity })
  const chosen = (presets.data || []).find((p) => p.id === f.preset)

  const draft = useMutation({
    mutationFn: () => settingsApi.presetDna(f.preset, {
      business_name: f.business_name.trim(), city: f.city.trim(),
      services: f.services.split(',').map((s) => s.trim()).filter(Boolean),
    }),
    onSuccess: (r) => { onDraft(r.dna); setOpen(false); toast.success('Draft ready: review it, then Save') },
    onError: (e) => toast.error(e.message),
  })
  const field = 'input text-sm'

  return (
    <div className="mb-4 rounded-lg border border-border">
      <button type="button" onClick={() => setOpen(!open)} className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left">
        <span className="inline-flex items-center gap-2 text-sm font-semibold"><Wand2 size={14} /> Quick setup from a template</span>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open && (
        <form className="px-4 pb-4 grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); draft.mutate() }}>
          <div className="sm:col-span-2">
            <label className="label" htmlFor="qs-type">Kind of business</label>
            <select id="qs-type" className={field} value={f.preset} onChange={(e) => setF({ ...f, preset: e.target.value })} required>
              <option value="">Choose one</option>
              {(presets.data || []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="qs-name">Business name</label>
            <input id="qs-name" className={field} value={f.business_name} onChange={(e) => setF({ ...f, business_name: e.target.value })} required maxLength={120} />
          </div>
          <div>
            <label className="label" htmlFor="qs-city">City</label>
            <input id="qs-city" className={field} value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} maxLength={120} />
          </div>
          <div className="sm:col-span-2">
            <label className="label" htmlFor="qs-services">What you sell, separated by commas</label>
            <input id="qs-services" className={field} value={f.services} onChange={(e) => setF({ ...f, services: e.target.value })}
                   placeholder={chosen ? chosen.services.join(', ') : ''} />
            <p className="text-meta mt-1.5">Leave empty to use the usual services for this kind of business.</p>
          </div>
          <div className="sm:col-span-2 flex flex-wrap items-center gap-3">
            <button type="submit" className="btn-primary active:scale-[0.98]" disabled={!f.preset || !f.business_name.trim() || draft.isPending}>
              {draft.isPending ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />} Create draft
            </button>
            {hasContent && <p className="text-meta">Replaces the text in the editor. Nothing is saved until you press Save.</p>}
          </div>
        </form>
      )}
    </div>
  )
}
