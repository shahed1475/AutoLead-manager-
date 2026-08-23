import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Mail, MessageCircle, Megaphone, Loader2, Sparkles, Check, X, Pencil, RotateCcw,
  ChevronDown, ChevronUp,
} from 'lucide-react'
import { marketingApi } from '../api/client'

const STATUS_BADGE = {
  READY_FOR_REVIEW: 'bg-amber-500/15 text-amber-400 border-amber-500/20',
  APPROVED:          'bg-emerald-500/15 text-emerald-400 border-emerald-500/20',
  REJECTED:          'bg-red-500/15 text-red-400 border-red-500/20',
}
const STATUS_LABEL = {
  READY_FOR_REVIEW: 'Ready for review',
  APPROVED:          'Approved',
  REJECTED:          'Rejected',
}

function StatusBadge({ status }) {
  const cls = STATUS_BADGE[status] || STATUS_BADGE.READY_FOR_REVIEW
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-semibold border ${cls}`}>
      {STATUS_LABEL[status] || status}
    </span>
  )
}

function ChannelRow({ leadId, msg, onChanged }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(msg.message)
  const [draftSubject, setDraftSubject] = useState(msg.subject || '')

  const approveMutation = useMutation({
    mutationFn: () => marketingApi.approve(leadId, msg.id),
    onSuccess: onChanged,
  })
  const rejectMutation = useMutation({
    mutationFn: () => marketingApi.reject(leadId, msg.id, 'Rejected from review panel'),
    onSuccess: onChanged,
  })
  const editMutation = useMutation({
    mutationFn: () => marketingApi.edit(leadId, msg.id, { message: draft, subject: msg.channel === 'EMAIL' ? draftSubject : undefined }),
    onSuccess: () => { setEditing(false); onChanged() },
  })

  const Icon = msg.channel === 'EMAIL' ? Mail : MessageCircle
  const busy = approveMutation.isPending || rejectMutation.isPending || editMutation.isPending
  const decided = msg.approval_status !== 'READY_FOR_REVIEW'

  return (
    <div className="rounded-lg border border-slate-700/40 bg-slate-900/40 p-2.5 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Icon size={12} className="text-slate-400" />
          <span className="text-[10px] font-semibold text-slate-300">{msg.channel}</span>
        </div>
        <StatusBadge status={msg.approval_status} />
      </div>

      {editing ? (
        <div className="space-y-1.5">
          {msg.channel === 'EMAIL' && (
            <input
              value={draftSubject}
              onChange={(e) => setDraftSubject(e.target.value)}
              placeholder="Subject"
              className="w-full text-xs bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-200"
            />
          )}
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={4}
            className="w-full text-xs bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-slate-200 leading-relaxed"
          />
          <div className="flex gap-1.5">
            <button
              onClick={() => editMutation.mutate()}
              disabled={editMutation.isPending}
              className="btn-primary text-[10px] py-1 px-2"
            >
              {editMutation.isPending ? <Loader2 size={10} className="animate-spin" /> : <Check size={10} />}
              Save
            </button>
            <button onClick={() => setEditing(false)} className="btn-secondary text-[10px] py-1 px-2">Cancel</button>
          </div>
        </div>
      ) : (
        <>
          {msg.subject && <p className="text-[10px] text-slate-500">Subject: <span className="text-slate-400">{msg.subject}</span></p>}
          <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-line">{msg.message}</p>
        </>
      )}

      {!editing && (
        <div className="flex gap-1.5 pt-1">
          {!decided && (
            <>
              <button
                onClick={() => approveMutation.mutate()}
                disabled={busy}
                className="btn-primary text-[10px] py-1 px-2"
              >
                <Check size={10} /> Approve
              </button>
              <button
                onClick={() => setEditing(true)}
                disabled={busy}
                className="btn-secondary text-[10px] py-1 px-2"
              >
                <Pencil size={10} /> Edit
              </button>
              <button
                onClick={() => rejectMutation.mutate()}
                disabled={busy}
                className="btn-secondary text-[10px] py-1 px-2 text-red-400"
              >
                <X size={10} /> Reject
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function VariantGroup({ leadId, variant, messages, onChanged }) {
  const [open, setOpen] = useState(variant === 'PRIMARY')
  const shared = messages[0]

  return (
    <div className="rounded-lg border border-brand-500/20 bg-brand-600/5 overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-start justify-between gap-2 p-2.5 text-left"
      >
        <div className="flex-1 min-w-0">
          <span className="text-[9px] font-bold uppercase tracking-wide text-brand-400">
            {variant === 'PRIMARY' ? 'Primary Message' : `Alternative ${variant.split('_')[1] || ''}`}
          </span>
          <p className="text-xs font-medium text-slate-200 mt-0.5">{shared.pain_point}</p>
        </div>
        {open ? <ChevronUp size={12} className="text-slate-500 shrink-0 mt-0.5" /> : <ChevronDown size={12} className="text-slate-500 shrink-0 mt-0.5" />}
      </button>
      {open && (
        <div className="px-2.5 pb-2.5 space-y-2.5">
          <div className="grid grid-cols-1 gap-1 text-[10px] text-slate-400 bg-slate-900/40 rounded-lg p-2">
            {shared.evidence && <p><span className="text-slate-500 font-semibold">Evidence:</span> {shared.evidence}</p>}
            {shared.business_impact && <p><span className="text-slate-500 font-semibold">Impact:</span> {shared.business_impact}</p>}
            {shared.solution && <p><span className="text-slate-500 font-semibold">Solution:</span> {shared.solution}</p>}
            {shared.business_benefit && <p><span className="text-slate-500 font-semibold">Benefit:</span> {shared.business_benefit}</p>}
            {shared.service_name && <p><span className="text-slate-500 font-semibold">Recommended service:</span> {shared.service_name}</p>}
          </div>
          <div className="space-y-2">
            {messages.map((m) => <ChannelRow key={m.id} leadId={leadId} msg={m} onChanged={onChanged} />)}
          </div>
        </div>
      )}
    </div>
  )
}

export default function MarketingMessagesPanel({ leadId }) {
  const queryClient = useQueryClient()

  const { data: messages = [], isLoading } = useQuery({
    queryKey: ['marketing-messages', leadId],
    queryFn: () => marketingApi.list(leadId),
    enabled: Boolean(leadId),
    retry: false,
    staleTime: 30 * 1000,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['marketing-messages', leadId] })
  const generateMutation = useMutation({
    mutationFn: () => marketingApi.generate(leadId),
    onSuccess: invalidate,
  })

  if (!leadId) return null

  const grouped = messages.reduce((acc, m) => {
    (acc[m.variant] ||= []).push(m)
    return acc
  }, {})
  const variantOrder = ['PRIMARY', 'ALTERNATIVE_1', 'ALTERNATIVE_2'].filter((v) => grouped[v]?.length)

  return (
    <div className="mt-1 pt-4 border-t border-slate-800 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Megaphone size={12} className="text-brand-400" />
          <span className="text-[10px] font-bold uppercase tracking-widest text-brand-400">Marketing Messages</span>
        </div>
        <button
          onClick={() => generateMutation.mutate()}
          disabled={generateMutation.isPending}
          className="btn-secondary text-[10px] py-1 px-2.5"
        >
          {generateMutation.isPending ? <Loader2 size={10} className="animate-spin" /> : messages.length ? <RotateCcw size={10} /> : <Sparkles size={10} />}
          {messages.length ? 'Regenerate' : 'Generate messages'}
        </button>
      </div>

      {generateMutation.isError && (
        <p className="text-[10px] text-red-400">{generateMutation.error?.message || 'Generation failed.'}</p>
      )}

      {isLoading ? (
        <div className="flex items-center gap-2 text-[10px] text-slate-600">
          <Loader2 size={11} className="animate-spin" /> Loading messages…
        </div>
      ) : variantOrder.length === 0 ? (
        <p className="text-[10px] text-slate-600 italic">No messages generated yet.</p>
      ) : (
        <div className="space-y-2">
          {variantOrder.map((variant) => (
            <VariantGroup key={variant} leadId={leadId} variant={variant} messages={grouped[variant]} onChanged={invalidate} />
          ))}
        </div>
      )}
    </div>
  )
}
