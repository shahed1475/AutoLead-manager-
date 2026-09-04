import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { automationApi, leadsApi, settingsApi } from '../../api/client'
import { RESEARCH_BADGE, RESEARCH_LABEL } from '../../lib/badges'
import { researchSentToast } from '../../lib/researchToast'

function QueueStat({ label, value, cls }) {
  return (
    <div className="text-center">
      <p className={`text-lg font-bold tabular-nums ${cls || 'text-slate-100'}`}>{value ?? 0}</p>
      <p className="text-[10px] text-slate-500">{label}</p>
    </div>
  )
}

export default function AutomationResearch() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [selected, setSelected] = useState(() => new Set())
  const [sending, setSending] = useState(false)

  const { data: status } = useQuery({
    queryKey: ['automation-status'],
    queryFn: automationApi.status,
    refetchInterval: 10000,
  })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: settingsApi.getAll })
  const { data: leadPage } = useQuery({
    queryKey: ['leads', { source_type: 'automation', page_size: 25, sort_by: 'created_at' }],
    queryFn: () => leadsApi.list({ source_type: 'automation', page_size: 25, sort_by: 'created_at', sort_dir: 'desc' }),
    refetchInterval: 15000,
  })

  const q = status?.research_queue || {}
  const autoOn = settings?.research_handoff_mode === 'automatic'
  const leads = leadPage?.items || []

  const toggle = (id) => setSelected((prev) => {
    const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n
  })
  const toggleAll = () => setSelected((prev) => {
    const ids = leads.map((l) => l.id)
    return ids.every((i) => prev.has(i)) ? new Set() : new Set(ids)
  })

  async function toggleAuto() {
    try {
      await settingsApi.update('research_handoff_mode', autoOn ? 'manual' : 'automatic')
      qc.invalidateQueries({ queryKey: ['settings'] })
      toast.success(autoOn ? 'Automatic handoff turned off' : 'New eligible leads will be sent automatically')
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not update setting')
    }
  }

  async function sendSelected() {
    setSending(true)
    try {
      const res = await leadsApi.research([...selected], 'manual_from_automation')
      researchSentToast(res, navigate)
      setSelected(new Set())
      qc.invalidateQueries({ queryKey: ['leads'] })
      qc.invalidateQueries({ queryKey: ['automation-status'] })
      qc.invalidateQueries({ queryKey: ['research-sessions'] })
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not start research')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 space-y-4">
      <div className="flex items-center gap-2">
        <Bot size={16} className="text-indigo-400" />
        <h3 className="text-sm font-bold text-slate-100">Research Agent</h3>
      </div>

      <label className="flex items-start gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3 cursor-pointer">
        <input type="checkbox" className="mt-0.5" checked={autoOn} onChange={toggleAuto} />
        <span className="text-xs">
          <span className="font-medium text-slate-200">Automatically send new leads to Research Agent</span>
          <span className="block text-slate-500">
            Eligible newly-discovered leads (score ≥ {settings?.research_handoff_min_score || 60}) are queued automatically.
            Persists until turned off. You can still send leads manually below.
          </span>
        </span>
      </label>

      <div className="grid grid-cols-4 gap-2 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
        <QueueStat label="Queued" value={q.queued} cls="text-amber-400" />
        <QueueStat label="Researching" value={q.researching} cls="text-sky-400" />
        <QueueStat label="Completed" value={q.completed} cls="text-emerald-400" />
        <QueueStat label="Failed" value={q.failed} cls="text-rose-400" />
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <p className="text-[10px] uppercase tracking-widest text-slate-500">Recent discovered leads</p>
          <button
            className="btn-secondary text-[11px] !py-1"
            disabled={selected.size === 0 || sending}
            onClick={sendSelected}
          >
            {sending ? <Loader2 size={11} className="animate-spin" /> : <Bot size={11} />} Send to Research Agent ({selected.size})
          </button>
        </div>
        <div className="rounded-lg border border-slate-800 overflow-hidden max-h-72 overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-slate-900">
              <tr className="text-left text-[10px] uppercase tracking-widest text-slate-500">
                <th className="px-3 py-2 w-8">
                  <input type="checkbox" aria-label="Select all"
                    checked={leads.length > 0 && leads.every((l) => selected.has(l.id))}
                    onChange={toggleAll} />
                </th>
                <th className="px-3 py-2">Business</th>
                <th className="px-3 py-2">Score</th>
                <th className="px-3 py-2">Research</th>
              </tr>
            </thead>
            <tbody>
              {leads.length === 0 && (
                <tr><td colSpan={4} className="px-3 py-6 text-center text-slate-600">No automation leads yet.</td></tr>
              )}
              {leads.map((l) => (
                <tr key={l.id} className="border-t border-slate-800/60">
                  <td className="px-3 py-2">
                    <input type="checkbox" checked={selected.has(l.id)} onChange={() => toggle(l.id)}
                      aria-label={`Select ${l.business_name}`} />
                  </td>
                  <td className="px-3 py-2 text-slate-200">
                    {l.business_name}
                    {l.discovery_status === 'MINIMAL' && (
                      <span className="ml-1.5 text-[9px] text-slate-500 uppercase">minimal</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-slate-400">{l.score || '—'}</td>
                  <td className="px-3 py-2">
                    <span className={RESEARCH_BADGE[l.research_status] || 'text-slate-500'}>
                      {RESEARCH_LABEL[l.research_status] || 'Not researched'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
