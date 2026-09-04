import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Zap } from 'lucide-react'
import { automationApi } from '../api/client'
import AutomationUpload from '../components/automation/AutomationUpload'
import AutomationDashboard from '../components/automation/AutomationDashboard'
import AutomationQueueTable from '../components/automation/AutomationQueueTable'
import AutomationLog from '../components/automation/AutomationLog'
import AutomationSettings from '../components/automation/AutomationSettings'
import AutomationResearch from '../components/automation/AutomationResearch'

export default function LeadSearchAutomation() {
  const { data: status } = useQuery({
    queryKey: ['automation-status'],
    queryFn: automationApi.status,
    refetchInterval: (q) => (['RUNNING', 'SCHEDULED'].includes(q.state.data?.status) ? 3000 : 15000),
  })
  const hasQueue = (status?.queue_total || 0) > 0

  return (
    <div className="p-6 space-y-5 max-w-4xl">
      <Link to="/lead-search" className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200">
        <ArrowLeft size={13} /> Lead Search
      </Link>
      <div className="flex items-center gap-2">
        <Zap size={18} className="text-brand-400" />
        <h1 className="text-lg font-bold text-slate-100">Lead Search Automation</h1>
      </div>
      <p className="text-sm text-slate-500 -mt-3">
        Discover leads from your imported niche + location list, enrich and score them, and (optionally)
        hand qualified leads to the Research Agent — on a daily schedule.
      </p>

      <div className="space-y-4">
        {!hasQueue && <AutomationUpload onImported={() => window.location.reload()} />}
        {hasQueue && (
          <>
            <AutomationDashboard />
            <AutomationResearch />
            <AutomationSettings />
            <details className="rounded-xl border border-slate-800 bg-slate-900/30">
              <summary className="px-4 py-2 text-xs font-semibold text-slate-400 cursor-pointer">Search queue</summary>
              <div className="p-3 pt-0"><AutomationQueueTable /></div>
            </details>
            <details className="rounded-xl border border-slate-800 bg-slate-900/30">
              <summary className="px-4 py-2 text-xs font-semibold text-slate-400 cursor-pointer">Activity log</summary>
              <div className="p-3 pt-0"><AutomationLog /></div>
            </details>
            <div className="text-right">
              <AutomationUpload compact onImported={() => window.location.reload()} />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
