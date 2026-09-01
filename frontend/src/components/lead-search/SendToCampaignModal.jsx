import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Send, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { emailCampaignsApi, emailSendersApi } from '../../api/client'

const LARGE_OP = 50

function isDisabledError(err) {
  return /not enabled|disabled|503/i.test(err?.message || '')
}

export default function SendToCampaignModal({ leadIds, leads, defaultName, onClose, onDone }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [name, setName] = useState(defaultName)
  const [senderId, setSenderId] = useState('')
  const [bigConfirm, setBigConfirm] = useState(false)

  const sendersQuery = useQuery({
    queryKey: ['email-senders'], queryFn: emailSendersApi.list, retry: false,
  })
  const senders = sendersQuery.data?.senders || []

  const noEmail = leads.filter((l) => !l.email).length

  const createMut = useMutation({
    mutationFn: () => emailCampaignsApi.createFromSearch({
      name: name.trim(),
      sender_profile_id: senderId ? Number(senderId) : undefined,
      lead_ids: leadIds,
    }),
    onSuccess: (data) => {
      toast.success((t) => (
        <span>
          Added {data.added} lead{data.added === 1 ? '' : 's'} to “{data.name}”.{' '}
          <button className="text-brand-400 underline"
                  onClick={() => { toast.dismiss(t.id); navigate('/email-campaigns') }}>Open →</button>
        </span>
      ), { duration: 8000 })
      if (data.missing_email + data.invalid_email > 0) {
        toast(`${data.missing_email + data.invalid_email} lead(s) had no usable email — added but not emailable.`)
      }
      qc.invalidateQueries({ queryKey: ['email-campaigns'] })
      onDone()
    },
    onError: (err) => {
      toast.error(isDisabledError(err)
        ? 'Enable Email Campaigns first (Email Campaigns page → Enable).'
        : (err.message || 'Could not create campaign'))
    },
  })

  const tooBig = leadIds.length > LARGE_OP
  const canSubmit = name.trim().length > 0 && (!tooBig || bigConfirm) && !createMut.isPending

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
         onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900 p-5 space-y-4"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-100">Send to Email Campaign</h3>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300"><X size={16} /></button>
        </div>

        <p className="text-sm text-slate-400">
          {leadIds.length} lead{leadIds.length === 1 ? '' : 's'} selected
          {noEmail > 0 && <> · <span className="text-amber-400">{noEmail} with no email</span> (added but not emailed)</>}.
          A new campaign is created in <strong>TEST&nbsp;MODE</strong>; nothing is sent.
        </p>

        <label className="block">
          <span className="text-xs font-medium text-slate-400">Campaign name</span>
          <input className="input mt-1" value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Dental clinics — California" />
        </label>

        <label className="block">
          <span className="text-xs font-medium text-slate-400">Send From</span>
          <select className="input mt-1" value={senderId} onChange={(e) => setSenderId(e.target.value)}>
            <option value="">Default / global SMTP</option>
            {senders.map((s) => (
              <option key={s.id} value={s.id} disabled={s.status !== 'connected'}>
                {s.name} — {s.email_address}{s.status !== 'connected' ? ` (${s.status})` : ''}
              </option>
            ))}
          </select>
        </label>

        {tooBig && (
          <label className="flex items-center gap-2 text-sm text-amber-300">
            <input type="checkbox" checked={bigConfirm} onChange={(e) => setBigConfirm(e.target.checked)} />
            Yes, add {leadIds.length} leads
          </label>
        )}

        <div className="flex gap-2 justify-end">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={!canSubmit} onClick={() => createMut.mutate()}>
            {createMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            Create campaign
          </button>
        </div>
      </div>
    </div>
  )
}
