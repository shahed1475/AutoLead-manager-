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
import BackToFindLeads from '../components/BackToFindLeads'

export default function LeadSearchAutomation() {
  const { data: status } = useQuery({
    queryKey: ['automation-status'],
    queryFn: automationApi.status,
    refetchInterval: (q) => (['RUNNING', 'SCHEDULED'].includes(q.state.data?.status) ? 3000 : 15000),
  })
  const hasQueue = (status?.queue_total || 0) > 0

  return (
    <div className="px-4 sm:px-8 py-8 max-w-5xl mx-auto space-y-6">
      <header>
        <BackToFindLeads />
        <h1 className="text-page">Daily automation</h1>
        <p className="text-support mt-1">
          Import a list of niches and locations, and new leads are found, enriched and scored every day.
          Qualified leads can go on to deep research.
        </p>
      </header>

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
