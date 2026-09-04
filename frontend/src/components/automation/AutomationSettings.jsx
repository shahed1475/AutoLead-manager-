import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FlaskConical, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { automationApi } from '../../api/client'

const ZONES = [
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'America/Phoenix', 'America/Anchorage', 'Pacific/Honolulu', 'America/Toronto',
  'America/Sao_Paulo', 'Europe/London', 'Europe/Paris', 'Europe/Berlin',
  'Europe/Madrid', 'Europe/Rome', 'Europe/Amsterdam', 'Europe/Moscow',
  'Africa/Johannesburg', 'Asia/Dubai', 'Asia/Karachi', 'Asia/Kolkata',
  'Asia/Dhaka', 'Asia/Bangkok', 'Asia/Singapore', 'Asia/Hong_Kong',
  'Asia/Shanghai', 'Asia/Tokyo', 'Australia/Sydney', 'Pacific/Auckland', 'UTC',
]

// Must match backend/automation/config.py::_INT_BOUNDS — the server rejects
// anything outside these, so keep the form from ever sending an invalid value.
const BOUNDS = {
  automation_daily_limit:     [1, 100000],
  automation_duration_hours:  [0, 24],
  automation_per_item_target: [1, 40],
  automation_max_retries:     [0, 5],
}
const clampField = (k, v) => {
  const [lo, hi] = BOUNDS[k]
  const n = Math.round(Number(v))
  if (!Number.isFinite(n)) return lo
  return Math.max(lo, Math.min(n, hi))
}

const FIELD_KEYS = [
  'automation_enabled', 'automation_daily_limit', 'automation_start_time', 'automation_timezone',
  'automation_duration_hours', 'automation_per_item_target', 'automation_max_retries',
]
const normalize = (f) => ({
  automation_enabled: !!f.automation_enabled,
  automation_daily_limit: clampField('automation_daily_limit', f.automation_daily_limit),
  automation_start_time: String(f.automation_start_time || '07:00'),
  automation_timezone: String(f.automation_timezone || 'UTC'),
  automation_duration_hours: clampField('automation_duration_hours', f.automation_duration_hours),
  automation_per_item_target: clampField('automation_per_item_target', f.automation_per_item_target),
  automation_max_retries: clampField('automation_max_retries', f.automation_max_retries),
})

export default function AutomationSettings() {
  const qc = useQueryClient()
  const { data: s } = useQuery({ queryKey: ['automation-status'], queryFn: automationApi.status })
  const [form, setForm] = useState(null)
  const [testResult, setTestResult] = useState(null)

  useEffect(() => {
    if (s?.settings && !form) setForm({ ...s.settings })
  }, [s, form])

  const dirty = useMemo(() => {
    if (!form || !s?.settings) return false
    const a = normalize(form), b = normalize(s.settings)
    return FIELD_KEYS.some((k) => a[k] !== b[k])
  }, [form, s])

  const saveM = useMutation({
    mutationFn: () => automationApi.saveSettings(normalize(form)),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['automation-status'] })
      qc.invalidateQueries({ queryKey: ['automation-log'] })
      if (data?.settings) setForm({ ...data.settings })   // reflect the server's canonical values
      toast.success('Settings saved')
    },
    onError: (e) => toast.error(e?.response?.data?.detail || 'Save failed'),
  })

  const testM = useMutation({
    mutationFn: automationApi.testSearch,
    onSuccess: (r) => { setTestResult(r); qc.invalidateQueries({ queryKey: ['automation-log'] }) },
    onError: (e) => { setTestResult(null); toast.error(e?.response?.data?.detail || 'Test search failed') },
  })

  if (!form) return null

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))
  const clampOnBlur = (k) => () => setForm((f) => ({ ...f, [k]: clampField(k, f[k]) }))
  const inputCls = 'mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200 focus:border-brand-500 focus:outline-none'
  const hintCls = 'mt-1 block text-[11px] text-slate-500'
  const legendCls = 'text-[10px] font-semibold uppercase tracking-widest text-slate-500'

  const hasQueue = (s?.queue_total || 0) > 0
  const limit = clampField('automation_daily_limit', form.automation_daily_limit)
  const remaining = Math.max(0, (s?.queue_total || 0) - (s?.searches_completed || 0))
  const minDays = hasQueue && limit > 0 && remaining > 0 ? Math.ceil(remaining / limit) : null
  const blocked = !!form.automation_enabled && ['STOPPED', 'PAUSED'].includes(s?.status)

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-slate-100">Automation Settings</h3>
        {dirty && <span className="text-[11px] font-medium text-amber-400">Unsaved changes</span>}
      </div>

      <label className="flex items-start gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3 cursor-pointer">
        <input type="checkbox" className="mt-0.5" checked={!!form.automation_enabled} onChange={set('automation_enabled')} />
        <span className="text-xs">
          <span className="font-medium text-slate-200">Enable Daily Automation</span>
          <span className="block text-slate-500">
            Runs the imported search list every day on the schedule below. Saving with this on hands control to the scheduler.
          </span>
        </span>
      </label>
      {blocked && (
        <p className="-mt-2 text-[11px] text-amber-400/90">
          Automation is currently {s.status.toLowerCase()} —{' '}
          {s.status === 'PAUSED' ? 'press “Resume” above.' : 'save these settings to hand it back to the scheduler, or press “Start Now” above.'}
        </p>
      )}

      <div className="space-y-3">
        <p className={legendCls}>Schedule</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm">
          <label className="block">
            <span className="text-xs text-slate-400">Daily Start Time</span>
            <input type="time" value={form.automation_start_time} onChange={set('automation_start_time')} className={inputCls} />
          </label>
          <label className="block">
            <span className="text-xs text-slate-400">Timezone</span>
            <select value={form.automation_timezone} onChange={set('automation_timezone')} className={inputCls}>
              {!ZONES.includes(form.automation_timezone) && <option value={form.automation_timezone}>{form.automation_timezone}</option>}
              {ZONES.map((z) => <option key={z} value={z}>{z}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-slate-400">Search Duration (hours)</span>
            <input type="number" min={0} max={24} value={form.automation_duration_hours}
              onChange={set('automation_duration_hours')} onBlur={clampOnBlur('automation_duration_hours')} className={inputCls} />
            <span className={hintCls}>How long the daily run may keep searching. 0 = no limit.</span>
          </label>
        </div>
      </div>

      <div className="space-y-3">
        <p className={legendCls}>Search scope</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
          <label className="block">
            <span className="text-xs text-slate-400">Daily Lead Limit</span>
            <input type="number" min={1} max={100000} value={form.automation_daily_limit}
              onChange={set('automation_daily_limit')} onBlur={clampOnBlur('automation_daily_limit')} className={inputCls} />
            <span className={hintCls}>Stop each day once this many new leads have been found (1–100,000).</span>
          </label>
          <label className="block">
            <span className="text-xs text-slate-400">Leads per search</span>
            <input type="number" min={1} max={40} value={form.automation_per_item_target}
              onChange={set('automation_per_item_target')} onBlur={clampOnBlur('automation_per_item_target')} className={inputCls} />
            <span className={hintCls}>Target businesses per niche × city (1–40). Re-runs top up over successive days.</span>
          </label>
        </div>
        {minDays != null && (
          <p className={hintCls}>
            ≈ {remaining.toLocaleString()} searches left · minimum ~{minDays.toLocaleString()} day{minDays === 1 ? '' : 's'} at {limit.toLocaleString()} leads/day
            {' '}(assumes ≥ 1 new lead per search — usually faster).
          </p>
        )}
      </div>

      <div className="space-y-3">
        <p className={legendCls}>Reliability</p>
        <label className="block sm:max-w-xs">
          <span className="text-xs text-slate-400">Retries per failed search</span>
          <input type="number" min={0} max={5} value={form.automation_max_retries}
            onChange={set('automation_max_retries')} onBlur={clampOnBlur('automation_max_retries')} className={inputCls} />
          <span className={hintCls}>Re-attempts (with a smaller target) when a search errors or times out (0–5).</span>
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button onClick={() => saveM.mutate()} disabled={saveM.isPending || !dirty} className="btn-primary text-xs">
          {saveM.isPending ? 'Saving…' : 'Save Settings'}
        </button>
        <button onClick={() => testM.mutate()} disabled={testM.isPending || !hasQueue}
          className="btn-secondary text-xs flex items-center gap-1.5">
          {testM.isPending ? <Loader2 size={12} className="animate-spin" /> : <FlaskConical size={12} />}
          {testM.isPending ? 'Running test search…' : 'Run a test search'}
        </button>
      </div>
      <p className={hintCls}>
        “Run a test search” does one real search now with the settings above — found leads are enriched, scored and kept
        just like a scheduled run — without advancing the daily counter or queue position.
      </p>

      {testResult && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3 text-xs space-y-1">
          <p className="font-medium text-slate-300">{testResult.niche} · {testResult.location}</p>
          {testResult.error ? (
            <p className="text-rose-400">{testResult.error}</p>
          ) : (
            <p className="text-slate-400">
              <span className="font-semibold text-emerald-400">{testResult.new_leads}</span> new ·
              {' '}{testResult.total_found} found ·
              {' '}{testResult.merged} merged ·
              {' '}{testResult.emails_found} email{testResult.emails_found === 1 ? '' : 's'}
              {testResult.sources_used?.length ? ` · via ${testResult.sources_used.join(', ')}` : ''}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
