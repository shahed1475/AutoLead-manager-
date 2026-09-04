import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Loader2, Upload, Paperclip, Trash2, Play, Pause, PlayCircle,
  CheckCircle2, Sparkles, AlertTriangle, ShieldAlert,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { Link } from 'react-router-dom'
import { emailCampaignsApi, emailSendersApi } from '../../api/client'
import { CAMPAIGN_STATUS_BADGE, LEAD_STATUS_BADGE } from '../../lib/emailCampaignBadges'
import ErrorState from '../ui/ErrorState'
import { SkeletonTableRows } from '../ui/Skeleton'

const ACTIVITY_LEVEL_CLS = {
  INFO: 'text-slate-400', WARNING: 'text-amber-400', ERROR: 'text-red-400',
}

function Stat({ label, value, tone }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`text-lg font-semibold ${tone || 'text-slate-200'}`}>{value ?? 0}</p>
    </div>
  )
}

function FileButton({ label, icon: Icon, accept, onFile, busy, disabled }) {
  const ref = useRef(null)
  return (
    <>
      <input
        ref={ref} type="file" accept={accept} className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) onFile(f) }}
      />
      <button
        type="button" className="btn-secondary text-xs" disabled={busy || disabled}
        onClick={() => ref.current?.click()}
      >
        {busy ? <Loader2 size={12} className="animate-spin" /> : <Icon size={12} />} {label}
      </button>
    </>
  )
}

export default function CampaignDetailPanel({ campaignId, onChanged }) {
  const queryClient = useQueryClient()
  const idemRef = useRef(null)
  const [importResult, setImportResult] = useState(null)
  const [showLeads, setShowLeads] = useState(true)
  const [showActivity, setShowActivity] = useState(false)

  const campQuery = useQuery({
    queryKey: ['email-campaign', campaignId],
    queryFn: () => emailCampaignsApi.get(campaignId),
    refetchInterval: (q) => (q.state.data?.status === 'RUNNING' ? 3000 : false),
  })
  const statsQuery = useQuery({
    queryKey: ['email-campaign-stats', campaignId],
    queryFn: () => emailCampaignsApi.stats(campaignId),
    refetchInterval: (q) => (q.state.data?.status === 'RUNNING' ? 3000 : false),
  })
  const leadsQuery = useQuery({
    queryKey: ['email-campaign-leads', campaignId],
    queryFn: () => emailCampaignsApi.leads(campaignId, { limit: 200 }),
    enabled: showLeads,
    refetchInterval: () => (campQuery.data?.status === 'RUNNING' ? 4000 : false),
  })
  const activityQuery = useQuery({
    queryKey: ['email-campaign-activity', campaignId],
    queryFn: () => emailCampaignsApi.activity(campaignId, 150),
    enabled: showActivity,
    refetchInterval: () => (campQuery.data?.status === 'RUNNING' ? 4000 : false),
  })

  const sendersQuery = useQuery({
    queryKey: ['email-senders'], queryFn: emailSendersApi.list, retry: false,
  })

  const camp = campQuery.data
  const stats = statsQuery.data
  const totals = stats?.totals || {}

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['email-campaign', campaignId] })
    queryClient.invalidateQueries({ queryKey: ['email-campaign-stats', campaignId] })
    queryClient.invalidateQueries({ queryKey: ['email-campaign-leads', campaignId] })
    queryClient.invalidateQueries({ queryKey: ['email-campaign-activity', campaignId] })
    onChanged?.()
  }

  const simpleMut = (fn, ok) => ({
    mutationFn: fn,
    onSuccess: () => { toast.success(ok); refresh() },
    onError: (e) => toast.error(e.message || 'Action failed'),
  })

  const importMut = useMutation({
    mutationFn: (file) => emailCampaignsApi.importLeads(campaignId, file),
    onSuccess: (res) => { setImportResult(res); toast.success(`Imported ${res.valid} valid lead(s)`); refresh() },
    onError: (e) => toast.error(e.message || 'Import failed'),
  })
  const attachMut = useMutation({
    mutationFn: (file) => emailCampaignsApi.uploadAttachment(campaignId, file),
    onSuccess: () => { toast.success('Attachment saved'); refresh() },
    onError: (e) => toast.error(e.message || 'Upload failed'),
  })
  const senderMut = useMutation({
    mutationFn: (v) => emailCampaignsApi.patch(campaignId, { sender_profile_id: v === '' ? null : Number(v) }),
    onSuccess: () => { toast.success('Send From updated'); refresh() },
    onError: (e) => toast.error(e.message || 'Could not update sender'),
  })
  const delAttachMut = useMutation(simpleMut(() => emailCampaignsApi.deleteAttachment(campaignId), 'Attachment removed'))
  const prepareMut = useMutation(simpleMut(() => emailCampaignsApi.prepare(campaignId), 'Preparation done'))
  const readyMut = useMutation(simpleMut(() => emailCampaignsApi.markReady(campaignId), 'Campaign ready'))
  const pauseMut = useMutation(simpleMut(() => emailCampaignsApi.pause(campaignId), 'Paused'))
  const startMut = useMutation({
    mutationFn: () => { idemRef.current = idemRef.current || crypto.randomUUID(); return emailCampaignsApi.start(campaignId, idemRef.current) },
    onSuccess: () => { toast.success('Sending started'); refresh() },
    onError: (e) => toast.error(e.message || 'Could not start'),
  })
  const resumeMut = useMutation({
    mutationFn: () => emailCampaignsApi.resume(campaignId, crypto.randomUUID()),
    onSuccess: () => { toast.success('Resumed'); refresh() },
    onError: (e) => toast.error(e.message || 'Could not resume'),
  })

  if (campQuery.isLoading) {
    return <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-6 text-sm text-slate-500">Loading campaign…</div>
  }
  if (campQuery.isError || !camp) {
    return <ErrorState message="Could not load this campaign." onRetry={() => campQuery.refetch()} retrying={campQuery.isFetching} />
  }

  const att = camp.attachment
  const canEditLeads = camp.status === 'DRAFT' || camp.status === 'READY'
  const validLeads = camp.valid_leads ?? 0
  const pending = totals.pending ?? 0

  return (
    <div className="space-y-5">
      {/* header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-bold text-slate-100">{camp.name}</h2>
            <span className={CAMPAIGN_STATUS_BADGE[camp.status] || 'badge'}>{camp.status}</span>
          </div>
          {camp.description && <p className="text-xs text-slate-500 mt-1">{camp.description}</p>}
        </div>
      </div>

      {/* TEST MODE banner — always on for this build */}
      <div className="rounded-lg border border-amber-600/30 bg-amber-500/10 px-4 py-3 flex items-start gap-2">
        <ShieldAlert size={15} className="text-amber-400 mt-0.5 shrink-0" />
        <p className="text-xs text-amber-200/90">
          <strong>TEST MODE.</strong> Every email is redirected to{' '}
          <span className="font-mono">{camp.test_recipient}</span> and prefixed{' '}
          <span className="font-mono">[TEST&nbsp;-&gt;&nbsp;…]</span>. No prospect is contacted.
        </p>
      </div>

      {/* Send From */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-semibold text-slate-300">Send From</p>
            <p className="text-xs text-slate-500 mt-0.5">
              {camp.sender
                ? <>{camp.sender.provider === 'gmail' ? 'Gmail' : 'SMTP'} · <span className="font-mono">{camp.sender.email_address}</span>
                    {camp.sender.status !== 'connected' && <span className="text-red-400"> ({camp.sender.status})</span>}</>
                : 'Default / global SMTP account'}
            </p>
          </div>
        </div>
        {canEditLeads && (
          (sendersQuery.data?.senders || []).length === 0 ? (
            <p className="text-[11px] text-slate-500 mt-2">
              No sender profiles. <Link to="/settings" className="text-brand-400">Settings → Email Senders</Link>.
            </p>
          ) : (
            <select
              className="input mt-2 text-xs"
              value={camp.sender_profile_id ?? ''}
              disabled={senderMut.isPending}
              onChange={(e) => senderMut.mutate(e.target.value)}
            >
              <option value="">Default / global SMTP</option>
              {(sendersQuery.data?.senders || []).map((s) => (
                <option key={s.id} value={s.id} disabled={s.status !== 'connected'}>
                  {s.name} — {s.email_address}{s.status !== 'connected' ? ` (${s.status})` : ''}
                </option>
              ))}
            </select>
          )
        )}
      </div>

      {/* stats */}
      {statsQuery.isError ? (
        <ErrorState message="Could not load stats." onRetry={() => statsQuery.refetch()} />
      ) : (
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
          <Stat label="Leads" value={totals.total_leads} />
          <Stat label="Valid" value={totals.valid_leads} />
          <Stat label="Pending" value={pending} />
          <Stat label="Sent" value={totals.sent} tone="text-teal-400" />
          <Stat label="Failed" value={(totals.send_failed ?? 0) + (totals.ai_failed ?? 0)} tone="text-amber-400" />
          <Stat label="Blocked" value={totals.send_blocked} tone="text-red-400" />
        </div>
      )}

      {/* lifecycle controls */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-4 flex flex-wrap items-center gap-2">
        {canEditLeads && (
          <button className="btn-secondary text-xs" disabled={prepareMut.isPending || validLeads === 0} onClick={() => prepareMut.mutate()}>
            {prepareMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />} Prepare copy
          </button>
        )}
        {camp.status === 'DRAFT' && (
          <button className="btn-primary text-xs" disabled={readyMut.isPending || validLeads === 0} onClick={() => readyMut.mutate()}>
            {readyMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />} Mark ready
          </button>
        )}
        {camp.status === 'READY' && (
          <button className="btn-primary text-xs" disabled={startMut.isPending} onClick={() => startMut.mutate()}>
            {startMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />} Start sending (test)
          </button>
        )}
        {camp.status === 'RUNNING' && (
          <button className="btn-secondary text-xs" disabled={pauseMut.isPending} onClick={() => pauseMut.mutate()}>
            {pauseMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Pause size={12} />} Pause
          </button>
        )}
        {camp.status === 'PAUSED' && (
          <button className="btn-primary text-xs" disabled={resumeMut.isPending} onClick={() => resumeMut.mutate()}>
            {resumeMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <PlayCircle size={12} />} Resume
          </button>
        )}
        {validLeads === 0 && canEditLeads && (
          <span className="text-xs text-slate-500 flex items-center gap-1">
            <AlertTriangle size={12} /> Import leads to continue
          </span>
        )}
        {(camp.status === 'COMPLETED' || camp.status === 'FAILED') && (
          <span className="text-xs text-slate-400">
            {camp.status === 'COMPLETED' ? 'Campaign finished.' : 'Campaign failed — check the activity log.'}
          </span>
        )}
      </div>

      {/* lead import + attachment */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-4 space-y-2">
          <p className="text-xs font-semibold text-slate-300">Leads</p>
          <p className="text-xs text-slate-500">CSV or XLSX. A <span className="font-mono">body</span> column is sent verbatim; otherwise AI writes it.</p>
          <FileButton
            label="Import leads" icon={Upload} accept=".csv,.xlsx,.xls"
            busy={importMut.isPending} disabled={!canEditLeads}
            onFile={(f) => importMut.mutate(f)}
          />
          {!canEditLeads && <p className="text-[11px] text-slate-600">Locked — campaign is {camp.status}.</p>}
          {importResult && (
            <p className="text-[11px] text-slate-500">
              {importResult.total_rows} rows · {importResult.valid} valid · {importResult.missing_email} no-email ·{' '}
              {importResult.invalid_email} invalid · {importResult.duplicates} duplicate
            </p>
          )}
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-4 space-y-2">
          <p className="text-xs font-semibold text-slate-300">Attachment (one, whole batch)</p>
          {att?.present ? (
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-slate-400 flex items-center gap-1.5 truncate">
                <Paperclip size={12} /> {att.filename} <span className="text-slate-600">({Math.round((att.size || 0) / 1024)} KB)</span>
              </span>
              <button className="btn-danger text-xs" disabled={delAttachMut.isPending} onClick={() => delAttachMut.mutate()}>
                {delAttachMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
              </button>
            </div>
          ) : (
            <FileButton
              label="Attach a file" icon={Paperclip} accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.txt,.csv,.xlsx"
              busy={attachMut.isPending} onFile={(f) => attachMut.mutate(f)}
            />
          )}
        </div>
      </div>

      {/* leads table */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/30">
        <button onClick={() => setShowLeads((s) => !s)} className="w-full flex items-center justify-between px-4 py-2.5 text-xs font-semibold text-slate-300">
          <span>Leads {stats ? `(${totals.total_leads ?? 0})` : ''}</span>
          <span className="text-slate-500">{showLeads ? '−' : '+'}</span>
        </button>
        {showLeads && (
          <div className="px-3 pb-3 overflow-x-auto">
            {leadsQuery.isLoading ? (
              <table className="w-full text-sm"><tbody><SkeletonTableRows rows={3} cols={4} /></tbody></table>
            ) : leadsQuery.isError ? (
              <ErrorState message="Could not load leads." onRetry={() => leadsQuery.refetch()} />
            ) : (leadsQuery.data?.leads || []).length === 0 ? (
              <p className="text-xs text-slate-600 px-1 py-3">No leads imported.</p>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
                    <th className="px-2 py-2">Email</th>
                    <th className="px-2 py-2">Company</th>
                    <th className="px-2 py-2">Subject</th>
                    <th className="px-2 py-2">Status</th>
                    <th className="px-2 py-2">Sent</th>
                  </tr>
                </thead>
                <tbody>
                  {(leadsQuery.data.leads).map((l) => (
                    <tr key={l.lead_key} className="border-b border-slate-800/50 last:border-0">
                      <td className="px-2 py-2 text-slate-300">{l.email || <span className="text-slate-600">—</span>}</td>
                      <td className="px-2 py-2 text-slate-400">{l.company || '—'}</td>
                      <td className="px-2 py-2 text-slate-400 truncate max-w-[220px]">{l.ai_subject || '—'}</td>
                      <td className="px-2 py-2">
                        <span className={LEAD_STATUS_BADGE[l.status] || 'badge'}>{l.status}</span>
                        {l.failure_reason && <p className="text-[10px] text-red-400/80 mt-0.5 truncate max-w-[220px]">{l.failure_reason}</p>}
                      </td>
                      <td className="px-2 py-2 text-slate-500">{l.sent_at ? new Date(l.sent_at).toLocaleTimeString() : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>

      {/* activity */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/30">
        <button onClick={() => setShowActivity((s) => !s)} className="w-full flex items-center justify-between px-4 py-2.5 text-xs font-semibold text-slate-300">
          <span>Activity log</span>
          <span className="text-slate-500">{showActivity ? '−' : '+'}</span>
        </button>
        {showActivity && (
          <div className="px-4 pb-3">
            {activityQuery.isLoading ? (
              <p className="text-xs text-slate-600 py-2">Loading…</p>
            ) : activityQuery.isError ? (
              <ErrorState message="Could not load activity." onRetry={() => activityQuery.refetch()} />
            ) : (
              <ul className="space-y-1 max-h-72 overflow-y-auto text-xs font-mono">
                {(activityQuery.data?.activity || []).map((a) => (
                  <li key={a.id} className="flex gap-2">
                    <span className="text-slate-600 shrink-0">{a.ts ? new Date(a.ts).toLocaleTimeString() : ''}</span>
                    <span className={`shrink-0 ${ACTIVITY_LEVEL_CLS[a.level] || 'text-slate-400'}`}>{a.event}</span>
                    <span className="text-slate-500 truncate">{a.detail}</span>
                  </li>
                ))}
                {(activityQuery.data?.activity || []).length === 0 && (
                  <li className="text-slate-600">No activity yet.</li>
                )}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
