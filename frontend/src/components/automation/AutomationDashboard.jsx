import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, Pause, Square, RotateCcw } from 'lucide-react'
import toast from 'react-hot-toast'
import { automationApi } from '../../api/client'

const PILL = {
  RUNNING:       ['bg-success',   'Running',              'text-emerald-400'],
  SCHEDULED:     ['bg-warning',   'Scheduled',            'text-amber-400'],
  PAUSED:        ['bg-slate-500', 'Paused',               'text-slate-300'],
  LIMIT_REACHED: ['bg-success',   'Daily limit reached',  'text-emerald-400'],
  STOPPED:       ['bg-error',     'Stopped',              'text-red-400'],
  COMPLETED:     ['bg-success',   'Queue complete',       'text-emerald-400'],
  IDLE:          ['bg-slate-500', 'Idle',                 'text-slate-400'],
}
const ACTIVE = new Set(['RUNNING', 'SCHEDULED'])

function Tile({ label, value }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-widest text-slate-500">{label}</p>
      <p className="text-sm font-medium text-slate-200 truncate">{value ?? '—'}</p>
    </div>
  )
}

function fmtDuration(sec) {
  if (sec == null) return '—'
  const s = Math.max(0, Math.floor(sec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

function tsLocal(v) {
  return v ? new Date(String(v).endsWith('Z') ? v : `${v}Z`) : null
}

export default function AutomationDashboard() {
  const qc = useQueryClient()
  const [confirmReset, setConfirmReset] = useState(false)

  const { data: s } = useQuery({
    queryKey: ['automation-status'],
    queryFn: automationApi.status,
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 3000 : 15000),
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['automation-status'] })
    qc.invalidateQueries({ queryKey: ['automation-queue'] })
    qc.invalidateQueries({ queryKey: ['automation-log'] })
  }
  const onErr = (e) => toast.error(e?.response?.data?.detail || 'Action failed')

  const startM = useMutation({ mutationFn: automationApi.start, onSuccess: () => { refresh(); toast.success('Started') }, onError: onErr })
  const pauseM = useMutation({ mutationFn: automationApi.pause, onSuccess: refresh, onError: onErr })
  const resumeM = useMutation({ mutationFn: automationApi.resume, onSuccess: refresh, onError: onErr })
  const stopM = useMutation({ mutationFn: automationApi.stop, onSuccess: refresh, onError: onErr })
  const resetM = useMutation({ mutationFn: automationApi.reset, onSuccess: () => { refresh(); toast.success('Progress reset') }, onError: onErr })

  if (!s) return <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 text-sm text-slate-500">Loading automation…</div>

  const [dot, label, cls] = PILL[s.status] || PILL.IDLE
  const limit = s.settings?.automation_daily_limit || 0
  const pct = s.progress_pct ?? 0
  const deadline = tsLocal(s.duration_deadline)
  const timeLeftSec = deadline ? (deadline - Date.now()) / 1000 : null
  // next_run_at is a full ISO string with offset (computed server-side); parse as-is
  const nextRun = s.next_run_at ? new Date(s.next_run_at) : null

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <span className={`flex items-center gap-2 text-sm font-semibold ${cls}`}><span className={`w-2 h-2 rounded-full ${dot}`} />{label}</span>
        <span className="text-xs text-slate-500">
          {nextRun ? `Next run: ${nextRun.toLocaleString()}` : (s.next_run_reason || 'Not scheduled')}
        </span>
      </div>

      <div>
        <div className="flex justify-between text-xs text-slate-400 mb-1">
          <span>Today's leads</span><span>{s.today_count} / {limit}</span>
        </div>
        <div className="h-2 rounded-full bg-slate-800 overflow-hidden">
          <div className="h-full bg-brand-500 transition-all" style={{ width: `${Math.min(100, pct)}%` }} />
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile label="Total leads (all time)" value={s.total_count} />
        <Tile label="Current niche" value={s.current_niche} />
        <Tile label="Current city" value={s.current_location} />
        <Tile label="Searches done / left" value={`${s.searches_completed} / ${s.searches_remaining}`} />
        <Tile label="Duration" value={`${s.settings?.automation_duration_hours || 0}h`} />
        <Tile label="Time left today" value={fmtDuration(timeLeftSec)} />
        <Tile label="Last success" value={tsLocal(s.last_success_at)?.toLocaleTimeString() ?? '—'} />
        <Tile label="Timezone" value={s.settings?.automation_timezone} />
      </div>

      {s.pipeline_totals?.runs > 0 && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
          <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-2">Pipeline (all searches)</p>
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 text-center">
            {[
              ['Searches', s.pipeline_totals.runs],
              ['Businesses found', s.pipeline_totals.raw_candidates],
              ['Leads stored', s.pipeline_totals.results_count],
              ['Emails found', s.pipeline_totals.emails_found],
              ['Leads scored', s.pipeline_totals.leads_scored],
              ['Sent to research', s.pipeline_totals.research_queued],
            ].map(([label, value]) => (
              <div key={label}>
                <p className="text-base font-bold text-slate-100 tabular-nums">{value ?? 0}</p>
                <p className="text-[10px] text-slate-500 leading-tight">{label}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2 pt-1">
        <button onClick={() => startM.mutate()} disabled={startM.isPending || s.status === 'RUNNING'} className="btn-primary text-xs flex items-center gap-1.5"><Play size={13} /> Start Now</button>
        <button onClick={() => pauseM.mutate()} disabled={!ACTIVE.has(s.status)} className="btn-secondary text-xs flex items-center gap-1.5"><Pause size={13} /> Pause</button>
        <button onClick={() => resumeM.mutate()} disabled={s.status !== 'PAUSED'} className="btn-secondary text-xs flex items-center gap-1.5"><Play size={13} /> Resume</button>
        <button onClick={() => stopM.mutate()} disabled={s.status === 'STOPPED'} className="btn-secondary text-xs flex items-center gap-1.5"><Square size={13} /> Stop</button>
        <button onClick={() => setConfirmReset(true)} className="btn-secondary text-xs flex items-center gap-1.5 ml-auto"><RotateCcw size={13} /> Reset Progress</button>
      </div>

      {confirmReset && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setConfirmReset(false)}>
          <div className="card w-full max-w-sm p-5 space-y-3" onClick={(e) => e.stopPropagation()}>
            <p className="text-sm text-slate-200">Reset the search position to the start? Collected leads and the all-time total are kept; every queue item goes back to Pending.</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmReset(false)} className="btn-secondary text-xs">Cancel</button>
              <button onClick={() => { resetM.mutate(); setConfirmReset(false) }} className="btn-danger text-xs">Reset</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
