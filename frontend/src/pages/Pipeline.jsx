import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  DndContext, DragOverlay, closestCorners, MouseSensor, TouchSensor, useSensor, useSensors,
  useDroppable, useDraggable,
} from '@dnd-kit/core'
import { GitBranch, Mail, MessageCircle } from 'lucide-react'
import toast from 'react-hot-toast'
import { pipelineApi, dealsApi, statsApi, settingsApi } from '../api/client'
import { fmtMoney } from '../lib/money'
import DealValueDialog from '../components/DealValueDialog'

const COLUMNS = [
  { key: 'NEW',        label: 'New' },
  { key: 'CONTACTED',  label: 'Contacted' },
  { key: 'REPLIED',    label: 'Replied' },
  { key: 'INTERESTED', label: 'Interested' },
  { key: 'MEETING',    label: 'Meeting' },
  { key: 'PROPOSAL',   label: 'Proposal' },
  { key: 'WON',        label: 'Won' },
  { key: 'LOST',       label: 'Lost' },
]

// NEW and CONTACTED are display-only aggregates on the backend (NEW groups
// PENDING/ENRICHED/SCORED/MESSAGES_READY) — a manual move targeting either
// column must send the single canonical status the backend's
// _MANUAL_STAGE_TARGETS allow-list actually accepts, not the column label.
const COLUMN_TO_STATUS = {
  NEW:        'PENDING',
  CONTACTED:  'SENT',
  REPLIED:    'REPLIED',
  INTERESTED: 'INTERESTED',
  MEETING:    'MEETING',
  PROPOSAL:   'PROPOSAL',
  WON:        'WON',
  LOST:       'LOST',
}

const VALUED = new Set(['INTERESTED', 'MEETING', 'PROPOSAL', 'WON'])   // columns where a deal value means something

const SCORE_STYLES = {
  HOT:  'border-red-500/50 bg-red-500/15 text-red-300',
  WARM: 'border-amber-500/50 bg-amber-500/15 text-amber-300',
  COLD: 'border-blue-500/50 bg-blue-500/15 text-blue-300',
}

function LeadCard({ lead, column, currency, onValue }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: String(lead.id) })
  const style = transform
    ? { transform: `translate(${transform.x}px, ${transform.y}px)`, opacity: isDragging ? 0.5 : 1 }
    : undefined
  const ChannelIcon = lead.channel === 'WHATSAPP' ? MessageCircle : Mail

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      className="rounded-lg border border-slate-700/50 bg-slate-900/60 p-2.5 space-y-1.5 cursor-grab active:cursor-grabbing"
    >
      <p className="text-xs font-medium text-slate-200 truncate">{lead.business_name}</p>
      {VALUED.has(column) && (lead.deal_value != null ? (
        <button type="button" onPointerDown={(e) => e.stopPropagation()} onClick={() => onValue?.(lead)}
          className="text-xs font-semibold tabular text-emerald-400 hover:underline" title="Change the deal value">
          {fmtMoney(lead.deal_value, currency)}
        </button>
      ) : onValue && (
        <button type="button" onPointerDown={(e) => e.stopPropagation()} onClick={() => onValue(lead)}
          className="text-[11px] text-slate-500 hover:text-slate-300">+ Add value</button>
      ))}
      <div className="flex items-center justify-between">
        <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-full border ${SCORE_STYLES[lead.score_label] || SCORE_STYLES.COLD}`}>
          {lead.score_label || 'COLD'}
        </span>
        <div className="flex items-center gap-1 text-[10px] text-slate-500">
          <ChannelIcon size={11} />
          <span>{lead.days_in_stage}d</span>
        </div>
      </div>
    </div>
  )
}

function Column({ column, leads, currency, onValue }) {
  const { setNodeRef, isOver } = useDroppable({ id: column.key })
  const total = leads.reduce((sum, l) => sum + (Number(l.deal_value) || 0), 0)
  return (
    <div
      ref={setNodeRef}
      className={`flex-1 min-w-[78vw] sm:min-w-[220px] snap-start rounded-xl border p-2.5 space-y-2 transition-colors ${
        isOver ? 'border-brand-500/50 bg-brand-600/5' : 'border-slate-800 bg-slate-900/30'
      }`}
    >
      <div className="flex items-center justify-between px-1 pb-1">
        <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">{column.label}</span>
        <span className="text-[10px] text-slate-600 tabular">
          {VALUED.has(column.key) && total > 0 && <span className="text-slate-400 mr-1.5">{fmtMoney(total, currency, { compact: true })}</span>}
          {leads.length}
        </span>
      </div>
      <div className="space-y-2 min-h-[40px]">
        {leads.map((lead) => <LeadCard key={lead.id} lead={lead} column={column.key} currency={currency} onValue={onValue} />)}
      </div>
    </div>
  )
}

export default function Pipeline() {
  const queryClient = useQueryClient()
  const [activeLead, setActiveLead] = useState(null)
  // Mouse: drag after a small move. Touch: press and hold, so a swipe still
  // scrolls the board instead of grabbing a card.
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 4 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 6 } }),
  )

  const { data: board = {}, isLoading } = useQuery({
    queryKey: ['pipeline-board'],
    queryFn: pipelineApi.board,
    staleTime: 15_000,
  })

  const refreshMoney = () => ['pipeline-board', 'dashboard-stats', 'stats-revenue', 'stats-results']
    .forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }))
  const moveMutation = useMutation({
    mutationFn: ({ leadId, toStatus, dealValue }) => pipelineApi.moveStage(leadId, toStatus, 'manual drag', dealValue),
    onSuccess: () => { refreshMoney(); setDealFor(null) },
    onError: (err) => toast.error(err?.message || 'Could not move lead'),
  })
  const valueMutation = useMutation({
    mutationFn: ({ leadId, value }) => dealsApi.setValue(leadId, value),
    onSuccess: () => { refreshMoney(); setDealFor(null) },
    onError: (err) => toast.error(err?.message || 'Could not save the value'),
  })

  // Revenue currency and the typical value that pre-fills the dialog.
  const { data: stats } = useQuery({ queryKey: ['dashboard-stats'], queryFn: statsApi.dashboard, staleTime: 60_000 })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: settingsApi.getAll, staleTime: 60_000 })
  const currency = stats?.currency || 'USD'
  const typical = Number(settings?.avg_deal_value) || null
  const [dealFor, setDealFor] = useState(null)   // { lead, won }

  function handleDragStart(event) {
    const id = Number(event.active.id)
    const lead = Object.values(board).flat().find((l) => l.id === id)
    setActiveLead(lead || null)
  }

  function handleDragEnd(event) {
    setActiveLead(null)
    const { active, over } = event
    if (!over) return
    const leadId = Number(active.id)
    const targetColumn = over.id
    const currentColumn = Object.entries(board).find(([, leads]) => leads.some((l) => l.id === leadId))?.[0]
    if (currentColumn === targetColumn) return
    const toStatus = COLUMN_TO_STATUS[targetColumn]
    if (!toStatus) return
    if (toStatus === 'WON') {          // ask what it's worth before counting it as won
      const lead = Object.values(board).flat().find((l) => l.id === leadId)
      setDealFor({ lead, won: true })
      return
    }
    moveMutation.mutate({ leadId, toStatus })
  }

  return (
    <div className="px-4 py-5 sm:p-6 space-y-4">
      <div className="flex items-center gap-2">
        <GitBranch size={18} className="text-brand-400" />
        <h1 className="text-lg font-bold text-slate-100">Pipeline</h1>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-600">Loading pipeline…</p>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <div className="flex gap-3 overflow-x-auto pb-4 snap-x snap-mandatory sm:snap-none -mx-4 px-4 sm:mx-0 sm:px-0 scroll-px-4">
            {COLUMNS.map((column) => (
              <Column key={column.key} column={column} leads={board[column.key] || []} currency={currency}
                onValue={(lead) => setDealFor({ lead, won: false })} />
            ))}
          </div>
          <DragOverlay>{activeLead ? <LeadCard lead={activeLead} currency={currency} /> : null}</DragOverlay>
        </DndContext>
      )}
      <DealValueDialog
        open={!!dealFor} lead={dealFor?.lead} won={dealFor?.won} currency={currency} typical={typical}
        pending={moveMutation.isPending || valueMutation.isPending}
        onCancel={() => setDealFor(null)}
        onSave={(value) => (dealFor.won
          ? moveMutation.mutate({ leadId: dealFor.lead.id, toStatus: 'WON', dealValue: value })
          : valueMutation.mutate({ leadId: dealFor.lead.id, value }))}
      />
    </div>
  )
}
