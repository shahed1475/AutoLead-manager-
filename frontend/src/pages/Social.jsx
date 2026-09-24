import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, Bot, CalendarClock, Check, CircleAlert, ExternalLink, ImagePlus, Info, Link2, LogIn,
  MonitorSmartphone, RefreshCw, Reply, Send, ShieldAlert, Sparkles, Trash2, Undo2, UserPlus, X,
} from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { socialApi } from '../api/client'
import ErrorState from '../components/ui/ErrorState'
import { isClientEdition } from '../lib/edition'
import BrowserWindow from '../components/social/BrowserWindow'
import SocialInbox from '../components/social/SocialInbox'

// Social media automation: Facebook Pages + Instagram (Meta API), LinkedIn and
// X (their API, or — owner's dashboard only — HOM's own browser that you sign
// in to yourself; client workspaces use their own API keys). The AI writes
// posts from your Company DNA; nothing is published until you press Publish
// now or Schedule. HOM's n8n (a workspace: its own loop) publishes due posts
// and runs the inbox.

const PLATFORM = {
  facebook: { label: 'Facebook', cls: 'bg-info/10 text-info', limit: 5000 },
  instagram: { label: 'Instagram', cls: 'bg-error/10 text-error', limit: 2200 },
  linkedin: { label: 'LinkedIn', cls: 'bg-primary/10 text-primary', limit: 3000 },
  x: { label: 'X', cls: 'bg-foreground/10 text-foreground', limit: 280 },
}
const STATUS = {
  DRAFT: 'bg-secondary text-muted-foreground', SCHEDULED: 'bg-info/10 text-info', PUBLISHING: 'bg-warning/10 text-warning',
  PUBLISHED: 'bg-success/10 text-success', PARTIAL: 'bg-warning/10 text-warning', FAILED: 'bg-error/10 text-error',
}
const ACT = {
  published: [Check, 'text-success'], scheduled: [CalendarClock, 'text-info'], failed: [CircleAlert, 'text-error'],
  info: [Info, 'text-muted-foreground'], auto_reply: [Bot, 'text-primary'], reply: [Reply, 'text-primary'],
  lead: [UserPlus, 'text-success'], optout: [ShieldAlert, 'text-error'], error: [CircleAlert, 'text-error'],
}

const utcToLocal = (iso) => (iso ? new Date(`${String(iso).replace(' ', 'T')}Z`) : null)
const fmt = (d) => (d ? d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '')
const mediaSrc = (name) => (name ? `/api/social/media/${name}` : null)

function Tag({ platform }) {
  const p = PLATFORM[platform] || { label: platform, cls: 'bg-secondary' }
  return <span className={clsx('rounded-full px-2 py-0.5 text-2xs font-semibold', p.cls)}>{p.label}</span>
}

// ── Accounts ──────────────────────────────────────────────────────────────

const STATE = {
  connected: ['Connected', 'text-success'], needs_login: ['Needs sign-in', 'text-warning'], error: ['Problem', 'text-error'],
}

function AccountList({ accounts, refetch, onSignIn }) {
  const [confirm, setConfirm] = useState(null)
  const check = useMutation({
    mutationFn: socialApi.check,
    onSuccess: (a) => { refetch(); a.status === 'connected' ? toast.success(`${a.name} works`) : toast.error(a.last_error || 'It didn’t work') },
    onError: (e) => toast.error(e.message),
  })
  const remove = useMutation({ mutationFn: socialApi.remove, onSuccess: () => { setConfirm(null); refetch() } })
  return (
    <section className="card p-5 space-y-4">
      <h2 className="text-section">Connected accounts</h2>
      {accounts.length === 0 && <p className="text-support">No accounts yet — add one on the right.</p>}
      <ul className="divide-y divide-border-subtle">
        {accounts.map((a) => {
          const [label, color] = STATE[a.status] || STATE.error
          return (
            <li key={a.id} className="py-3 flex flex-wrap items-center gap-2">
              <Tag platform={a.platform} />
              <span className="min-w-0 flex-1">
                <span className="block font-medium text-sm truncate">{a.name}</span>
                <span className="text-2xs text-muted-foreground">{a.transport === 'browser' ? 'Browser' : 'API'}{a.target === 'company' ? ' · Company Page' : ''}{a.inbox ? ' · posts + inbox' : ''}</span>
              </span>
              <span className={clsx('text-xs', color)} title={a.last_error || ''}>{label}</span>
              {a.transport === 'browser' && !isClientEdition()
                ? <button className="btn-secondary h-8 text-xs" onClick={() => onSignIn(a)}><LogIn size={12} /> Sign in</button>
                : <button className="btn-ghost h-8 text-xs" disabled={check.isPending} onClick={() => check.mutate(a.id)}>Check</button>}
              {confirm === a.id
                ? <><button className="btn h-8 px-3 text-xs bg-error text-white" onClick={() => remove.mutate(a.id)}>Disconnect</button><button className="btn-ghost h-8 text-xs" onClick={() => setConfirm(null)}>Cancel</button></>
                : <button className="btn-ghost h-8 px-2 hover:text-error" aria-label={`Disconnect ${a.name}`} onClick={() => setConfirm(a.id)}><Trash2 size={14} /></button>}
              {a.status !== 'connected' && a.last_error && <p className="basis-full text-xs text-error break-words">{a.last_error}</p>}
              {a.inbox && a.inbox_error && <p className="basis-full text-xs text-warning break-words">Inbox: {a.inbox_error}</p>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function MetaForm({ done }) {
  const [token, setToken] = useState('')
  const [found, setFound] = useState(null)
  const [pick, setPick] = useState([])
  const find = useMutation({ mutationFn: () => socialApi.discover(token), onSuccess: (f) => { setFound(f); setPick(f.map((x) => `${x.platform}:${x.external_id}`)) }, onError: (e) => toast.error(e.message) })
  const connect = useMutation({
    mutationFn: () => socialApi.connect(token, pick),
    onSuccess: () => { setToken(''); setFound(null); done(); toast.success('Connected') },
    onError: (e) => toast.error(e.message),
  })
  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); found ? connect.mutate() : find.mutate() }} autoComplete="off">
      <ol className="text-meta list-decimal pl-4 space-y-0.5">
        <li>In <a className="text-primary hover:underline" href="https://developers.facebook.com/apps" target="_blank" rel="noreferrer">Meta for Developers</a>, use an app with Facebook Login for Business.</li>
        <li>Create a System user (Business settings) with access to your Page, or a long-lived token, with <b>pages_show_list, pages_manage_posts, pages_read_engagement, instagram_basic, instagram_content_publish</b>.</li>
        <li>For the inbox also add <b>pages_manage_engagement, pages_messaging, instagram_manage_comments, instagram_manage_messages</b>.</li>
        <li>Paste the token — HOM finds your Pages and the Instagram accounts linked to them.</li>
      </ol>
      <div>
        <label className="label" htmlFor="meta-token">Meta access token</label>
        <input id="meta-token" type="password" className="input" placeholder="EAAG…" value={token} onChange={(e) => { setToken(e.target.value); setFound(null) }} />
      </div>
      {found && (
        <fieldset className="space-y-2">
          <legend className="label">Choose accounts to connect</legend>
          {found.length === 0 && <p className="text-support">This token can’t manage any Page.</p>}
          {found.map((f) => {
            const id = `${f.platform}:${f.external_id}`
            return (
              <label key={id} className="flex items-center gap-2.5 text-sm cursor-pointer">
                <input type="checkbox" className="size-4 accent-[rgb(var(--primary))]" checked={pick.includes(id)}
                  onChange={(e) => setPick(e.target.checked ? [...pick, id] : pick.filter((x) => x !== id))} />
                <Tag platform={f.platform} /> {f.name}
              </label>
            )
          })}
        </fieldset>
      )}
      <button className="btn-primary h-10" disabled={!token.trim() || find.isPending || connect.isPending || (found && !pick.length)}>
        {(find.isPending || connect.isPending) && <RefreshCw size={14} className="animate-spin" />} {found ? 'Connect selected' : 'Find my accounts'}
      </button>
    </form>
  )
}

function ApiForm({ platform, done }) {
  const [f, setF] = useState({ token: '', org_id: '', refresh_token: '', client_id: '', client_secret: '' })
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const go = useMutation({
    mutationFn: () => (platform === 'linkedin'
      ? socialApi.connectLinkedin({ token: f.token, org_id: f.org_id || null })
      : socialApi.connectX({ token: f.token, refresh_token: f.refresh_token, client_id: f.client_id, client_secret: f.client_secret })),
    onSuccess: () => { setF({ token: '', org_id: '', refresh_token: '', client_id: '', client_secret: '' }); done(); toast.success('Connected') },
    onError: (e) => toast.error(e.message),
  })
  const field = (k, label, ph, secret = true) => (
    <div>
      <label className="label" htmlFor={`api-${k}`}>{label}</label>
      <input id={`api-${k}`} type={secret ? 'password' : 'text'} className="input" placeholder={ph} value={f[k]} onChange={set(k)} />
    </div>
  )
  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); go.mutate() }} autoComplete="off">
      {platform === 'linkedin' ? (
        <>
          <ol className="text-meta list-decimal pl-4 space-y-0.5">
            <li>Create an app at <a className="text-primary hover:underline" href="https://www.linkedin.com/developers/apps" target="_blank" rel="noreferrer">LinkedIn Developers</a> and add “Sign In with LinkedIn using OpenID Connect” and “Share on LinkedIn”.</li>
            <li>For a Company Page also add the “Community Management API” (LinkedIn must approve it).</li>
            <li>In the app’s token generator, create a token with <b>openid profile w_member_social</b> (+ <b>w_organization_social</b> for a Company Page). Tokens last about 60 days.</li>
          </ol>
          {field('token', 'LinkedIn access token', 'AQV…')}
          {field('org_id', 'Company Page id (optional)', 'The number in linkedin.com/company/<number>/admin — empty = your profile', false)}
        </>
      ) : (
        <>
          <ol className="text-meta list-decimal pl-4 space-y-0.5">
            <li>In the <a className="text-primary hover:underline" href="https://developer.x.com/en/portal/dashboard" target="_blank" rel="noreferrer">X developer portal</a>, set up OAuth 2.0 for your app (posting needs an X API plan that allows writes).</li>
            <li>Create a user token with <b>tweet.read tweet.write users.read media.write offline.access</b>.</li>
            <li>X tokens expire after about 2 hours — add the refresh token and Client ID so HOM renews it by itself.</li>
          </ol>
          {field('token', 'X access token', 'Access token')}
          {field('refresh_token', 'Refresh token (recommended)', 'Refresh token')}
          <div className="grid gap-3 sm:grid-cols-2">
            {field('client_id', 'Client ID', 'Client ID', false)}
            {field('client_secret', 'Client secret (if any)', 'Only for confidential apps')}
          </div>
        </>
      )}
      <button className="btn-primary h-10" disabled={!f.token.trim() || go.isPending}>
        {go.isPending && <RefreshCw size={14} className="animate-spin" />} Connect
      </button>
    </form>
  )
}

function BrowserForm({ platform, onCreated }) {
  const [name, setName] = useState('')
  const [company, setCompany] = useState('')
  const go = useMutation({
    mutationFn: () => socialApi.addBrowser({ platform, name, company_id: company || null }),
    onSuccess: (a) => { setName(''); setCompany(''); onCreated(a) },
    onError: (e) => toast.error(e.message),
  })
  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); go.mutate() }} autoComplete="off">
      <p className="text-meta">No API needed: HOM opens {PLATFORM[platform].label} in its own browser and shows it to you live. You sign in yourself (password, codes); HOM keeps only the sign-in cookies and then posts the way you would. It pauses and asks you whenever the site shows a security check, and posts at most 8 times a day per account.</p>
      <p className="text-meta text-warning flex gap-1.5"><AlertTriangle size={13} className="shrink-0 mt-0.5" />Automated posting may be against {PLATFORM[platform].label}’s rules — use the API when you can.</p>
      <div>
        <label className="label" htmlFor="br-name">Name for this account</label>
        <input id="br-name" className="input" placeholder={platform === 'x' ? '@yourhandle' : 'Your name or company'} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      {platform === 'linkedin' && (
        <div>
          <label className="label" htmlFor="br-company">Company Page id (optional)</label>
          <input id="br-company" className="input" placeholder="The number in linkedin.com/company/<number>/admin — empty = your profile" value={company} onChange={(e) => setCompany(e.target.value)} />
        </div>
      )}
      <button className="btn-primary h-10" disabled={go.isPending}><LogIn size={14} /> Add and sign in</button>
    </form>
  )
}

function Accounts({ accounts, refetch }) {
  const qc = useQueryClient()
  const [platform, setPlatform] = useState('meta')
  const [pick, setHow] = useState('api')
  const [signIn, setSignIn] = useState(null)
  const owner = !isClientEdition()     // browser posting is owner-only; clients use their own API keys
  const how = owner ? pick : 'api'
  const done = () => { qc.invalidateQueries({ queryKey: ['social-accounts'] }); refetch() }
  return (
    <div className="grid gap-6 lg:grid-cols-2 items-start">
      <AccountList accounts={accounts} refetch={refetch} onSignIn={setSignIn} />
      <section className="card p-5 space-y-4">
        <h2 className="text-section flex items-center gap-2"><Link2 size={16} className="text-primary" /> Add an account</h2>
        <div className="grid grid-cols-3 gap-1 p-1 rounded-xl bg-secondary" role="tablist" aria-label="Platform">
          {[['meta', 'Facebook & Instagram'], ['linkedin', 'LinkedIn'], ['x', 'X']].map(([id, label]) => (
            <button key={id} role="tab" aria-selected={platform === id} onClick={() => setPlatform(id)}
              className={clsx('h-8 rounded-lg text-xs sm:text-sm transition-all', platform === id ? 'bg-surface-elevated text-foreground font-semibold shadow-sm' : 'text-muted-foreground hover:text-foreground')}>{label}</button>
          ))}
        </div>
        {platform !== 'meta' && owner && (
          <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="How to connect">
            {[['api', 'Official API', 'Most reliable. Needs a developer app and token.', Link2],
              ['browser', 'Browser (no API)', 'You sign in; HOM posts like you would.', MonitorSmartphone]].map(([id, label, hint, Icon]) => (
              <label key={id} className={clsx('cursor-pointer rounded-xl border p-3', how === id ? 'border-primary bg-primary/5' : 'border-border')}>
                <input type="radio" className="sr-only" name="how" checked={how === id} onChange={() => setHow(id)} />
                <span className="flex items-center gap-2 text-sm font-medium"><Icon size={14} className="text-primary" />{label}</span>
                <span className="block text-meta mt-0.5">{hint}</span>
              </label>
            ))}
          </div>
        )}
        {platform === 'meta' && <MetaForm done={done} />}
        {platform !== 'meta' && how === 'api' && <ApiForm key={platform} platform={platform} done={done} />}
        {platform !== 'meta' && how === 'browser' && <BrowserForm key={platform} platform={platform} onCreated={(a) => { done(); setSignIn(a) }} />}
        <p className="text-meta">Tokens are stored encrypted and never shown again.</p>
      </section>
      {signIn && <BrowserWindow account={signIn} onClose={() => { setSignIn(null); done() }} onDone={done} />}
    </div>
  )
}

// ── Create ────────────────────────────────────────────────────────────────

function Create({ accounts, onDone, publicLink }) {
  const [idea, setIdea] = useState('')
  const [tone, setTone] = useState('')
  const [chosen, setChosen] = useState(() => accounts.filter((a) => a.status === 'connected').map((a) => a.id))
  const [captions, setCaptions] = useState({})
  const [media, setMedia] = useState(null)
  const [preview, setPreview] = useState(null)
  const [when, setWhen] = useState('')
  const platforms = [...new Set(accounts.filter((a) => chosen.includes(a.id)).map((a) => a.platform))]
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])
  const write = useMutation({
    mutationFn: () => socialApi.compose({ idea, platforms, tone: tone || undefined }),
    onSuccess: (c) => setCaptions((x) => ({ ...x, ...c })),
    onError: (e) => toast.error(e.message),
  })
  const upload = useMutation({
    mutationFn: (file) => socialApi.upload(file),
    onSuccess: (name) => setMedia(name),
    onError: (e) => { toast.error(e.message); setPreview(null) },
  })
  const payload = () => ({ text: captions[platforms[0]] || idea, captions, account_ids: chosen, media_name: media })
  const act = useMutation({
    mutationFn: async (how) => {
      const post = await socialApi.create(payload())
      if (how === 'publish') return socialApi.publish(post.id)
      if (how === 'schedule') return socialApi.schedule(post.id, new Date(when).toISOString())
      return post
    },
    onSuccess: (p, how) => {
      toast.success(how === 'publish' ? (p.status === 'PUBLISHED' ? 'Published' : `Finished: ${p.status.toLowerCase()} — see Posts`) : how === 'schedule' ? 'Scheduled' : 'Saved as draft')
      setIdea(''); setCaptions({}); setMedia(null); setPreview(null); setWhen(''); onDone()
    },
    onError: (e) => toast.error(e.message),
  })
  const tooLong = platforms.some((p) => (captions[p] || '').length > PLATFORM[p].limit)
  const needsImage = platforms.includes('instagram') && !media
  const empty = platforms.some((p) => !(captions[p] || '').trim()) && !media
  const disabled = act.isPending || !chosen.length || needsImage || empty || tooLong || upload.isPending

  if (!accounts.length) return <p className="text-support py-8">Connect an account first (Accounts tab).</p>
  return (
    <div className="grid gap-6 lg:grid-cols-[1.3fr_1fr] items-start">
      <section className="card p-5 space-y-5">
        <div>
          <label className="label" htmlFor="so-idea">What’s the post about?</label>
          <textarea id="so-idea" rows={3} className="input" maxLength={1000} placeholder="e.g. We now build e-commerce websites with WhatsApp ordering — invite shop owners to a free consultation"
            value={idea} onChange={(e) => setIdea(e.target.value)} />
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <select className="input h-9 w-auto" value={tone} onChange={(e) => setTone(e.target.value)} aria-label="Tone">
              <option value="">Tone: from your Company DNA</option>
              <option value="professional">Professional</option><option value="friendly">Friendly</option>
              <option value="exciting">Exciting</option><option value="educational">Educational</option>
            </select>
            <button type="button" className="btn-secondary h-9 text-sm" disabled={!idea.trim() || !platforms.length || write.isPending} onClick={() => write.mutate()}>
              {write.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />} {write.isPending ? 'Writing…' : 'Write with AI'}
            </button>
          </div>
        </div>
        {platforms.map((p) => (
          <div key={p}>
            <label className="label flex items-center justify-between" htmlFor={`so-${p}`}><span className="flex items-center gap-2"><Tag platform={p} /> post text</span>
              <span className="tabular font-normal">{(captions[p] || '').length}/{PLATFORM[p].limit}</span></label>
            <textarea id={`so-${p}`} rows={p === 'instagram' ? 7 : 6} className="input leading-6" maxLength={PLATFORM[p].limit}
              value={captions[p] || ''} onChange={(e) => setCaptions({ ...captions, [p]: e.target.value })} placeholder="Write the post, or use Write with AI" />
          </div>
        ))}
      </section>
      <aside className="card p-5 space-y-5">
        <fieldset className="space-y-2">
          <legend className="label">Post to</legend>
          {accounts.map((a) => (
            <label key={a.id} className="flex items-center gap-2.5 text-sm cursor-pointer">
              <input type="checkbox" className="size-4 accent-[rgb(var(--primary))]" checked={chosen.includes(a.id)} disabled={a.status !== 'connected'}
                onChange={(e) => setChosen(e.target.checked ? [...chosen, a.id] : chosen.filter((x) => x !== a.id))} />
              <Tag platform={a.platform} /> <span className="truncate">{a.name}</span>
              {a.transport === 'browser' && <span className="text-2xs text-muted-foreground">browser</span>}
              {a.status === 'needs_login' && <span className="text-2xs text-warning">sign in first</span>}
            </label>
          ))}
        </fieldset>
        <div>
          <p className="label">Image {platforms.includes('instagram') ? <span className="font-normal">(needed for Instagram)</span> : <span className="font-normal">(optional)</span>}</p>
          {preview ? (
            <div className="relative">
              <img src={preview} alt="Post image" className="w-full rounded-xl border border-border-subtle object-cover max-h-72" />
              <button type="button" className="absolute top-2 right-2 btn-secondary h-8 w-8 px-0" aria-label="Remove image" onClick={() => { setMedia(null); setPreview(null) }}><X size={14} /></button>
              {upload.isPending && <p className="text-meta mt-1">Uploading…</p>}
            </div>
          ) : (
            <label className="flex items-center gap-3 cursor-pointer rounded-xl border border-dashed border-border hover:border-primary/60 hover:bg-primary/5 px-4 py-6 justify-center text-sm">
              <ImagePlus size={18} className="text-primary" /> Choose a JPG or PNG (max 8 MB)
              <input type="file" accept="image/jpeg,image/png" className="sr-only" onChange={(e) => { const f = e.target.files?.[0]; if (f) { setPreview(URL.createObjectURL(f)); upload.mutate(f) } e.target.value = '' }} />
            </label>
          )}
          {platforms.includes('instagram') && !publicLink && <p className="text-meta text-warning mt-2 flex gap-1.5"><AlertTriangle size={13} className="shrink-0 mt-0.5" />{isClientEdition()
            ? 'Instagram fetches the image from your HOM link, which isn’t online right now — the post will fail on Instagram until it is.'
            : 'Instagram fetches the image from your dashboard link — keep HOM’s link online (./start.sh) when it publishes.'}</p>}
        </div>
        <div className="space-y-2 border-t border-border-subtle pt-4">
          <button type="button" className="btn-primary w-full h-10" disabled={disabled} onClick={() => act.mutate('publish')}><Send size={14} /> Publish now</button>
          <div className="flex gap-2">
            <input type="datetime-local" className="input h-10 flex-1" value={when} onChange={(e) => setWhen(e.target.value)} aria-label="Schedule time" />
            <button type="button" className="btn-secondary h-10" disabled={disabled || !when || new Date(when) < new Date()} onClick={() => act.mutate('schedule')}><CalendarClock size={14} /> Schedule</button>
          </div>
          <button type="button" className="btn-ghost w-full h-9 text-sm" disabled={act.isPending || !chosen.length} onClick={() => act.mutate('draft')}>Save as draft</button>
          {needsImage && <p className="text-meta">Add an image to post on Instagram.</p>}
        </div>
      </aside>
    </div>
  )
}

// ── Posts ─────────────────────────────────────────────────────────────────

function Posts({ posts, refetch }) {
  const done = () => refetch()
  const act = useMutation({
    mutationFn: ({ id, a }) => (a === 'publish' ? socialApi.publish(id) : a === 'unschedule' ? socialApi.unschedule(id) : socialApi.remove_post(id)),
    onSuccess: done, onError: (e) => toast.error(e.message),
  })
  const { data: activity = [] } = useQuery({ queryKey: ['social-activity'], queryFn: socialApi.activity, refetchInterval: 5000 })
  return (
    <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr] items-start">
      <ul className="space-y-3">
        {posts.length === 0 && <li className="text-support py-6">No posts yet. Create one in the Create tab.</li>}
        {posts.map((p) => {
          const text = Object.values(p.captions || {})[0] || p.text
          return (
            <li key={p.id} className="card p-4 flex gap-4">
              {p.media_name && <img src={mediaSrc(p.media_name)} alt="" className="size-20 rounded-lg object-cover shrink-0 border border-border-subtle" />}
              <div className="min-w-0 flex-1 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={clsx('rounded-full px-2 py-0.5 text-2xs font-semibold', STATUS[p.status])}>{p.status.toLowerCase()}</span>
                  {p.status === 'SCHEDULED' && <span className="text-meta">{fmt(utcToLocal(p.scheduled_at))}</span>}
                  {p.published_at && <span className="text-meta">{fmt(utcToLocal(p.published_at))}</span>}
                  <span className="flex-1" />
                  {['DRAFT', 'SCHEDULED', 'FAILED', 'PARTIAL'].includes(p.status) && <button className="btn-secondary h-7 text-xs" disabled={act.isPending} onClick={() => act.mutate({ id: p.id, a: 'publish' })}><Send size={12} /> {p.status === 'FAILED' || p.status === 'PARTIAL' ? 'Retry' : 'Publish now'}</button>}
                  {p.status === 'SCHEDULED' && <button className="btn-ghost h-7 text-xs" onClick={() => act.mutate({ id: p.id, a: 'unschedule' })}><Undo2 size={12} /> To drafts</button>}
                  {p.status !== 'PUBLISHING' && <button className="btn-ghost h-7 px-2 hover:text-error" aria-label="Delete post" onClick={() => act.mutate({ id: p.id, a: 'delete' })}><Trash2 size={13} /></button>}
                </div>
                <p className="text-sm text-foreground/90 line-clamp-3 whitespace-pre-line">{text}</p>
                <div className="flex flex-wrap gap-2">
                  {p.targets.map((t) => (
                    <span key={t.id} className="inline-flex items-center gap-1.5 text-xs" title={t.error || ''}>
                      <Tag platform={t.platform} />
                      {t.status === 'PUBLISHED' && t.url ? <a href={t.url} target="_blank" rel="noreferrer" className="text-primary hover:underline inline-flex items-center gap-1">View <ExternalLink size={11} /></a>
                        : <span className={t.status === 'FAILED' ? 'text-error' : 'text-muted-foreground'}>{t.status === 'FAILED' ? 'failed' : t.status.toLowerCase()}</span>}
                    </span>
                  ))}
                </div>
                {p.targets.filter((t) => t.error).map((t) => <p key={t.id} className="text-xs text-error break-words">{t.account_name}: {t.error}</p>)}
              </div>
            </li>
          )
        })}
      </ul>
      <section className="card overflow-hidden">
        <div className="px-5 py-3.5 border-b border-border-subtle flex items-center justify-between">
          <h2 className="text-sm font-semibold">What’s happening</h2>
          <span className="text-2xs text-muted-foreground flex items-center gap-1.5"><span className="size-1.5 rounded-full bg-success animate-pulse" />Live</span>
        </div>
        <ul className="divide-y divide-border-subtle max-h-[32rem] overflow-y-auto">
          {activity.length === 0 && <li className="p-5 text-support">Publishing, schedules and errors appear here.</li>}
          {activity.map((a) => {
            const [Icon, color] = ACT[a.kind] || ACT.info
            return <li key={a.id} className="px-5 py-3 flex gap-3 text-sm"><Icon size={15} className={clsx('mt-0.5 shrink-0', color)} /><span className="flex-1 break-words">{a.text}</span><span className="text-2xs text-muted-foreground tabular shrink-0">{fmt(utcToLocal(a.created_at))}</span></li>
          })}
        </ul>
      </section>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────

export default function Social() {
  const [tab, setTab] = useState('create')
  const acc = useQuery({ queryKey: ['social-accounts'], queryFn: socialApi.accounts })
  const posts = useQuery({ queryKey: ['social-posts'], queryFn: socialApi.posts, refetchInterval: 8000 })
  const accounts = acc.data || []
  useEffect(() => { if (acc.data && !acc.data.length) setTab('accounts') }, [acc.data])
  return (
    <div className="px-4 sm:px-8 py-8 max-w-6xl mx-auto space-y-6">
      <header>
        <h1 className="text-page">Social media</h1>
        <p className="text-support mt-1">Write posts with AI from your Company DNA, publish or schedule them on Facebook, Instagram, LinkedIn and X — and answer comments and messages automatically.</p>
      </header>
      {(acc.isError || posts.isError) && <ErrorState message="Couldn't load social media." onRetry={() => { acc.refetch(); posts.refetch() }} />}
      <div className="grid grid-cols-4 gap-1 p-1 rounded-xl bg-secondary max-w-md" role="tablist">
        {[['create', 'Create'], ['posts', 'Posts'], ['inbox', 'Inbox'], ['accounts', `Accounts${accounts.length ? ` (${accounts.length})` : ''}`]].map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={clsx('h-8 rounded-lg text-sm transition-all', tab === id ? 'bg-surface-elevated text-foreground font-semibold shadow-sm' : 'text-muted-foreground hover:text-foreground')}>{label}</button>
        ))}
      </div>
      {tab === 'create' && acc.data && <Create accounts={accounts} publicLink={posts.data?.public_link} onDone={() => { posts.refetch(); setTab('posts') }} />}
      {tab === 'posts' && <Posts posts={posts.data?.posts || []} refetch={posts.refetch} />}
      {tab === 'inbox' && <SocialInbox hasMeta={accounts.some((a) => a.inbox)} />}
      {tab === 'accounts' && <Accounts accounts={accounts} refetch={acc.refetch} />}
    </div>
  )
}
