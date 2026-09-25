import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import {
  AtSign, Plus, Loader2, Trash2, Plug, PlugZap, CheckCircle2, XCircle, Star, Send,
  Eye, EyeOff, KeyRound, Save, ShieldCheck,
} from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { emailSendersApi, settingsApi } from '../../api/client'

// Must match the backend redaction placeholder (settings_router._MASKED). When
// this value is submitted back for a secret key the backend leaves the stored
// secret untouched — so an unchanged Client Secret field never clobbers it.
const SECRET_MASK = '••••set••••'

const STATUS_BADGE = {
  connected:    'badge bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
  disconnected: 'badge bg-slate-500/20 text-slate-400 border border-slate-500/30',
  error:        'badge bg-red-500/20 text-red-400 border border-red-500/30',
}

const CONNECT_REASON = {
  denied: 'Google sign-in was cancelled.',
  bad_state: 'The connection link expired — try again.',
  exchange_failed: 'Google rejected the authorization. Check the OAuth client + redirect URI.',
  no_email: 'Could not read the Google account email.',
  no_refresh_token: 'Google did not return a refresh token — remove Gmail access at myaccount.google.com/permissions, then reconnect.',
  not_configured: 'Google OAuth is not configured on the server.',
}

function isDisabled(err) {
  return /not enabled|503/i.test(err?.message || '')
}

const DNS_TONE = { ok: 'text-emerald-400', weak: 'text-amber-400', missing: 'text-red-400', unknown: 'text-slate-500' }
const DNS_WORD = { ok: 'OK', weak: 'Weak', missing: 'Missing', unknown: "Couldn't check" }

// SPF / DKIM / DMARC / MX for the sender's domain: inboxes check these
// before trusting cold email.
function DeliverabilityReport({ r }) {
  return (
    <div className="mt-3 rounded-lg border border-slate-700/60 p-3 space-y-1.5">
      <p className="text-xs font-semibold text-slate-300">Domain check: {r.domain}</p>
      {[['spf', 'SPF'], ['dkim', 'DKIM'], ['dmarc', 'DMARC'], ['mx', 'Mail servers']].map(([k, label]) => (
        <div key={k} className="grid grid-cols-[6.5rem_4.5rem_1fr] gap-2 text-[11px]">
          <span className="text-slate-400">{label}</span>
          <span className={`font-semibold ${DNS_TONE[r[k].status]}`}>{DNS_WORD[r[k].status]}</span>
          <span className="text-slate-500 break-all">{r[k].detail}</span>
        </div>
      ))}
      {r.advice?.length > 0 && (
        <ul className="pt-1 space-y-1">
          {r.advice.map((a) => <li key={a} className="text-[11px] text-amber-300/90">{a}</li>)}
        </ul>
      )}
    </div>
  )
}

function DailyLimit({ s, onSaved }) {
  const [v, setV] = useState(String(s.daily_limit ?? 0))
  const save = useMutation({
    mutationFn: () => emailSendersApi.patch(s.id, { daily_limit: Math.max(0, parseInt(v || '0', 10) || 0) }),
    onSuccess: () => { toast.success('Daily limit saved'); onSaved() },
    onError: (e) => toast.error(e.message),
  })
  const changed = String(s.daily_limit ?? 0) !== v
  return (
    <div className="mt-3 flex flex-wrap items-end gap-2">
      <div>
        <label htmlFor={`dl-${s.id}`} className="block text-[11px] text-slate-400 mb-1">Emails per day (0 = no limit)</label>
        <input id={`dl-${s.id}`} type="number" min="0" max="5000" value={v} onChange={(e) => setV(e.target.value)}
               className="input w-28 text-xs" />
      </div>
      {changed && (
        <button className="btn-secondary text-xs" disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />} Save
        </button>
      )}
      <p className="text-[11px] text-slate-500 basis-full">A new inbox should start around 20 a day. Campaigns pause when the limit is reached and resume the next day.</p>
    </div>
  )
}

function SenderCard({ s, onChanged }) {
  const qc = useQueryClient()
  const refresh = () => { qc.invalidateQueries({ queryKey: ['email-senders'] }); onChanged?.() }

  const testMut = useMutation({
    mutationFn: () => emailSendersApi.test(s.id),
    onSuccess: (r) => { r.ok ? toast.success('Connected') : toast.error(r.message || 'Connection failed'); refresh() },
    onError: (e) => toast.error(e.message),
  })
  const disconnectMut = useMutation({
    mutationFn: () => emailSendersApi.disconnect(s.id),
    onSuccess: () => { toast.success('Disconnected'); refresh() },
    onError: (e) => toast.error(e.message),
  })
  const deleteMut = useMutation({
    mutationFn: () => emailSendersApi.remove(s.id),
    onSuccess: () => { toast.success('Sender removed'); refresh() },
    onError: (e) => toast.error(e.message),
  })
  const dnsMut = useMutation({
    mutationFn: () => emailSendersApi.deliverability(s.id),
    onError: (e) => toast.error(e.message),
  })
  const defaultMut = useMutation({
    mutationFn: () => emailSendersApi.setDefault(s.id),
    onSuccess: () => { toast.success('Default sender set'); refresh() },
    onError: (e) => toast.error(e.message),
  })

  return (
    <div className="rounded-lg border border-slate-700/60 bg-slate-800/40 p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-slate-200 text-sm truncate">{s.name}</span>
            {s.is_default && (
              <span className="text-[10px] text-amber-400 flex items-center gap-0.5"><Star size={10} /> default</span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-0.5">
            {s.provider === 'gmail' ? 'Gmail (OAuth2)' : 'SMTP'} · {s.email_address}
          </p>
          {s.last_error && s.status !== 'connected' && (
            <p className="text-[11px] text-red-400/80 mt-1 truncate">{s.last_error}</p>
          )}
        </div>
        <span className={STATUS_BADGE[s.status] || 'badge'}>{s.status}</span>
      </div>

      <div className="flex flex-wrap gap-1.5 mt-3">
        <button className="btn-secondary text-xs" disabled={testMut.isPending} onClick={() => testMut.mutate()}>
          {testMut.isPending ? <Loader2 size={11} className="animate-spin" /> : <Plug size={11} />} Test
        </button>
        <button className="btn-secondary text-xs" disabled={dnsMut.isPending} onClick={() => dnsMut.mutate()}>
          {dnsMut.isPending ? <Loader2 size={11} className="animate-spin" /> : <ShieldCheck size={11} />} Check domain
        </button>
        {!s.is_default && (
          <button className="btn-secondary text-xs" disabled={defaultMut.isPending} onClick={() => defaultMut.mutate()}>
            <Star size={11} /> Make default
          </button>
        )}
        {s.provider === 'gmail' ? (
          <button className="btn-secondary text-xs" disabled={disconnectMut.isPending} onClick={() => disconnectMut.mutate()}>
            {disconnectMut.isPending ? <Loader2 size={11} className="animate-spin" /> : <PlugZap size={11} />} Disconnect
          </button>
        ) : null}
        <button
          className="btn-danger text-xs"
          disabled={deleteMut.isPending}
          onClick={() => { if (confirm(`Remove sender "${s.name}"? Campaigns using it fall back to "no sender".`)) deleteMut.mutate() }}
        >
          {deleteMut.isPending ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />} Delete
        </button>
      </div>
      <DailyLimit key={s.daily_limit} s={s} onSaved={refresh} />
      {dnsMut.data && <DeliverabilityReport r={dnsMut.data} />}
    </div>
  )
}

function AddSmtpForm({ onDone }) {
  const qc = useQueryClient()
  const [f, setF] = useState({
    name: '', email_address: '', display_name: '', reply_to: '',
    smtp_host: 'smtp.gmail.com', smtp_port: 465, smtp_security: 'ssl',
    smtp_username: '', smtp_password: '',
  })
  const set = (k, v) => setF((p) => ({ ...p, [k]: v }))

  const createMut = useMutation({
    mutationFn: () => emailSendersApi.createSmtp({
      ...f, smtp_port: Number(f.smtp_port) || 465,
      display_name: f.display_name || undefined, reply_to: f.reply_to || undefined,
    }),
    onSuccess: () => { toast.success('SMTP sender added'); qc.invalidateQueries({ queryKey: ['email-senders'] }); onDone() },
    onError: (e) => toast.error(e.message),
  })

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (!f.name || !f.email_address || !f.smtp_host || !f.smtp_username || !f.smtp_password) {
          toast.error('Name, from email, host, username and password are required'); return
        }
        createMut.mutate()
      }}
      className="rounded-lg border border-slate-700/60 bg-slate-800/40 p-4 space-y-3"
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="block"><span className="text-xs text-slate-400">Profile name *</span>
          <input className="input mt-1" value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="PopupGenix SMTP" /></label>
        <label className="block"><span className="text-xs text-slate-400">From email *</span>
          <input className="input mt-1" value={f.email_address} onChange={(e) => set('email_address', e.target.value)} placeholder="hello@popupgenix.com" /></label>
        <label className="block"><span className="text-xs text-slate-400">From name</span>
          <input className="input mt-1" value={f.display_name} onChange={(e) => set('display_name', e.target.value)} placeholder="Fahad | PopupGenix" /></label>
        <label className="block"><span className="text-xs text-slate-400">Reply-To</span>
          <input className="input mt-1" value={f.reply_to} onChange={(e) => set('reply_to', e.target.value)} placeholder="optional" /></label>
        <label className="block"><span className="text-xs text-slate-400">SMTP host *</span>
          <input className="input mt-1" value={f.smtp_host} onChange={(e) => set('smtp_host', e.target.value)} /></label>
        <div className="grid grid-cols-2 gap-2">
          <label className="block"><span className="text-xs text-slate-400">Port</span>
            <input className="input mt-1" value={f.smtp_port} onChange={(e) => set('smtp_port', e.target.value)} /></label>
          <label className="block"><span className="text-xs text-slate-400">Security</span>
            <select className="input mt-1" value={f.smtp_security} onChange={(e) => set('smtp_security', e.target.value)}>
              <option value="ssl">SSL</option><option value="starttls">STARTTLS</option>
            </select></label>
        </div>
        <label className="block"><span className="text-xs text-slate-400">SMTP username *</span>
          <input className="input mt-1" value={f.smtp_username} onChange={(e) => set('smtp_username', e.target.value)} placeholder="hello@popupgenix.com" /></label>
        <label className="block"><span className="text-xs text-slate-400">Password / App Password *</span>
          <input type="password" className="input mt-1" value={f.smtp_password} onChange={(e) => set('smtp_password', e.target.value)} /></label>
      </div>
      <p className="text-[11px] text-slate-500">
        For a Gmail address use <span className="font-mono">smtp.gmail.com</span> with an App Password, and the From email must equal the username.
      </p>
      <div className="flex gap-2">
        <button type="submit" className="btn-primary text-xs" disabled={createMut.isPending}>
          {createMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />} Add sender
        </button>
        <button type="button" className="btn-secondary text-xs" onClick={onDone}>Cancel</button>
      </div>
    </form>
  )
}

function GoogleOAuthConfigCard({ cfg, loading }) {
  const qc = useQueryClient()
  const [f, setF] = useState({ client_id: '', client_secret: '', redirect_uri: '' })
  const [dirty, setDirty] = useState(false)
  const [showSecret, setShowSecret] = useState(false)

  // Load the stored (non-secret) config once it arrives; the secret is only
  // ever represented by the mask — the real value never leaves the server.
  useEffect(() => {
    if (!cfg || dirty) return
    setF({
      client_id: cfg.client_id || '',
      client_secret: cfg.client_secret_set ? SECRET_MASK : '',
      redirect_uri: cfg.redirect_uri || cfg.default_redirect_uri || '',
    })
  }, [cfg, dirty])

  const set = (k, v) => { setF((p) => ({ ...p, [k]: v })); setDirty(true) }

  const saveMut = useMutation({
    mutationFn: () => settingsApi.bulkUpdate({
      google_oauth_client_id: f.client_id.trim(),
      // SECRET_MASK is ignored server-side (unchanged secret) — safe to send as-is
      google_oauth_client_secret: f.client_secret,
      google_oauth_redirect_uri: f.redirect_uri.trim(),
    }),
    onSuccess: () => {
      toast.success('Google OAuth settings saved')
      setDirty(false)
      setShowSecret(false)
      qc.invalidateQueries({ queryKey: ['gmail-config-status'] })
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (e) => toast.error(e.message || 'Could not save Google OAuth settings'),
  })

  const configured = cfg?.configured
  const redirectUri = cfg?.redirect_uri || cfg?.default_redirect_uri || f.redirect_uri

  return (
    <div className="rounded-lg border border-slate-700/60 bg-slate-800/40 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="p-1.5 rounded-md bg-slate-800 text-sky-400"><KeyRound size={14} /></span>
          <div>
            <p className="text-sm font-medium text-slate-200">Google OAuth Configuration</p>
            <p className="text-xs text-slate-500 mt-0.5">
              The Google Cloud OAuth application used when connecting Gmail sender accounts.
              This is <span className="text-slate-400">not</span> a Gmail username/password.
            </p>
          </div>
        </div>
        <span className={clsx(
          'badge shrink-0 flex items-center gap-1 border',
          configured
            ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
            : 'bg-slate-500/20 text-slate-400 border-slate-500/30',
        )}>
          <span className={clsx('w-1.5 h-1.5 rounded-full', configured ? 'bg-emerald-400' : 'bg-slate-500')} />
          {loading ? 'Checking…' : configured ? 'Configured' : 'Not configured'}
        </span>
      </div>

      <label className="block">
        <span className="text-xs text-slate-400">Client ID</span>
        <input
          className="input mt-1 font-mono text-xs"
          value={f.client_id}
          onChange={(e) => set('client_id', e.target.value)}
          placeholder="xxxxxxxx.apps.googleusercontent.com"
          autoComplete="off"
        />
      </label>

      <label className="block">
        <span className="text-xs text-slate-400">Client Secret</span>
        <div className="relative mt-1">
          <input
            type={showSecret ? 'text' : 'password'}
            className="input pr-10 font-mono text-xs"
            value={f.client_secret}
            onChange={(e) => set('client_secret', e.target.value)}
            placeholder="Paste the client secret from Google Cloud"
            autoComplete="off"
          />
          <button
            type="button"
            onClick={() => setShowSecret((v) => !v)}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
            tabIndex={-1}
          >
            {showSecret ? <EyeOff size={13} /> : <Eye size={13} />}
          </button>
        </div>
        <span className="text-[11px] text-slate-500">
          {cfg?.client_secret_set
            ? 'A secret is stored (encrypted). Leave unchanged to keep it, or type a new one to replace it.'
            : 'Stored encrypted on the server — never shown again after saving.'}
        </span>
      </label>

      <label className="block">
        <span className="text-xs text-slate-400">Redirect URI</span>
        <input
          className="input mt-1 font-mono text-xs"
          value={f.redirect_uri}
          onChange={(e) => set('redirect_uri', e.target.value)}
          placeholder={cfg?.default_redirect_uri}
          autoComplete="off"
        />
        <span className="text-[11px] text-slate-500">
          Must be registered exactly in the Google Cloud OAuth client’s Authorized redirect URIs.
        </span>
      </label>

      <div className="flex items-center gap-2">
        <button
          className="btn-primary text-xs"
          disabled={saveMut.isPending || !dirty}
          onClick={() => saveMut.mutate()}
        >
          {saveMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
          Save Google OAuth Settings
        </button>
        {dirty && <span className="text-[11px] text-amber-400">Unsaved changes</span>}
      </div>

      <div className="rounded-md border border-slate-700/50 bg-slate-900/40 p-3 text-[11px] text-slate-400 leading-relaxed">
        <p className="font-medium text-slate-300 mb-1">Google Cloud setup</p>
        <ol className="list-decimal list-inside space-y-0.5">
          <li>Create a <span className="text-slate-300">Web application</span> OAuth client in Google Cloud Console.</li>
          <li>Add this exact redirect URI to <span className="text-slate-300">Authorized redirect URIs</span>:</li>
        </ol>
        <p className="font-mono text-[11px] text-sky-300 break-all my-1.5 pl-4">{redirectUri}</p>
        <ol className="list-decimal list-inside space-y-0.5" start={3}>
          <li>Copy the Client ID and Client Secret into the fields above and save.</li>
          <li>Then click <span className="text-slate-300">Connect Gmail</span> below and pick the Gmail account to send from.</li>
        </ol>
      </div>
    </div>
  )
}

export default function EmailSendersSection() {
  const [adding, setAdding] = useState(false)
  const [params, setParams] = useSearchParams()
  const qc = useQueryClient()

  const listQuery = useQuery({
    queryKey: ['email-senders'],
    queryFn: emailSendersApi.list,
    retry: (n, e) => !isDisabled(e) && n < 1,
  })
  const cfgQuery = useQuery({
    queryKey: ['gmail-config-status'],
    queryFn: emailSendersApi.gmailConfigStatus,
    enabled: !listQuery.isError || !isDisabled(listQuery.error),
    retry: false,
  })

  // handle the OAuth return (?sender=connected | ?sender=error&reason=...)
  useEffect(() => {
    const r = params.get('sender')
    if (!r) return
    if (r === 'connected') toast.success(`Gmail connected${params.get('email') ? ` — ${params.get('email')}` : ''}`)
    else toast.error(CONNECT_REASON[params.get('reason')] || 'Could not connect Gmail')
    qc.invalidateQueries({ queryKey: ['email-senders'] })
    const next = new URLSearchParams(params); next.delete('sender'); next.delete('reason'); next.delete('email')
    setParams(next, { replace: true })
  }, [params, setParams, qc])

  const connectMut = useMutation({
    mutationFn: emailSendersApi.gmailConnect,
    onSuccess: (d) => { if (d?.auth_url) window.location.href = d.auth_url },
    onError: (e) => toast.error(e.message || 'Could not start Gmail connect'),
  })

  if (listQuery.isError && isDisabled(listQuery.error)) {
    return (
      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-700/60 flex items-center gap-3">
          <span className="p-2 rounded-lg bg-slate-800 text-purple-400"><AtSign size={16} /></span>
          <h2 className="font-semibold text-slate-200 text-sm">Email Senders</h2>
        </div>
        <div className="p-5 text-xs text-slate-500">
          Enable Email Campaigns first (open the Email Campaigns page and click “Enable”).
        </div>
      </div>
    )
  }

  const senders = listQuery.data?.senders || []
  const gmailConfigured = cfgQuery.data?.configured

  return (
    <div className="card overflow-hidden">
      <div className="flex items-start justify-between px-5 py-4 border-b border-slate-700/60">
        <div className="flex items-center gap-3">
          <span className="p-2 rounded-lg bg-slate-800 text-purple-400"><AtSign size={16} /></span>
          <div>
            <h2 className="font-semibold text-slate-200 text-sm">Email Senders</h2>
            <p className="text-xs text-slate-500 mt-0.5">The accounts a campaign can send from. Passwords &amp; tokens are encrypted and never shown.</p>
          </div>
        </div>
      </div>

      <div className="p-5 space-y-3">
        {listQuery.isLoading && <p className="text-xs text-slate-500">Loading…</p>}

        {senders.map((s) => <SenderCard key={s.id} s={s} onChanged={() => qc.invalidateQueries({ queryKey: ['email-campaigns'] })} />)}

        {!listQuery.isLoading && senders.length === 0 && (
          <p className="text-xs text-slate-500">No senders yet. Add an SMTP account or connect Gmail.</p>
        )}

        {adding
          ? <AddSmtpForm onDone={() => setAdding(false)} />
          : <button className="btn-secondary text-xs" onClick={() => setAdding(true)}><Plus size={12} /> Add SMTP</button>}

        <div className="pt-2 border-t border-slate-700/50 space-y-3">
          <GoogleOAuthConfigCard cfg={cfgQuery.data} loading={cfgQuery.isLoading} />

          <div>
            <button
              className="btn-secondary text-xs"
              disabled={connectMut.isPending || gmailConfigured === false}
              onClick={() => connectMut.mutate()}
            >
              {connectMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />} Connect Gmail
            </button>
            {gmailConfigured === false && (
              <p className="text-[11px] text-slate-500 mt-1.5">
                Configure the Google OAuth credentials above first, then this button opens the Google consent screen.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
