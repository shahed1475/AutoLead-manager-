import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { automationApi, settingsApi } from '../../api/client'

// Set up the daily search by typing it — no file needed. Every niche is
// searched in every place, spread over the days by the daily limit.
const split = (v) => v.split(/[;\n]+/).map((s) => s.trim()).filter(Boolean)
const localZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' } catch { return 'UTC' } }

export default function AutomationQuickSetup() {
  const qc = useQueryClient()
  const [f, setF] = useState({ niches: '', places: '', perDay: 50, time: '09:00', contacts: true })
  const niches = split(f.niches), places = split(f.places)
  const zone = localZone()

  const start = useMutation({
    mutationFn: async () => {
      await automationApi.saveSettings({
        automation_enabled: true, automation_daily_limit: Number(f.perDay) || 50,
        automation_start_time: f.time, automation_timezone: zone,
      })
      if (f.contacts) {
        // Nobody picks leads for research on a schedule: send every new lead.
        await settingsApi.update('research_handoff_mode', 'automatic')
        await settingsApi.update('research_handoff_min_score', '0')
      }
      await automationApi.confirmImport({
        locations: places.map((city) => ({ city, state: null })), niches,
        filename: 'Typed in HOM', layout: 'combined',
      })
      try {
        await automationApi.start()               // run today's first searches now
      } catch (e) {
        if ((e.status ?? e?.response?.status) !== 409) throw e   // 409 = today's limit already met
      }
    },
    onSuccess: () => {
      toast.success('Daily search is on. The first searches are running now.')
      ;['automation-status', 'automation-log', 'automation-queue', 'settings'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }))
    },
    onError: (e) => toast.error(e?.message || 'Could not start the daily search'),
  })

  const ready = niches.length && places.length && Number(f.perDay) > 0 && /^\d{2}:\d{2}$/.test(f.time)
  return (
    <form className="rounded-xl border border-border p-5 space-y-5" onSubmit={(e) => { e.preventDefault(); if (ready) start.mutate() }}>
      <div className="flex items-start gap-3">
        <CalendarClock size={20} className="text-primary shrink-0 mt-0.5" />
        <div>
          <h2 className="text-subheading">Set up a daily search</h2>
          <p className="text-meta mt-0.5">Type what to look for and where. Separate several with a semicolon or a new line.</p>
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="qs-niches">Kinds of business</label>
          <textarea id="qs-niches" rows={3} className="input" value={f.niches} onChange={(e) => setF({ ...f, niches: e.target.value })}
            placeholder="Dental clinics; Law firms" />
        </div>
        <div>
          <label className="label" htmlFor="qs-places">Places</label>
          <textarea id="qs-places" rows={3} className="input" value={f.places} onChange={(e) => setF({ ...f, places: e.target.value })}
            placeholder="Dubai; Austin, TX" />
        </div>
        <div>
          <label className="label" htmlFor="qs-perday">New leads per day</label>
          <input id="qs-perday" type="number" min="1" max="1000" className="input" value={f.perDay}
            onChange={(e) => setF({ ...f, perDay: e.target.value })} />
        </div>
        <div>
          <label className="label" htmlFor="qs-time">Start every day at</label>
          <input id="qs-time" type="time" className="input" value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} />
          <p className="text-meta mt-1">Your time ({zone})</p>
        </div>
      </div>
      <label className="flex items-start gap-3 cursor-pointer">
        <input type="checkbox" className="size-4 mt-0.5 accent-[rgb(var(--primary))]" checked={f.contacts}
          onChange={(e) => setF({ ...f, contacts: e.target.checked })} />
        <span className="text-sm">Find the owner and contacts for every new lead
          <span className="block text-meta">Researches each lead's website and listings for the owner, managers, their titles, phone and email. Every lead also gets its business report.</span>
        </span>
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary active:scale-[0.98]" disabled={!ready || start.isPending}>
          {start.isPending && <Loader2 size={14} className="animate-spin" />} Start daily search
        </button>
        <p className="text-meta">
          {niches.length && places.length
            ? `${niches.length * places.length} search${niches.length * places.length === 1 ? '' : 'es'}, up to ${f.perDay} new leads a day.`
            : 'Fill in both boxes to start.'}
        </p>
      </div>
    </form>
  )
}
