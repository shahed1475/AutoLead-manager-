import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { automationApi } from '../../api/client'

const STATUS_CLS = {
  PENDING:   'text-slate-500',
  SEARCHING: 'text-amber-400',
  COMPLETED: 'text-emerald-400',
  PARTIAL:   'text-amber-400',
  FAILED:    'text-red-400',
  SKIPPED:   'text-slate-600',
}
const FILTERS = ['ALL', 'PENDING', 'SEARCHING', 'COMPLETED', 'PARTIAL', 'FAILED']

export default function AutomationQueueTable() {
  const [filter, setFilter] = useState('ALL')
  const { data } = useQuery({
    queryKey: ['automation-queue', filter],
    queryFn: () => automationApi.queue({ status: filter === 'ALL' ? undefined : filter, limit: 500 }),
    refetchInterval: 5000,
  })
  const items = data?.items || []
  const counts = data?.counts_by_status || {}

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 overflow-hidden">
      <div className="flex flex-wrap gap-1.5 p-3 border-b border-slate-800">
        {FILTERS.map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`text-[10px] font-bold uppercase px-2 py-1 rounded ${filter === f ? 'bg-brand-500/20 text-brand-300' : 'text-slate-500 hover:text-slate-300'}`}>
            {f}{f !== 'ALL' && counts[f] ? ` ${counts[f]}` : ''}
          </button>
        ))}
      </div>
      <div className="max-h-[420px] overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-900">
            <tr className="text-left text-[10px] font-bold uppercase tracking-widest text-slate-500 border-b border-slate-800">
              <th className="px-4 py-2">#</th><th className="px-4 py-2">Niche</th>
              <th className="px-4 py-2">City</th><th className="px-4 py-2">State</th>
              <th className="px-4 py-2">Status</th><th className="px-4 py-2">New</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <tr key={it.id} className="border-b border-slate-800/50 last:border-0">
                <td className="px-4 py-2 text-slate-500">{it.position + 1}</td>
                <td className="px-4 py-2 text-slate-200">{it.niche}</td>
                <td className="px-4 py-2 text-slate-400">{it.city}</td>
                <td className="px-4 py-2 text-slate-400">{it.state || '—'}</td>
                <td className={`px-4 py-2 text-[11px] font-semibold ${STATUS_CLS[it.status] || ''}`}>{it.status}</td>
                <td className="px-4 py-2 text-slate-400">{it.new_leads || 0}</td>
              </tr>
            ))}
            {items.length === 0 && <tr><td colSpan={6} className="px-4 py-6 text-center text-xs text-slate-600">No items</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
