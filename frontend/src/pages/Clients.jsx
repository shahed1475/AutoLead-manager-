import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  X, RefreshCw, MailWarning, UserPlus, Pencil, Check, Play, Pause, Plus, Trash2, UploadCloud, ServerOff,
} from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { clientsApi } from '../api/client'
import ErrorState from '../components/ui/ErrorState'
import PortalSettings from '../components/clients/PortalSettings'

// Clients — every client gets their own private HOM workspace (same app,
// without this page), created automatically when they sign up on your client
// link. Here you see and control those workspaces, and publish your latest
// commit to all of them.

const WS = {
  RUNNING:             { label: 'Running',            cls: 'bg-success/10 text-success' },
  STARTING:            { label: 'Starting',           cls: 'bg-info/10 text-info' },
  CREATING:            { label: 'Setting up',         cls: 'bg-info/10 text-info' },
  WAITING_FOR_RELEASE: { label: 'Needs a publish',    cls: 'bg-warning/10 text-warning' },
  WAITLIST:            { label: 'Waiting for a place', cls: 'bg-warning/10 text-warning' },
  STOPPING:            { label: 'Pausing',            cls: 'bg-secondary text-muted-foreground' },
  STOPPED:             { label: 'Paused',             cls: 'bg-secondary text-muted-foreground' },
  OFFLINE:             { label: 'Service off',        cls: 'bg-warning/10 text-warning' },
  ERROR:               { label: 'Problem',            cls: 'bg-error/10 text-error' },
  NONE:                { label: 'Not signed in yet',  cls: 'bg-secondary text-muted-foreground' },
}

function when(iso) {
  if (!iso) return '—'
  const d = new Date(String(iso).replace(' ', 'T') + (String(iso).includes('Z') ? '' : 'Z'))
  return d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

function WorkspacePill({ ws }) {
  const s = WS[ws?.state] || WS.NONE
  return (
    <span title={ws?.error || undefined} className={clsx('inline-flex items-center rounded-full px-2 py-0.5 text-2xs font-semibold whitespace-nowrap', s.cls)}>
      {s.label}{ws?.version && ws.state === 'RUNNING' ? <span className="font-normal opacity-70">&nbsp;· {ws.version === 'older' ? 'older version' : ws.version.slice(0, 7)}</span> : null}
    </span>
  )
}

function AddClient({ onClose }) {
  const qc = useQueryClient()
  const [f, setF] = useState({ email: '', name: '', company: '', invite: true })
  const add = useMutation({
    mutationFn: () => clientsApi.add(f),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['clients'] })
      if (r.invited) toast.success(`Added — invitation sent to ${r.client.email}`)
      else if (f.invite && r.invite_problem) toast(`Added. ${r.invite_problem}`, { icon: 'ℹ️', duration: 6000 })
      else toast.success('Client added')
      onClose()
    },
    onError: (e) => toast.error(e.message),
  })
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }))
  return (
    <form className="rounded-2xl border border-border p-4 space-y-3" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
      <p className="text-sm font-semibold">Add a client</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <div><label className="label" htmlFor="ac-email">Email</label>
          <input id="ac-email" type="email" required autoFocus className="input" placeholder="client@gmail.com" value={f.email} onChange={set('email')} /></div>
        <div><label className="label" htmlFor="ac-name">Name <span className="font-normal">(optional)</span></label>
          <input id="ac-name" className="input" value={f.name} onChange={set('name')} /></div>
        <div><label className="label" htmlFor="ac-company">Company <span className="font-normal">(optional)</span></label>
          <input id="ac-company" className="input" value={f.company} onChange={set('company')} /></div>
      </div>
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <input type="checkbox" className="accent-[rgb(var(--primary))]" checked={f.invite} onChange={(e) => setF((x) => ({ ...x, invite: e.target.checked }))} />
        Email them the client link now
      </label>
      <div className="flex gap-2">
        <button className="btn-primary h-9 text-sm" disabled={add.isPending || !f.email.trim()}>
          {add.isPending ? <RefreshCw size={14} className="animate-spin" /> : <UserPlus size={14} />} Add client
        </button>
        <button type="button" className="btn-ghost h-9 text-sm" onClick={onClose}>Cancel</button>
      </div>
    </form>
  )
}

function EditableName({ c }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [f, setF] = useState({ name: c.name || '', company: c.company || '' })
  const save = useMutation({
    mutationFn: () => clientsApi.update(c.id, f),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['clients'] }); setEditing(false) },
    onError: (e) => toast.error(e.message),
  })
  if (!editing) {
    return (
      <div className="group">
        <p className="font-medium flex items-center gap-1.5">
          {c.name || <span className="text-muted-foreground">No name yet</span>}{c.company ? <span className="text-muted-foreground font-normal"> · {c.company}</span> : ''}
          <button className="opacity-0 group-hover:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100 text-muted-foreground hover:text-foreground"
            aria-label="Edit name and company" onClick={() => { setF({ name: c.name || '', company: c.company || '' }); setEditing(true) }}><Pencil size={12} /></button>
        </p>
        <p className="text-meta">{c.email}{c.sector && <> · {c.sector}</>}{!c.onboarded_at && c.last_login_at && <span className="text-warning"> · setting up</span>}{c.status === 'BLOCKED' && <span className="text-error"> · blocked</span>}</p>
      </div>
    )
  }
  return (
    <form className="flex flex-wrap gap-1.5" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <input className="input h-8 w-32" placeholder="Name" aria-label="Name" value={f.name} onChange={(e) => setF((x) => ({ ...x, name: e.target.value }))} autoFocus />
      <input className="input h-8 w-32" placeholder="Company" aria-label="Company" value={f.company} onChange={(e) => setF((x) => ({ ...x, company: e.target.value }))} />
      <button className="btn-secondary h-8 px-2" aria-label="Save" disabled={save.isPending}><Check size={14} /></button>
      <button type="button" className="btn-ghost h-8 px-2" aria-label="Cancel" onClick={() => setEditing(false)}><X size={14} /></button>
    </form>
  )
}

// Delete needs the client's email typed in — their workspace data is moved to
// workspaces/_deleted/ on this computer (never erased), but they lose access.
function DeleteWorkspace({ c, onDone }) {
  const [typed, setTyped] = useState('')
  const qc = useQueryClient()
  const del = useMutation({
    mutationFn: () => clientsApi.workspace(c.id, 'delete', typed),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['clients'] }); toast.success('Workspace deleted — its data was archived on this computer'); onDone() },
    onError: (e) => toast.error(e.message),
  })
  return (
    <form className="mt-2 flex flex-wrap items-center gap-2 rounded-xl bg-error/5 p-2.5" onSubmit={(e) => { e.preventDefault(); del.mutate() }}>
      <span className="text-xs text-foreground">Type <b className="select-all">{c.email}</b> to delete this workspace:</span>
      <input className="input h-8 w-56" value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Client email" autoFocus />
      <button className="btn h-8 px-3 text-xs bg-error text-white disabled:opacity-50" disabled={typed.trim().toLowerCase() !== c.email || del.isPending}>Delete</button>
      <button type="button" className="btn-ghost h-8 text-xs" onClick={onDone}>Cancel</button>
    </form>
  )
}

function ClientRow({ c }) {
  const qc = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const ws = c.workspace || { state: 'NONE' }
  const act = useMutation({
    mutationFn: (action) => clientsApi.workspace(c.id, action),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['clients'] }),
    onError: (e) => toast.error(e.message),
  })
  const block = useMutation({
    mutationFn: () => clientsApi.setStatus(c.id, c.status === 'BLOCKED' ? 'ACTIVE' : 'BLOCKED'),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['clients'] }); toast.success(r.status === 'BLOCKED' ? 'Client blocked, signed out, workspace paused' : 'Client unblocked') },
    onError: (e) => toast.error(e.message),
  })
  const hasWs = !['NONE', 'WAITLIST'].includes(ws.state)
  const paused = ws.desired === 'STOPPED'
  return (
    <li className="py-3.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-56"><EditableName c={c} /></div>
        <div className="basis-40"><WorkspacePill ws={ws} /></div>
        <div className="text-meta basis-32 tabular">{c.last_login_at ? `Signed in ${when(c.last_login_at)}` : 'Never signed in'}</div>
        <div className="flex items-center gap-1 ml-auto">
          {!hasWs && c.status !== 'BLOCKED' && (
            <button className="btn-secondary h-8 text-xs" disabled={act.isPending} onClick={() => act.mutate('create')}><Plus size={13} /> Create workspace</button>
          )}
          {hasWs && (paused
            ? <button className="btn-secondary h-8 text-xs" disabled={act.isPending || c.status === 'BLOCKED'} onClick={() => act.mutate('start')}><Play size={13} /> Resume</button>
            : <button className="btn-ghost h-8 text-xs" disabled={act.isPending} onClick={() => act.mutate('stop')}><Pause size={13} /> Pause</button>)}
          {hasWs && <button className="btn-ghost h-8 px-2 hover:text-error" aria-label="Delete workspace" title="Delete workspace" onClick={() => setConfirmDelete(true)}><Trash2 size={14} /></button>}
          <button className={c.status === 'BLOCKED' ? 'btn-secondary h-8 text-xs' : 'btn-ghost h-8 text-xs hover:text-error'}
            disabled={block.isPending} onClick={() => block.mutate()}>
            {c.status === 'BLOCKED' ? 'Unblock' : 'Block'}
          </button>
        </div>
      </div>
      {ws.state === 'ERROR' && ws.error && <p className="text-xs text-error mt-1.5 break-words">{ws.error}</p>}
      {confirmDelete && <DeleteWorkspace c={c} onDone={() => setConfirmDelete(false)} />}
    </li>
  )
}

function ClientsTab() {
  const [adding, setAdding] = useState(false)
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['clients'], queryFn: clientsApi.list, refetchInterval: 5000 })
  if (isError) return <ErrorState message="Couldn't load clients." onRetry={refetch} />
  if (isLoading) return <div className="h-24 surface-subtle animate-pulse" />
  const clients = data?.clients || []
  return (
    <div className="space-y-4">
      {data && !data.supervisor_running && (
        <div role="alert" className="flex gap-3 rounded-xl border border-warning/40 bg-warning/10 p-4 text-sm">
          <ServerOff size={18} className="text-warning shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">The workspace service isn't running</p>
            <p className="text-muted-foreground mt-0.5">Client workspaces can't start or open until it is. Start HOM with the HOM button or <code className="text-foreground">./start.sh</code>.</p>
          </div>
        </div>
      )}
      {adding
        ? <AddClient onClose={() => setAdding(false)} />
        : <button className="btn-secondary h-9 text-sm" onClick={() => setAdding(true)}><UserPlus size={14} /> Add client</button>}
      {clients.length === 0
        ? <p className="text-support py-6">No clients yet. Share your client link — each person who signs up gets their own private workspace.</p>
        : <ul className="divide-y divide-border-subtle">{clients.map((c) => <ClientRow key={c.id} c={c} />)}</ul>}
    </div>
  )
}

// Your release flow: build and test in your own dashboard, commit, then publish.
function ReleaseCard() {
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const { data } = useQuery({
    queryKey: ['client-release'], queryFn: clientsApi.release,
    refetchInterval: (q) => (q.state.data?.publish?.state === 'BUILDING' ? 3000 : 20000),
  })
  const publish = useMutation({
    mutationFn: clientsApi.publish,
    onSuccess: () => { setConfirming(false); toast.success('Publishing started'); qc.invalidateQueries({ queryKey: ['client-release'] }) },
    onError: (e) => toast.error(e.message),
  })
  if (!data) return null
  const { release, git, publish: pub } = data
  const building = pub?.state === 'BUILDING'
  const upToDate = release && git && release.sha === git.sha
  return (
    <section className="rounded-2xl border border-border p-4 sm:p-5 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-section flex items-center gap-2"><UploadCloud size={16} className="text-primary" /> Client version</h2>
          <p className="text-support mt-1">
            {release
              ? <>Clients run <b className="text-foreground">{release.short?.slice(0, 7)}</b> — “{release.subject}” · published {when(release.published_at)}</>
              : 'Nothing published yet — client workspaces start after your first publish.'}
          </p>
          {git && (
            <p className="text-meta mt-1">
              Your latest commit: {git.short} — “{git.subject}”
              {upToDate ? ' · already published' : git.commits_not_published ? ` · ${git.commits_not_published} commit${git.commits_not_published === 1 ? '' : 's'} not published yet` : ''}
              {git.uncommitted_changes ? ` · ${git.uncommitted_changes} uncommitted change${git.uncommitted_changes === 1 ? '' : 's'} stay in your dashboard only` : ''}
            </p>
          )}
        </div>
        {!confirming && (
          <button className="btn-primary h-9 text-sm" disabled={building || !data.supervisor_running || upToDate}
            onClick={() => setConfirming(true)}>
            {building ? <RefreshCw size={14} className="animate-spin" /> : <UploadCloud size={14} />}
            {building ? (pub.step || 'Publishing…') : upToDate ? 'Up to date' : 'Publish to clients'}
          </button>
        )}
      </div>
      {confirming && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl bg-primary/5 p-3">
          <p className="text-sm flex-1 min-w-[14rem]">Build your latest commit <b>{git?.short}</b> and move every client workspace to it? Running workspaces restart for about a minute.</p>
          <button className="btn-primary h-9 text-sm" disabled={publish.isPending} onClick={() => publish.mutate()}><Check size={14} /> Publish</button>
          <button className="btn-ghost h-9 text-sm" onClick={() => setConfirming(false)}>Cancel</button>
        </div>
      )}
      {pub?.state === 'FAILED' && <p className="text-xs text-error break-words">Last publish failed: {pub.error}</p>}
      {pub?.state === 'DONE' && !building && <p className="text-xs text-success">Last publish finished {when(pub.finished_at)}.</p>}
    </section>
  )
}

export default function Clients() {
  const [tab, setTab] = useState('clients')
  const { data: setup } = useQuery({ queryKey: ['clients-setup'], queryFn: clientsApi.setup, staleTime: 30_000 })
  return (
    <div className="px-4 sm:px-8 py-8 max-w-5xl mx-auto space-y-8">
      <header>
        <h1 className="text-page">Clients</h1>
        <p className="text-support mt-1">Each client gets their own private copy of HOM. Build and test here, commit, then publish to them.</p>
      </header>
      {setup && !setup.email_ready && tab !== 'portal' && (
        <div role="alert" className="flex gap-3 rounded-xl border border-warning/40 bg-warning/10 p-4 text-sm">
          <MailWarning size={18} className="text-warning shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold text-foreground">Clients can't sign in yet</p>
            <p className="text-muted-foreground mt-0.5">Sign-in codes are emailed from your account.{' '}
              <button className="text-primary font-medium hover:underline" onClick={() => setTab('portal')}>Connect an email account</button> in the Portal tab.</p>
          </div>
        </div>
      )}
      <ReleaseCard />
      <div className="grid grid-cols-2 gap-1 p-1 rounded-xl bg-secondary max-w-xs" role="tablist">
        {[['clients', 'Clients'], ['portal', 'Portal']].map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={clsx('h-8 rounded-lg text-sm transition-all',
              tab === id ? 'bg-surface-elevated text-foreground font-semibold shadow-sm' : 'text-muted-foreground hover:text-foreground')}>
            {label}
          </button>
        ))}
      </div>
      {tab === 'clients' ? <ClientsTab /> : <PortalSettings />}
    </div>
  )
}
