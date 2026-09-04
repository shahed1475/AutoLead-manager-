import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Mail, Plus, Loader2, ShieldCheck, ArrowLeft } from 'lucide-react'
import toast from 'react-hot-toast'
import { emailCampaignsApi, emailSendersApi, settingsApi } from '../api/client'
import { Link } from 'react-router-dom'
import { SkeletonTableRows } from '../components/ui/Skeleton'
import EmptyState from '../components/ui/EmptyState'
import ErrorState from '../components/ui/ErrorState'
import CampaignDetailPanel from '../components/email-campaigns/CampaignDetailPanel'
import { CAMPAIGN_STATUS_BADGE } from '../lib/emailCampaignBadges'

function isDisabledError(err) {
  return /not enabled|disabled|503/i.test(err?.message || '')
}

function DisabledPanel({ onEnabled }) {
  const enableMut = useMutation({
    mutationFn: () => settingsApi.update('email_campaigns_enabled', 'true'),
    onSuccess: () => { toast.success('Email Campaigns enabled'); onEnabled() },
    onError: (e) => toast.error(e.message || 'Could not enable'),
  })
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-6 max-w-lg">
      <div className="flex items-center gap-2 text-slate-200 font-semibold">
        <ShieldCheck size={16} className="text-brand-400" /> Preview feature — off by default
      </div>
      <p className="text-sm text-slate-400 mt-2">
        Email Campaigns is a preview module. It runs in <strong>TEST&nbsp;MODE</strong> only:
        every generated email is redirected to your test recipient and no prospect is
        contacted. Enable it to try it out.
      </p>
      <button
        onClick={() => enableMut.mutate()}
        disabled={enableMut.isPending}
        className="btn-primary mt-4"
      >
        {enableMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
        Enable Email Campaigns
      </button>
    </div>
  )
}

function CreateForm({ onCreated }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ name: '', description: '', from_name: '', from_email: '',
                                     ai_enabled: true, sender_profile_id: '', reply_to: '' })
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  const sendersQuery = useQuery({
    queryKey: ['email-senders'], queryFn: emailSendersApi.list, retry: false,
  })
  const senders = sendersQuery.data?.senders || []

  const createMut = useMutation({
    mutationFn: () => emailCampaignsApi.create({
      name: form.name.trim(),
      description: form.description.trim() || undefined,
      from_name: form.from_name.trim() || undefined,
      from_email: form.from_email.trim() || undefined,
      ai_enabled: form.ai_enabled,
      sender_profile_id: form.sender_profile_id ? Number(form.sender_profile_id) : undefined,
      reply_to: form.reply_to.trim() || undefined,
    }),
    onSuccess: (camp) => {
      toast.success('Campaign created')
      setForm({ name: '', description: '', from_name: '', from_email: '', ai_enabled: true,
                sender_profile_id: '', reply_to: '' })
      setOpen(false)
      onCreated(camp)
    },
    onError: (e) => toast.error(e.message || 'Could not create campaign'),
  })

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="btn-primary">
        <Plus size={14} /> New campaign
      </button>
    )
  }

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (!form.name.trim()) { toast.error('Name is required'); return } createMut.mutate() }}
      className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 space-y-3"
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs font-medium text-slate-400">Campaign name *</span>
          <input className="input mt-1" value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Dental clinics — Texas" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-400">Description</span>
          <input className="input mt-1" value={form.description} onChange={(e) => set('description', e.target.value)} placeholder="Optional" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-400">From name</span>
          <input className="input mt-1" value={form.from_name} onChange={(e) => set('from_name', e.target.value)} placeholder="Optional — falls back to SMTP settings" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-400">From email</span>
          <input className="input mt-1" value={form.from_email} onChange={(e) => set('from_email', e.target.value)} placeholder="Optional (used only for the global SMTP fallback)" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-400">Send From</span>
          <select className="input mt-1" value={form.sender_profile_id}
                  onChange={(e) => set('sender_profile_id', e.target.value)}>
            <option value="">Default / global SMTP</option>
            {senders.map((s) => (
              <option key={s.id} value={s.id} disabled={s.status !== 'connected'}>
                {s.name} — {s.email_address}{s.status !== 'connected' ? ` (${s.status})` : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-400">Reply-To</span>
          <input className="input mt-1" value={form.reply_to} onChange={(e) => set('reply_to', e.target.value)} placeholder="Optional — does not change the From identity" />
        </label>
      </div>
      {senders.length === 0 && !sendersQuery.isLoading && (
        <p className="text-[11px] text-slate-500">
          No sender profiles yet. <Link to="/settings" className="text-brand-400">Settings → Email Senders</Link> to add one, or leave “Default / global SMTP”.
        </p>
      )}
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={form.ai_enabled} onChange={(e) => set('ai_enabled', e.target.checked)} />
        Generate email copy with AI (leads without a supplied body)
      </label>
      <div className="flex gap-2">
        <button type="submit" disabled={createMut.isPending} className="btn-primary">
          {createMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Create
        </button>
        <button type="button" onClick={() => setOpen(false)} className="btn-secondary">Cancel</button>
      </div>
    </form>
  )
}

function CampaignRow({ camp, onOpen }) {
  const t = camp.totals || {}
  return (
    <tr className="border-b border-slate-800/60 last:border-0 hover:bg-slate-800/30 cursor-pointer" onClick={() => onOpen(camp.id)}>
      <td className="px-4 py-3">
        <p className="font-medium text-slate-200">{camp.name}</p>
        {camp.description && <p className="text-xs text-slate-500 truncate max-w-[280px]">{camp.description}</p>}
      </td>
      <td className="px-4 py-3">
        <span className={CAMPAIGN_STATUS_BADGE[camp.status] || 'badge'}>{camp.status}</span>
      </td>
      <td className="px-4 py-3 text-xs text-slate-400">
        {(camp.sent_count ?? 0)} sent / {(camp.total_leads ?? 0)} leads
      </td>
      <td className="px-4 py-3 text-xs text-slate-500">
        {camp.updated_at ? new Date(camp.updated_at).toLocaleString() : '—'}
      </td>
    </tr>
  )
}

export default function EmailCampaigns() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState(null)

  const listQuery = useQuery({
    queryKey: ['email-campaigns'],
    queryFn: emailCampaignsApi.list,
    retry: (count, err) => !isDisabledError(err) && count < 1,
    refetchInterval: (q) =>
      (q.state.data?.campaigns || []).some((c) => c.status === 'RUNNING') ? 4000 : false,
  })

  const refetchAll = () => queryClient.invalidateQueries({ queryKey: ['email-campaigns'] })

  if (listQuery.isError && isDisabledError(listQuery.error)) {
    return (
      <div className="p-6 space-y-5 max-w-4xl">
        <div className="flex items-center gap-2">
          <Mail size={18} className="text-brand-400" />
          <h1 className="text-lg font-bold text-slate-100">Email Campaigns</h1>
        </div>
        <DisabledPanel onEnabled={refetchAll} />
      </div>
    )
  }

  if (selectedId != null) {
    return (
      <div className="p-6 space-y-5 max-w-5xl">
        <button onClick={() => setSelectedId(null)} className="btn-secondary text-xs">
          <ArrowLeft size={12} /> All campaigns
        </button>
        <CampaignDetailPanel
          campaignId={selectedId}
          onChanged={refetchAll}
          onDeleted={() => { setSelectedId(null); refetchAll() }}
        />
      </div>
    )
  }

  const campaigns = listQuery.data?.campaigns || []

  return (
    <div className="p-6 space-y-5 max-w-4xl">
      <div className="flex items-center gap-2">
        <Mail size={18} className="text-brand-400" />
        <h1 className="text-lg font-bold text-slate-100">Email Campaigns</h1>
      </div>
      <p className="text-sm text-slate-500 -mt-3">
        Import leads, generate or supply the copy, and send. Runs in <strong>TEST MODE</strong> —
        every email goes to your test recipient, never to a prospect.
      </p>

      <CreateForm onCreated={(camp) => { refetchAll(); setSelectedId(camp.id) }} />

      {listQuery.isLoading && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/30 overflow-hidden">
          <table className="w-full text-sm"><tbody><SkeletonTableRows rows={4} cols={4} /></tbody></table>
        </div>
      )}

      {listQuery.isError && !isDisabledError(listQuery.error) && (
        <ErrorState message="Could not load campaigns." onRetry={() => listQuery.refetch()} retrying={listQuery.isFetching} />
      )}

      {!listQuery.isLoading && !listQuery.isError && campaigns.length === 0 && (
        <EmptyState icon={Mail} title="No campaigns yet" description="Create your first campaign to get started." />
      )}

      {campaigns.length > 0 && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/30 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-left text-[10px] font-bold uppercase tracking-widest text-slate-500">
                  <th className="px-4 py-2.5">Campaign</th>
                  <th className="px-4 py-2.5">Status</th>
                  <th className="px-4 py-2.5">Progress</th>
                  <th className="px-4 py-2.5">Updated</th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => <CampaignRow key={c.id} camp={c} onOpen={setSelectedId} />)}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
