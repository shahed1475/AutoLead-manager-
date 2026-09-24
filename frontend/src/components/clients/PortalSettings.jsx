import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Copy, ExternalLink, Link2, Mail, MailWarning, Plus, RefreshCw, Send, Server, ShieldCheck } from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { clientsApi, emailSendersApi } from '../../api/client'
import ErrorState from '../ui/ErrorState'

// Clients → Portal: everything about the client link in one place — the link
// itself, the email account sign-in codes and invitations come from, who may
// sign up, what clients see, and request limits.

const MODES = [
  ['open', 'Anyone', 'Anyone with the link can sign up and gets their own workspace.'],
  ['invite', 'Only clients I add', 'People must be on your Clients list first (add them on the Clients tab).'],
  ['closed', 'Closed', 'Nobody can sign in. Clients already signed in keep access.'],
]

function Section({ icon: Icon, title, hint, children }) {
  return (
    <section className="grid gap-4 md:grid-cols-[14rem_1fr] py-7 border-b border-border-subtle last:border-0">
      <div>
        <h2 className="text-section flex items-center gap-2"><Icon size={16} className="text-primary" />{title}</h2>
        {hint && <p className="text-meta mt-1.5 leading-relaxed">{hint}</p>}
      </div>
      <div className="min-w-0 space-y-4">{children}</div>
    </section>
  )
}

function ClientLink({ link }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(link); setCopied(true); setTimeout(() => setCopied(false), 1500) }
    catch { toast.error('Copy failed — select the link and copy it') }
  }
  if (!link) {
    return (
      <p className="text-support">No client link is open. Start HOM with the HOM button (or <code className="text-foreground">./start.sh</code>) and it appears here.</p>
    )
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-lg bg-secondary px-3 py-2 text-sm text-foreground select-all">{link}</code>
        <button className="btn-secondary h-9 text-xs" onClick={copy}>{copied ? <Check size={14} /> : <Copy size={14} />} {copied ? 'Copied' : 'Copy'}</button>
        <a className="btn-ghost h-9 text-xs" href={link} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Open</a>
      </div>
      <p className="text-meta">This link changes when HOM restarts — send clients the new one (or set up your own domain, see docs/SHARING.md).</p>
    </div>
  )
}

function AddGmail({ portalName, senders, onAdded, onCancel }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const add = useMutation({
    mutationFn: async () => {
      const addr = email.trim().toLowerCase()
      const pass = password.replace(/\s+/g, '')
      // Trying again with the same Gmail updates that account instead of adding a copy.
      const existing = senders.find((x) => x.provider === 'smtp' && x.email_address === addr)
      const profile = existing
        ? await emailSendersApi.patch(existing.id, { smtp_password: pass })
        : await emailSendersApi.createSmtp({
          name: `Gmail · ${addr}`, email_address: addr, display_name: portalName, smtp_host: 'smtp.gmail.com',
          smtp_port: 465, smtp_security: 'ssl', smtp_username: addr, smtp_password: pass,
        })
      const test = await emailSendersApi.test(profile.id)
      if (!test.ok) {
        if (!existing) await emailSendersApi.remove(profile.id).catch(() => {})   // don't keep a broken copy
        throw new Error(/5\.7\.8|not accepted|Authentication/i.test(test.message || '')
          ? 'Gmail did not accept that password. Use an App password (16 letters from myaccount.google.com/apppasswords), not your normal Gmail password.'
          : `Gmail refused the sign-in: ${test.message}`)
      }
      return profile
    },
    onSuccess: (profile) => { setPassword(''); toast.success('Gmail connected'); onAdded(profile) },
    onError: (e) => toast.error(e.message, { duration: 8000 }),
  })
  return (
    <form className="rounded-2xl border border-border p-4 space-y-3" autoComplete="off"
      onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
      <p className="text-sm font-semibold">Add a Gmail account</p>
      <ol className="text-meta list-decimal pl-4 space-y-0.5">
        <li>Turn on 2-Step Verification in your Google account.</li>
        <li>Open <a className="text-primary hover:underline" href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">myaccount.google.com/apppasswords</a>, create one named “HOM”.</li>
        <li>Paste the 16-letter password below. It is stored encrypted and never shown again.</li>
      </ol>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="gm-email">Gmail address</label>
          <input id="gm-email" type="email" className="input" placeholder="you@gmail.com" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="gm-pass">App password</label>
          <input id="gm-pass" type="password" className="input" autoComplete="new-password" placeholder="xxxx xxxx xxxx xxxx" required
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="btn-primary h-9 text-sm" disabled={add.isPending || !email.trim() || !password.trim()}>
          {add.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Check size={14} />} Connect and use for the portal
        </button>
        <button type="button" className="btn-ghost h-9 text-sm" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

function SignInEmail({ data }) {
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [to, setTo] = useState('')
  const { settings, sender, senders } = data
  const save = useMutation({
    mutationFn: (value) => clientsApi.saveSettings({ sender: value }),
    onSuccess: (d) => { qc.setQueryData(['portal-settings'], d); qc.invalidateQueries({ queryKey: ['clients-setup'] }) },
    onError: (e) => toast.error(e.message),
  })
  const test = useMutation({
    mutationFn: () => clientsApi.testEmail(to.trim()),
    onSuccess: (r) => toast.success(`Test email sent to ${r.to}`),
    onError: (e) => toast.error(e.message),
  })
  return (
    <>
      <div className={clsx('flex gap-3 rounded-xl p-3.5 text-sm', sender.ready ? 'bg-success/10' : 'bg-warning/10')}>
        {sender.ready ? <Check size={18} className="text-success shrink-0" /> : <MailWarning size={18} className="text-warning shrink-0" />}
        <div className="min-w-0">
          <p className="font-semibold">{sender.ready ? 'Clients can sign in' : "Clients can't sign in yet"}</p>
          <p className="text-muted-foreground break-words">{sender.ready ? <>Codes are sent from <span className="text-foreground">{sender.label}</span>.</> : sender.problem}</p>
        </div>
      </div>

      <div>
        <label className="label" htmlFor="portal-sender">Send sign-in codes and invitations from</label>
        <select id="portal-sender" className="input" value={settings.sender} disabled={save.isPending}
          onChange={(e) => save.mutate(e.target.value)}>
          <option value="auto">Automatic — the first account that works</option>
          <option value="smtp">Main email (Settings → Email)</option>
          {senders.map((s) => (
            <option key={s.id} value={String(s.id)}>{s.name} · {s.email_address}{s.status !== 'connected' ? ` (${s.status})` : ''}</option>
          ))}
        </select>
        <p className="text-meta mt-1.5">These are the same email accounts your Email Campaigns use.{' '}
          <Link to="/settings" className="text-primary hover:underline">Manage all accounts</Link></p>
      </div>

      {adding
        ? <AddGmail portalName={settings.name} senders={senders} onCancel={() => setAdding(false)} onAdded={(p) => { setAdding(false); save.mutate(String(p.id)); qc.invalidateQueries({ queryKey: ['email-senders'] }) }} />
        : <button className="btn-secondary h-9 text-xs" onClick={() => setAdding(true)}><Plus size={14} /> Add a Gmail account</button>}

      {sender.ready && (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); test.mutate() }}>
          <div className="flex-1 min-w-[12rem]">
            <label className="label" htmlFor="test-to">Send a test email to</label>
            <input id="test-to" type="email" className="input" placeholder="you@gmail.com" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          <button className="btn-secondary h-10 text-xs" disabled={test.isPending || !to.trim()}>
            {test.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Send size={14} />} Send test
          </button>
        </form>
      )}
    </>
  )
}

function PortalForm({ settings }) {
  const qc = useQueryClient()
  const [form, setForm] = useState(settings)
  useEffect(() => setForm(settings), [settings])
  const dirty = ['signup_mode', 'name', 'welcome', 'contact_email', 'max_workspaces']
    .some((k) => String(form[k] ?? '') !== String(settings[k] ?? ''))
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))
  const save = useMutation({
    mutationFn: () => clientsApi.saveSettings({
      signup_mode: form.signup_mode, name: form.name, welcome: form.welcome, contact_email: form.contact_email,
      max_workspaces: Number(form.max_workspaces),
    }),
    onSuccess: (d) => { qc.setQueryData(['portal-settings'], d); toast.success('Portal settings saved') },
    onError: (e) => toast.error(e.message),
  })
  return (
    <form onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <Section icon={ShieldCheck} title="Who can sign in" hint="Blocked clients can never sign in, whatever you choose here.">
        <div className="grid gap-2 sm:grid-cols-3" role="radiogroup">
          {MODES.map(([id, label, hint]) => (
            <label key={id} className={clsx('cursor-pointer rounded-2xl border p-3.5 transition-colors',
              form.signup_mode === id ? 'border-primary bg-primary/5' : 'border-border hover:bg-secondary')}>
              <input type="radio" name="signup_mode" value={id} className="sr-only" checked={form.signup_mode === id} onChange={set('signup_mode')} />
              <span className="flex items-center justify-between text-sm font-semibold">{label}
                {form.signup_mode === id && <Check size={14} className="text-primary" />}</span>
              <span className="text-meta mt-1 block leading-relaxed">{hint}</span>
            </label>
          ))}
        </div>
      </Section>

      <Section icon={Mail} title="What clients see" hint="Shown on the sign-in page and in the emails clients get.">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="p-name">Portal name</label>
            <input id="p-name" className="input" maxLength={60} value={form.name} onChange={set('name')} />
          </div>
          <div>
            <label className="label" htmlFor="p-contact">Contact email for clients <span className="font-normal">(optional)</span></label>
            <input id="p-contact" type="email" className="input" placeholder="Replies go here" value={form.contact_email} onChange={set('contact_email')} />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="p-welcome">Welcome message <span className="font-normal">(optional)</span></label>
          <textarea id="p-welcome" rows={2} maxLength={400} className="input" placeholder="e.g. Tell us who you want to reach — we deliver verified leads within 3 days."
            value={form.welcome} onChange={set('welcome')} />
        </div>
      </Section>

      <Section icon={Server} title="Workspaces" hint="Each running workspace uses about 1 GB of memory and shares your AI model and internet.">
        <div className="max-w-xs">
          <label className="label" htmlFor="p-max-ws">Running workspaces at most</label>
          <input id="p-max-ws" type="number" min={0} max={20} className="input tabular" value={form.max_workspaces} onChange={set('max_workspaces')} />
          <p className="text-meta mt-1.5">New sign-ups beyond this wait in line (you can still create one by hand on the Clients tab). 0 = never automatic.</p>
        </div>
      </Section>

      <div className={clsx('flex justify-end pt-2', dirty && 'sticky bottom-[calc(5rem+env(safe-area-inset-bottom))] lg:bottom-4')}>
        <button className="btn-primary h-10 shadow-lg" disabled={!dirty || save.isPending}>
          {save.isPending && <RefreshCw size={14} className="animate-spin" />} {dirty ? 'Save changes' : 'Saved'}
        </button>
      </div>
    </form>
  )
}

export default function PortalSettings() {
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['portal-settings'], queryFn: clientsApi.settings })
  if (isError) return <ErrorState message="Couldn't load the portal settings." onRetry={refetch} />
  if (isLoading) return <div className="h-64 surface-subtle animate-pulse" />
  return (
    <div>
      <Section icon={Link2} title="Client link" hint="Clients sign in here and land in their own private workspace. Your dashboard is never reachable from it.">
        <ClientLink link={data.client_link} />
      </Section>
      <Section icon={Mail} title="Sign-in email" hint="Clients sign in with a 6-digit code sent from this account. Invitations come from it too.">
        <SignInEmail data={data} />
      </Section>
      <PortalForm settings={data.settings} />
    </div>
  )
}
