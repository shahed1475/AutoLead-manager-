import { useQuery } from '@tanstack/react-query'
import { automationApi } from '../../api/client'

const LEVEL_CLS = { ERROR: 'text-red-400', WARN: 'text-amber-400', INFO: 'text-slate-400' }

function tsLocal(v) {
  return v ? new Date(String(v).endsWith('Z') ? v : `${v}Z`).toLocaleTimeString() : ''
}

export default function AutomationLog() {
  const { data } = useQuery({
    queryKey: ['automation-log'],
    queryFn: () => automationApi.log(100),
    refetchInterval: 5000,
  })
  const lines = data?.lines || []
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950 p-3 max-h-64 overflow-y-auto font-mono text-[11px] leading-relaxed">
      {lines.length === 0 && <p className="text-slate-600">No activity yet.</p>}
      {lines.map((l) => (
        <div key={l.id} className={LEVEL_CLS[l.level] || 'text-slate-400'}>
          <span className="text-slate-600">{tsLocal(l.ts)} </span>
          {l.message}
        </div>
      ))}
    </div>
  )
}
