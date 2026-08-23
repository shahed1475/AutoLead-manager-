import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GitBranch, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { pipelineApi } from '../api/client'

const MANUAL_STAGES = ['MEETING', 'PROPOSAL', 'WON', 'LOST']

const STAGE_LABEL = {
  MEETING:  'Mark as Meeting Scheduled',
  PROPOSAL: 'Mark as Proposal Sent',
  WON:      'Mark as Won',
  LOST:     'Mark as Lost',
}

export default function StageHistoryPanel({ leadId, currentStatus }) {
  const queryClient = useQueryClient()

  const { data: history = [], isLoading } = useQuery({
    queryKey: ['stage-history', leadId],
    queryFn: () => pipelineApi.stageHistory(leadId),
    enabled: Boolean(leadId),
    staleTime: 15_000,
  })

  const moveMutation = useMutation({
    mutationFn: (toStatus) => pipelineApi.moveStage(leadId, toStatus, 'manual (drawer button)'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stage-history', leadId] })
      queryClient.invalidateQueries({ queryKey: ['pipeline-board'] })
    },
    onError: (err) => toast.error(err?.message || 'Could not move lead'),
  })

  if (!leadId || currentStatus === 'DO_NOT_CONTACT') return null

  return (
    <div className="mt-1 pt-4 border-t border-slate-800 space-y-3">
      <div className="flex items-center gap-1.5">
        <GitBranch size={12} className="text-brand-400" />
        <span className="text-[10px] font-bold uppercase tracking-widest text-brand-400">Deal Stage</span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {MANUAL_STAGES.filter((s) => s !== currentStatus).map((stage) => (
          <button
            key={stage}
            onClick={() => moveMutation.mutate(stage)}
            disabled={moveMutation.isPending}
            className="btn-secondary text-[10px] py-1 px-2"
          >
            {moveMutation.isPending ? <Loader2 size={10} className="animate-spin" /> : null}
            {STAGE_LABEL[stage]}
          </button>
        ))}
      </div>

      {isLoading ? (
        <p className="text-[10px] text-slate-600">Loading history…</p>
      ) : history.length === 0 ? (
        <p className="text-[10px] text-slate-600 italic">No stage changes yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {history.map((h) => (
            <li key={h.id} className="text-[10px] text-slate-500 flex items-baseline gap-1.5">
              <span className="text-slate-300 font-semibold">{h.to_status}</span>
              <span className="text-slate-600">·</span>
              <span>{h.changed_by === 'system' ? 'auto' : 'you'}</span>
              {h.reason && <span className="text-slate-600 truncate">— {h.reason}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
