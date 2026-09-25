import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Ban, Bot, Check, CircleAlert, Info, Link2, MessageCircle, Pause, Play, Plus, QrCode, RefreshCw, Send,
  Settings2, SkipForward, Smartphone, Unlink, Workflow, X, FileSpreadsheet, Upload, Users, Copy,
} from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { whatsappApi } from '../api/client'
import ErrorState from '../components/ui/ErrorState'

// WhatsApp Campaigns — your WhatsApp linked by QR (self-hosted WhatsApp Web
// engine), campaigns sent at a steady pace (n8n triggers every minute, HOM
// sends), replies detected and answered automatically, and a live monitor.

const STATE = {
  WORKING:        ['Connected', 'bg-success/10 text-success'],
  SCAN_QR_CODE:   ['Scan the QR code', 'bg-warning/10 text-warning'],
  STARTING:       ['Starting…', 'bg-info/10 text-info'],
  NOT_STARTED:    ['Not connected', 'bg-secondary text-muted-foreground'],
  STOPPED:        ['Not connected', 'bg-secondary text-muted-foreground'],
  FAILED:         ['Connection failed', 'bg-error/10 text-error'],
  ENGINE_OFFLINE: ['Engine offline', 'bg-error/10 text-error'],
  ENGINE_ERROR:   ['Engine error', 'bg-error/10 text-error'],
  NOT_CONFIGURED: ['Not set up', 'bg-secondary text-muted-foreground'],
  ERROR:          ['Meta API error', 'bg-error/10 text-error'],
}
const ACT = {
  sent: [Send, 'text-primary'], received: [MessageCircle, 'text-info'], auto_reply: [Bot, 'text-success'],
  opt_out: [Ban, 'text-error'], skipped: [SkipForward, 'text-muted-foreground'], error: [CircleAlert, 'text-error'],
  info: [Info, 'text-muted-foreground'],
}
const CAMPAIGN = {
  DRAFT: 'bg-secondary text-muted-foreground', RUNNING: 'bg-success/10 text-success', PAUSED: 'bg-warning/10 text-warning',
  DONE: 'bg-info/10 text-info', CANCELLED: 'bg-secondary text-muted-foreground',
}

function time(iso) {
  if (!iso) return ''
  const raw = String(iso).replace(' ', 'T')
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(raw) ? raw : `${raw}Z`)     // the server stores UTC
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

// Images need the session header, so they're fetched as blobs.
function useLiveImage(which, enabled, everyMs) {
  const [url, setUrl] = useState(null)
  const last = useRef(null)
  useEffect(() => {
    if (!enabled) { setUrl(null); return undefined }
    let alive = true
    const load = async () => {
      try {
        const blob = await whatsappApi.image(which)
        if (!alive) return
        const next = URL.createObjectURL(blob)
        if (last.current) URL.revokeObjectURL(last.current)
        last.current = next
        setUrl(next)
      } catch { /* keep the last frame */ }
    }
    load()
    const t = setInterval(load, everyMs)
    return () => { alive = false; clearInterval(t) }
  }, [which, enabled, everyMs])
  return url
}

function Pill({ state }) {
  const [label, cls] = STATE[state] || [state, 'bg-secondary text-muted-foreground']
  return <span className={clsx('inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold', cls)}>
    <span className="size-1.5 rounded-full bg-current" />{label}</span>
}

// ── Monitor ───────────────────────────────────────────────────────────────

function LivePhone({ status, onSetup }) {
  const qc = useQueryClient()
  const state = status?.whatsapp?.state
  const isMeta = status?.engine === 'meta'
  const [confirm, setConfirm] = useState(false)
  const qr = useLiveImage('qr', state === 'SCAN_QR_CODE', 5000)
  const [open, setOpen] = useState(null)
  const chats = useQuery({ queryKey: ['wa-chats'], queryFn: whatsappApi.chats, refetchInterval: 4000, enabled: state === 'WORKING' })
  const connect = useMutation({ mutationFn: whatsappApi.connect, onSuccess: () => qc.invalidateQueries({ queryKey: ['wa-status'] }), onError: (e) => toast.error(e.message) })
  const unlink = useMutation({ mutationFn: whatsappApi.unlink, onSuccess: () => { setConfirm(false); qc.invalidateQueries({ queryKey: ['wa-status'] }) }, onError: (e) => toast.error(e.message) })

  return (
    <section className="card overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-border-subtle">
        <h2 className="text-sm font-semibold flex items-center gap-2"><Smartphone size={16} className="text-primary" /> Live WhatsApp</h2>
        {state && <Pill state={state} />}
      </div>
      <div className="p-4 sm:p-5">
        {state === 'WORKING' && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-success/5 border border-success/20 px-4 py-3 text-sm">
              <span className="flex items-center gap-2"><Check size={15} className="text-success" />
                {isMeta ? 'Meta Cloud API:' : 'Linked:'} <span className="font-medium">{status.whatsapp.name || 'WhatsApp'}</span>{status.whatsapp.number && <span className="text-muted-foreground">+{status.whatsapp.number}</span>}
                {isMeta && status.whatsapp.quality && <span className="text-meta">· quality {String(status.whatsapp.quality).toLowerCase()}</span>}</span>
              {isMeta ? <button className="btn-ghost h-8 text-xs" onClick={onSetup}><Settings2 size={13} /> Connection</button> : confirm
                ? <span className="flex items-center gap-2"><span className="text-xs">Unlink WhatsApp from HOM?</span>
                    <button className="btn h-8 px-3 text-xs bg-error text-white" disabled={unlink.isPending} onClick={() => unlink.mutate()}>Unlink</button>
                    <button className="btn-ghost h-8 text-xs" onClick={() => setConfirm(false)}>Cancel</button></span>
                : <button className="btn-ghost h-8 text-xs" onClick={() => setConfirm(true)}><Unlink size={13} /> Unlink</button>}
            </div>
            <p className="text-2xs font-semibold uppercase tracking-wider text-muted-foreground mt-5 mb-2">Live chats</p>
            <ul className="divide-y divide-border-subtle rounded-xl border border-border-subtle max-h-[26rem] overflow-y-auto">
              {(chats.data || []).length === 0 && <li className="p-4 text-support">No WhatsApp conversations yet. They appear here the moment a campaign sends or someone writes to you.</li>}
              {(chats.data || []).map((c) => (
                <li key={c.chat_id}>
                  <button type="button" disabled={!c.lead_id} onClick={() => setOpen({ id: c.lead_id, name: c.business_name })}
                    className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-secondary/50 disabled:hover:bg-transparent">
                    <span className="grid place-items-center size-9 rounded-full bg-primary/10 text-primary text-sm font-semibold shrink-0">
                      {(c.business_name || '#').slice(0, 1).toUpperCase()}</span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center justify-between gap-2">
                        <span className="font-medium text-sm truncate">{c.business_name || `+${c.chat_id.split('@')[0]}`}</span>
                        <span className="text-2xs text-muted-foreground tabular shrink-0">{time(c.last_at)}</span>
                      </span>
                      <span className="block text-sm text-muted-foreground truncate">
                        {c.last_direction === 'OUT' ? (c.last_source === 'auto_reply' ? 'You (auto): ' : 'You: ') : ''}{c.last_body}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {open && <Conversation lead={open} onClose={() => setOpen(null)} />}
          </>
        )}
        {isMeta && state === 'NOT_CONFIGURED' && (
          <div className="py-10 text-center space-y-4">
            <MessageCircle size={32} className="mx-auto text-muted-foreground" />
            <p className="text-support max-w-sm mx-auto">Connect your WhatsApp Business number with your own Meta WhatsApp Cloud API keys.</p>
            <button className="btn-primary h-10" onClick={onSetup}><Link2 size={14} /> Set up Meta API</button>
          </div>
        )}
        {isMeta && state === 'ERROR' && (
          <div className="py-8 text-center space-y-3">
            <p className="text-sm text-error max-w-md mx-auto break-words">{status.whatsapp.error || 'Meta rejected the connection.'}</p>
            <button className="btn-secondary h-9" onClick={onSetup}><Settings2 size={14} /> Check the connection</button>
          </div>
        )}
        {state === 'SCAN_QR_CODE' && (
          <div className="grid gap-6 sm:grid-cols-[auto_1fr] items-center">
            <div className="rounded-2xl bg-white p-3 w-fit mx-auto">
              {qr ? <img src={qr} alt="WhatsApp QR code" className="size-56" /> : <div className="size-56 grid place-items-center"><RefreshCw className="animate-spin text-muted-foreground" size={18} /></div>}
            </div>
            <ol className="space-y-2.5 text-sm text-foreground/85 list-decimal pl-5">
              <li>Open <b>WhatsApp</b> on your phone.</li>
              <li>Tap <b>Settings → Linked devices → Link a device</b>.</li>
              <li>Point your phone at this QR code.</li>
              <li className="text-muted-foreground list-none -ml-5 pt-2">Use a business number — WhatsApp doesn’t allow unofficial automation and can block numbers.</li>
            </ol>
          </div>
        )}
        {(state === 'STARTING') && <p className="text-support py-10 text-center"><RefreshCw className="inline animate-spin mr-2" size={14} />Starting WhatsApp…</p>}
        {['NOT_STARTED', 'STOPPED', 'FAILED'].includes(state) && (
          <div className="py-10 text-center space-y-4">
            <QrCode size={32} className="mx-auto text-muted-foreground" />
            <p className="text-support max-w-sm mx-auto">Link your WhatsApp by scanning a QR code with your phone, like WhatsApp Web.</p>
            <button className="btn-primary h-10" disabled={connect.isPending} onClick={() => connect.mutate()}>
              {connect.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Link2 size={14} />} Connect WhatsApp
            </button>
          </div>
        )}
        {['ENGINE_OFFLINE', 'ENGINE_ERROR'].includes(state) && (
          <p className="text-support py-10 text-center max-w-md mx-auto">The WhatsApp engine isn’t running on this computer. Start HOM with the HOM button or <code className="text-foreground">./start.sh</code>.</p>
        )}
      </div>
    </section>
  )
}

function ActivityFeed() {
  const [open, setOpen] = useState(null)
  const { data = [], isLoading } = useQuery({ queryKey: ['wa-activity'], queryFn: whatsappApi.activity, refetchInterval: 4000 })
  return (
    <section className="card overflow-hidden flex flex-col min-h-[24rem]">
      <div className="px-5 py-3.5 border-b border-border-subtle flex items-center justify-between">
        <h2 className="text-sm font-semibold flex items-center gap-2"><Workflow size={16} className="text-primary" /> What’s happening</h2>
        <span className="text-2xs text-muted-foreground flex items-center gap-1.5"><span className="size-1.5 rounded-full bg-success animate-pulse" />Live</span>
      </div>
      <ul className="flex-1 overflow-y-auto max-h-[32rem] divide-y divide-border-subtle">
        {isLoading && <li className="p-5 text-support">Loading…</li>}
        {!isLoading && data.length === 0 && <li className="p-5 text-support">Nothing yet. Sends, replies and automatic answers appear here as they happen.</li>}
        {data.map((a) => {
          const [Icon, color] = ACT[a.kind] || ACT.info
          return (
            <li key={a.id}>
              <button type="button" disabled={!a.lead_id} onClick={() => setOpen({ id: a.lead_id, name: a.business_name })}
                className="w-full text-left px-5 py-3 flex gap-3 hover:bg-secondary/50 disabled:hover:bg-transparent">
                <Icon size={16} className={clsx('mt-0.5 shrink-0', color)} />
                <span className="flex-1 min-w-0 text-sm text-foreground/90 break-words">{a.text}</span>
                <span className="text-2xs text-muted-foreground tabular shrink-0">{time(a.created_at)}</span>
              </button>
            </li>
          )
        })}
      </ul>
      {open && <Conversation lead={open} onClose={() => setOpen(null)} />}
    </section>
  )
}

function Conversation({ lead, onClose }) {
  const { data = [] } = useQuery({ queryKey: ['wa-convo', lead.id], queryFn: () => whatsappApi.conversation(lead.id), refetchInterval: 4000 })
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label={`Conversation with ${lead.name}`}>
      <button className="absolute inset-0 bg-black/40" aria-label="Close" onClick={onClose} />
      <div className="relative w-full max-w-md h-full bg-surface-elevated border-l border-border flex flex-col">
        <div className="flex items-center justify-between px-5 h-14 border-b border-border-subtle">
          <p className="font-semibold truncate">{lead.name || 'Conversation'}</p>
          <button className="btn-ghost h-8 w-8 px-0" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-2 bg-background">
          {data.length === 0 && <p className="text-support">No WhatsApp messages yet.</p>}
          {data.map((m) => (
            <div key={m.id} className={clsx('max-w-[85%] rounded-2xl px-3.5 py-2 text-sm', m.direction === 'OUT'
              ? 'ml-auto bg-primary text-primary-foreground rounded-br-md' : 'bg-surface border border-border-subtle rounded-bl-md')}>
              <p className="whitespace-pre-wrap break-words">{m.body}</p>
              <p className={clsx('text-2xs mt-1', m.direction === 'OUT' ? 'opacity-75' : 'text-muted-foreground')}>
                {time(m.created_at)}{m.source === 'auto_reply' && ' · automatic reply'}{m.source === 'campaign' && ' · campaign'}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// How risky today's sending is for the number (advisory unless Safe mode is on).
function NumberSafety({ p }) {
  const r = p?.safety
  if (!r) return null
  const tone = { low: 'text-success', medium: 'text-warning', high: 'text-error' }[r.level] || 'text-muted-foreground'
  const label = { low: 'Low risk', medium: 'Medium risk', high: 'High risk' }[r.level] || r.level
  return (
    <div className="border-t border-border pt-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold">Number safety: <span className={tone}>{label}</span></p>
        <span className="text-meta">{p.safe_mode ? 'Safe mode on' : 'Safe mode off'}</span>
      </div>
      <ul className="mt-1.5 space-y-1">
        {r.reasons.map((reason) => <li key={reason} className="text-meta">{reason}</li>)}
      </ul>
    </div>
  )
}

function Today({ status, campaigns }) {
  const p = status?.pacing
  const running = campaigns.filter((c) => c.status === 'RUNNING')
  return (
    <section className="card p-5 space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <p className="text-2xs font-semibold uppercase tracking-wider text-muted-foreground">Sent today</p>
          <p className="text-2xl font-semibold tabular mt-1">{p?.sent_today ?? '–'}<span className="text-base text-muted-foreground"> / {p?.daily_limit ?? '–'}</span></p>
        </div>
        <div>
          <p className="text-2xs font-semibold uppercase tracking-wider text-muted-foreground">Sender</p>
          <p className="text-sm mt-2">{p?.state || '–'}</p>
        </div>
      </div>
      <NumberSafety p={p} />
      {running.map((c) => {
        const done = (c.counts.SENT || 0) + (c.counts.REPLIED || 0) + (c.counts.SKIPPED || 0) + (c.counts.FAILED || 0)
        return (
          <div key={c.id}>
            <div className="flex justify-between text-sm"><span className="font-medium truncate">{c.name}</span><span className="tabular text-muted-foreground">{done}/{c.total}</span></div>
            <div className="h-1.5 rounded-full bg-secondary mt-1.5 overflow-hidden"><div className="h-full bg-primary" style={{ width: `${(done / Math.max(1, c.total)) * 100}%` }} /></div>
          </div>
        )
      })}
      {!running.length && <p className="text-meta">No campaign running.</p>}
    </section>
  )
}

// ── Campaigns ─────────────────────────────────────────────────────────────

const PLACEHOLDERS = ['{business_name}', '{first_name}', '{city}', '{niche}']

function readCountryCode() {
  try { return localStorage.getItem('hom-wa-cc') || '+880' } catch { return '+880' }
}

// The manual audience: a contact file (CSV / Excel), checked before anything is sent.
function ContactUpload({ result, onResult, file, onFile, cc, onCc }) {
  const inputRef = useRef(null)
  const check = useMutation({
    mutationFn: () => whatsappApi.uploadContacts(file, cc, false),
    onSuccess: onResult,
    onError: (e) => { onResult(null); toast.error(e.message) },
  })
  useEffect(() => { if (file) check.mutate() }, [file, cc]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1 min-w-[14rem]">
          <input ref={inputRef} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            className="sr-only" id="wc-file" onChange={(e) => { onFile(e.target.files?.[0] || null); e.target.value = '' }} />
          <label htmlFor="wc-file" className="flex items-center gap-3 cursor-pointer rounded-xl border border-dashed border-border hover:border-primary/60 hover:bg-primary/5 px-4 py-3.5">
            <FileSpreadsheet size={20} className="text-primary shrink-0" />
            <span className="min-w-0">
              <span className="block text-sm font-medium truncate">{file ? file.name : 'Choose a contact file'}</span>
              <span className="block text-meta">Excel (.xlsx) or CSV · needs a Phone / Mobile / WhatsApp column; name, company, city are optional</span>
            </span>
          </label>
        </div>
        <div className="w-28">
          <label className="label" htmlFor="wc-cc">Country code</label>
          <input id="wc-cc" className="input tabular" value={cc} onChange={(e) => onCc(e.target.value)} placeholder="+880" />
        </div>
      </div>
      {check.isPending && <p className="text-support"><RefreshCw size={13} className="inline animate-spin mr-1.5" />Checking the file…</p>}
      {result && !check.isPending && (
        <div className="rounded-xl border border-border-subtle p-4 space-y-3">
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
            <span className="font-semibold text-success">{result.ready} ready</span>
            {result.invalid > 0 && <span className="text-warning">{result.invalid} invalid number{result.invalid === 1 ? '' : 's'}</span>}
            {result.duplicates > 0 && <span className="text-muted-foreground">{result.duplicates} duplicate{result.duplicates === 1 ? '' : 's'} merged</span>}
            {result.opted_out > 0 && <span className="text-error">{result.opted_out} opted out (left out)</span>}
            {result.already_talking > 0 && <span className="text-muted-foreground">{result.already_talking} already talking to you (left out)</span>}
          </div>
          <p className="text-meta">Columns found: {Object.entries(result.columns).map(([k, v]) => `${k} → “${v}”`).join(' · ')}</p>
          {result.preview.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[28rem]">
                <thead><tr className="text-left text-meta"><th className="font-medium py-1 pr-3">Phone</th><th className="font-medium py-1 pr-3">Name</th><th className="font-medium py-1 pr-3">Company</th><th className="font-medium py-1">City</th></tr></thead>
                <tbody className="divide-y divide-border-subtle">
                  {result.preview.map((c) => (
                    <tr key={c.phone}><td className="py-1.5 pr-3 tabular">{c.phone}</td><td className="py-1.5 pr-3">{c.name || '—'}</td><td className="py-1.5 pr-3">{c.company || '—'}</td><td className="py-1.5">{c.city || '—'}</td></tr>
                  ))}
                </tbody>
              </table>
              {result.ready > result.preview.length && <p className="text-meta mt-1.5">…and {result.ready - result.preview.length} more</p>}
            </div>
          )}
          {result.invalid_rows.length > 0 && (
            <p className="text-meta">Skipped rows: {result.invalid_rows.map((r) => `row ${r.row} (“${r.value}”)`).join(', ')}{result.invalid > result.invalid_rows.length ? '…' : ''}</p>
          )}
        </div>
      )}
    </div>
  )
}

function NewCampaign({ onClose, engine }) {
  const qc = useQueryClient()
  const [f, setF] = useState({ name: '', source: 'leads', labels: ['HOT', 'WARM'], city: '', niche: '', mode: 'template', template: 'Hi {first_name}, I came across {business_name} in {city} and had a quick idea for you — may I share it?' })
  const [file, setFile] = useState(null)
  const [cc, setCcState] = useState(readCountryCode)
  const [checked, setChecked] = useState(null)
  const [metaTpl, setMetaTpl] = useState(null)
  const onMeta = engine === 'meta'
  const setCc = (v) => { setCcState(v); try { localStorage.setItem('hom-wa-cc', v) } catch { /* private mode */ } }
  const fromFile = f.source === 'file'
  const q = useMemo(() => ({ labels: f.labels.length ? f.labels : null, city: f.city || null, niche: f.niche || null, use_ai_drafts: f.mode === 'drafts' }), [f.labels, f.city, f.niche, f.mode])
  const aud = useQuery({ queryKey: ['wa-audience', q], queryFn: () => whatsappApi.audience(q), enabled: !fromFile })
  const count = fromFile ? (checked?.ready ?? 0) : (aud.data?.count ?? 0)
  const first = fromFile
    ? (checked?.preview?.[0] && { business_name: checked.preview[0].company || checked.preview[0].name || checked.preview[0].phone, city: checked.preview[0].city, niche: checked.preview[0].niche, first_name: (checked.preview[0].name || '').split(' ')[0] })
    : (aud.data?.leads?.[0] && { ...aud.data.leads[0], first_name: '' })
  const mode = fromFile ? 'template' : f.mode
  const preview = first && mode === 'template'
    ? f.template.replace(/\{(\w+)\}/g, (m, k) => ({ business_name: first.business_name, city: first.city || '', niche: first.niche || '', first_name: first.first_name || '' }[k] ?? m)).replace(/[ \t]{2,}/g, ' ').replace(' ,', ',')
    : null
  const create = useMutation({
    mutationFn: async () => {
      const meta = onMeta ? { meta_template: metaTpl } : {}
      if (fromFile) {
        const saved = await whatsappApi.uploadContacts(file, cc, true)
        return whatsappApi.create({ name: f.name, template: f.template, lead_ids: saved.lead_ids, ...meta })
      }
      return whatsappApi.create({ name: f.name, template: mode === 'template' ? f.template : null, ...q, ...meta })
    },
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ['wa-campaigns'] }); toast.success(`“${c.name}” ready for ${c.total} contacts — press Start when you’re ready`); onClose() },
    onError: (e) => toast.error(e.message),
  })
  const toggle = (l) => setF((x) => ({ ...x, labels: x.labels.includes(l) ? x.labels.filter((y) => y !== l) : [...x.labels, l] }))
  return (
    <form className="card p-5 space-y-5" onSubmit={(e) => { e.preventDefault(); create.mutate() }}>
      <div className="flex items-center justify-between"><h2 className="text-section">New WhatsApp campaign</h2>
        <button type="button" className="btn-ghost h-8 w-8 px-0" onClick={onClose} aria-label="Close"><X size={16} /></button></div>
      <div>
        <label className="label" htmlFor="wc-name">Campaign name</label>
        <input id="wc-name" className="input" required placeholder="e.g. Dubai dental clinics" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      </div>
      <fieldset className="space-y-3">
        <legend className="label">Who gets it</legend>
        <div className="grid grid-cols-2 gap-1 p-1 rounded-xl bg-secondary max-w-md">
          {[['leads', 'From your leads', Users], ['file', 'Upload a file', Upload]].map(([id, label, Icon]) => (
            <button key={id} type="button" onClick={() => setF({ ...f, source: id })} aria-pressed={f.source === id}
              className={clsx('h-8 rounded-lg text-xs inline-flex items-center justify-center gap-1.5', f.source === id ? 'bg-surface-elevated font-semibold shadow-sm' : 'text-muted-foreground')}>
              <Icon size={13} />{label}</button>
          ))}
        </div>
        {fromFile ? (
          <ContactUpload file={file} onFile={(fl) => { setFile(fl); setChecked(null) }} cc={cc} onCc={setCc} result={checked} onResult={setChecked} />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {['HOT', 'WARM', 'COLD'].map((l) => (
                <button key={l} type="button" aria-pressed={f.labels.includes(l)} onClick={() => toggle(l)}
                  className={clsx('h-8 px-3 rounded-full border text-xs font-semibold', f.labels.includes(l) ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground')}>{l}</button>
              ))}
              <input className="input h-8 w-36 text-sm" placeholder="City (optional)" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} aria-label="City" />
              <input className="input h-8 w-36 text-sm" placeholder="Niche (optional)" value={f.niche} onChange={(e) => setF({ ...f, niche: e.target.value })} aria-label="Niche" />
            </div>
            <p className="text-meta">{aud.isLoading ? 'Counting…' : `${aud.data?.count ?? 0} leads with a WhatsApp number`} — people who opted out or are already talking to you are left out.</p>
          </>
        )}
      </fieldset>
      {onMeta ? (
        <fieldset className="space-y-3">
          <legend className="label">Message (approved template)</legend>
          <TemplatePicker value={metaTpl} onChange={setMetaTpl} firstLead={first} />
        </fieldset>
      ) : (
      <fieldset className="space-y-3">
          <legend className="label">Message</legend>
          {!fromFile && (
            <div className="grid grid-cols-2 gap-1 p-1 rounded-xl bg-secondary max-w-md">
              {[['template', 'Write one message'], ['drafts', 'Each lead’s approved draft']].map(([id, label]) => (
                <button key={id} type="button" onClick={() => setF({ ...f, mode: id })}
                  className={clsx('h-8 rounded-lg text-xs', f.mode === id ? 'bg-surface-elevated font-semibold shadow-sm' : 'text-muted-foreground')}>{label}</button>
              ))}
            </div>
          )}
          {mode === 'template' ? (
            <>
              <textarea className="input" rows={4} maxLength={1500} value={f.template} onChange={(e) => setF({ ...f, template: e.target.value })} aria-label="Message" />
              <div className="flex flex-wrap gap-1.5">{PLACEHOLDERS.map((p) => (
                <button key={p} type="button" className="h-7 px-2.5 rounded-full border border-border text-xs text-muted-foreground hover:text-foreground"
                  onClick={() => setF({ ...f, template: `${f.template} ${p}`.trim() })}>{p}</button>))}</div>
              {preview && <div className="rounded-xl bg-secondary/60 p-3.5 text-sm"><p className="text-2xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">Preview · {first.business_name}</p>{preview}</div>}
            </>
          ) : <p className="text-support">Sends each lead the WhatsApp message you approved for them in AI Lab. Leads without one are skipped.</p>}
        </fieldset>
      )}
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost h-10" onClick={onClose}>Cancel</button>
        <button className="btn-primary h-10" disabled={create.isPending || !f.name.trim() || !(count > 0) || (fromFile && !file) || (onMeta && !metaTpl)}>
          {create.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Check size={14} />} Create campaign{count ? ` (${count})` : ''}
        </button>
      </div>
    </form>
  )
}

function Campaigns({ campaigns, isError, refetch, engine }) {
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const act = useMutation({ mutationFn: ({ id, a }) => whatsappApi.action(id, a), onSuccess: () => qc.invalidateQueries({ queryKey: ['wa-campaigns'] }), onError: (e) => toast.error(e.message) })
  if (isError) return <ErrorState message="Couldn't load campaigns." onRetry={refetch} />
  return (
    <div className="space-y-4">
      {adding ? <NewCampaign engine={engine} onClose={() => setAdding(false)} /> : <button className="btn-primary h-10" onClick={() => setAdding(true)}><Plus size={15} /> New campaign</button>}
      {campaigns.length === 0 && !adding && <p className="text-support py-6">No campaigns yet. Create one to message leads on WhatsApp at a steady, safe pace.</p>}
      <ul className="grid gap-3">
        {campaigns.map((c) => {
          const sent = (c.counts.SENT || 0) + (c.counts.REPLIED || 0)
          return (
            <li key={c.id} className="card p-4 sm:p-5">
              <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold truncate">{c.name}</p>
                  <p className="text-meta mt-0.5">{c.total} leads · {sent} sent · {c.counts.REPLIED || 0} replied{c.counts.SKIPPED ? ` · ${c.counts.SKIPPED} skipped` : ''}{c.counts.FAILED ? ` · ${c.counts.FAILED} failed` : ''}</p>
                </div>
                <span className={clsx('rounded-full px-2.5 py-1 text-xs font-semibold', CAMPAIGN[c.status])}>{c.status.toLowerCase()}</span>
                {['DRAFT', 'PAUSED'].includes(c.status) && <button className="btn-primary h-8 text-xs" onClick={() => act.mutate({ id: c.id, a: 'start' })}><Play size={13} /> {c.status === 'DRAFT' ? 'Start' : 'Resume'}</button>}
                {c.status === 'RUNNING' && <button className="btn-secondary h-8 text-xs" onClick={() => act.mutate({ id: c.id, a: 'pause' })}><Pause size={13} /> Pause</button>}
                {['DRAFT', 'RUNNING', 'PAUSED'].includes(c.status) && <button className="btn-ghost h-8 text-xs hover:text-error" onClick={() => act.mutate({ id: c.id, a: 'cancel' })}>Cancel</button>}
              </div>
              <div className="h-1.5 rounded-full bg-secondary mt-3 overflow-hidden"><div className="h-full bg-primary" style={{ width: `${(sent / Math.max(1, c.total)) * 100}%` }} /></div>
              {c.template && <p className="text-sm text-muted-foreground mt-3 line-clamp-2">“{c.template}”</p>}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// ── Settings ──────────────────────────────────────────────────────────────

// How HOM connects to WhatsApp: the free WhatsApp Web engine (QR, owner only)
// or the official Meta WhatsApp Cloud API with your own keys.
function ConnectionSettings({ status }) {
  const qc = useQueryClient()
  const owner = status?.edition !== 'client'
  const web = status?.web_available !== false
  const engine = status?.engine
  const metaQ = useQuery({ queryKey: ['wa-meta'], queryFn: whatsappApi.meta })
  const [m, setM] = useState(null)
  useEffect(() => { if (metaQ.data) setM(metaQ.data) }, [metaQ.data])
  const [copied, setCopied] = useState('')
  const choose = useMutation({ mutationFn: (e) => whatsappApi.saveSettings({ wa_engine: e }), onSuccess: () => { qc.invalidateQueries({ queryKey: ['wa-status'] }); toast.success('Connection changed') }, onError: (e) => toast.error(e.message) })
  const save = useMutation({
    mutationFn: async () => { const saved = await whatsappApi.saveMeta(m); const t = await whatsappApi.testMeta(); return { saved, t } },
    onSuccess: ({ saved, t }) => { setM(saved); qc.invalidateQueries({ queryKey: ['wa-status'] }); t.state === 'WORKING' ? toast.success(`Connected: ${t.name || ''} +${t.number || ''}`) : toast.error(t.error || 'Saved — but Meta didn’t accept the keys yet') },
    onError: (e) => toast.error(e.message),
  })
  const copy = async (label, text) => { try { await navigator.clipboard.writeText(text); setCopied(label); setTimeout(() => setCopied(''), 1500) } catch { toast.error('Copy failed') } }
  const webhook = m ? `${window.location.origin}${m.webhook_path}` : ''
  const localOnly = /localhost|127\.0\.0\.1/.test(window.location.hostname)
  const field = (k, label, props = {}) => (
    <div>
      <label className="label" htmlFor={`meta-${k}`}>{label}</label>
      <input id={`meta-${k}`} className="input" value={m?.[k] ?? ''} onChange={(e) => setM({ ...m, [k]: e.target.value })} autoComplete="off" {...props} />
    </div>
  )
  return (
    <section className="card p-5 space-y-5">
      <div>
        <h2 className="text-section">Connection</h2>
        <p className="text-support mt-1">How HOM sends and receives your WhatsApp messages.</p>
      </div>
      <div className={clsx('grid gap-2', web && 'sm:grid-cols-2')} role="radiogroup" aria-label="WhatsApp connection">
        {[web && ['web', 'WhatsApp Web (QR code)', owner ? 'Free. Link your phone by QR code. Runs on this computer.' : 'Free. Link your phone by QR code — your own private WhatsApp connection.'],
          ['meta', 'Meta WhatsApp Cloud API', 'Official. Your own Meta keys. First messages use approved templates.']].filter(Boolean).map(([id, label, hint]) => (
          <button key={id} type="button" role="radio" aria-checked={engine === id} disabled={choose.isPending || !web}
            onClick={() => engine !== id && choose.mutate(id)}
            className={clsx('text-left rounded-xl border p-3.5', engine === id ? 'border-primary bg-primary/5' : 'border-border hover:bg-secondary')}>
            <span className="text-sm font-semibold flex items-center justify-between">{label}{engine === id && <Check size={14} className="text-primary" />}</span>
            <span className="text-meta block mt-1">{hint}</span>
          </button>
        ))}
      </div>
      {engine === 'meta' && m && (
        <form className="space-y-4 border-t border-border-subtle pt-5" onSubmit={(e) => { e.preventDefault(); save.mutate() }} autoComplete="off">
          <ol className="text-meta list-decimal pl-4 space-y-0.5">
            <li>In <a className="text-primary hover:underline" href="https://developers.facebook.com/apps" target="_blank" rel="noreferrer">Meta for Developers</a>, open your app → WhatsApp → API setup.</li>
            <li>Copy the Phone number ID and WhatsApp Business Account ID; create a permanent access token (System user).</li>
            <li>App settings → Basic → App secret. Then add the webhook below and subscribe to <b>messages</b>.</li>
          </ol>
          <div className="grid gap-4 sm:grid-cols-2">
            {field('phone_number_id', 'Phone number ID', { inputMode: 'numeric', placeholder: 'e.g. 1098765432101234' })}
            {field('waba_id', 'WhatsApp Business Account ID', { inputMode: 'numeric' })}
            {field('access_token', 'Access token', { type: 'password', placeholder: 'EAAG…' })}
            {field('app_secret', 'App secret', { type: 'password' })}
            {field('api_version', 'Graph API version', { placeholder: 'v23.0' })}
          </div>
          <div className="rounded-xl bg-secondary/60 p-4 space-y-3">
            <p className="text-sm font-semibold">Webhook for Meta</p>
            {[['Callback URL', webhook], ['Verify token', m.verify_token]].map(([label, value]) => (
              <div key={label}>
                <p className="text-meta">{label}</p>
                <div className="flex items-center gap-2 mt-1">
                  <code className="flex-1 min-w-0 truncate rounded-lg bg-surface px-3 py-2 text-xs select-all">{value}</code>
                  <button type="button" className="btn-secondary h-8 text-xs" onClick={() => copy(label, value)}>{copied === label ? <Check size={13} /> : <Copy size={13} />} Copy</button>
                </div>
              </div>
            ))}
            {localOnly && <p className="text-meta text-warning">You’re on this computer’s address — Meta can’t reach it. Open this page from your dashboard link (or your own domain) and copy the Callback URL from there.</p>}
            <p className="text-meta">Free links change when HOM restarts — update the Callback URL in Meta then, or use your own domain.</p>
          </div>
          <button className="btn-primary h-10" disabled={save.isPending}>{save.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Check size={14} />} Save and test</button>
        </form>
      )}
    </section>
  )
}

function TemplatePicker({ value, onChange, firstLead }) {
  const q = useQuery({ queryKey: ['wa-meta-templates'], queryFn: whatsappApi.metaTemplates, retry: false })
  const tpl = (q.data || []).find((t) => `${t.name}|${t.language}` === `${value?.name}|${value?.language}`)
  const vars = value?.vars || []
  const fields = [['first_name', 'First name'], ['business_name', 'Business name'], ['city', 'City'], ['niche', 'Niche']]
  const sample = (k) => ({ first_name: firstLead?.first_name || 'Sara', business_name: firstLead?.business_name || 'Bright Dental', city: firstLead?.city || 'Dhaka', niche: firstLead?.niche || '' }[k] || '')
  const preview = tpl ? tpl.body.replace(/\{\{(\d+)\}\}/g, (m, n) => sample(vars[Number(n) - 1]) || m) : ''
  if (q.isError) return <p className="text-sm text-error">{q.error.message}</p>
  return (
    <div className="space-y-3">
      <p className="text-meta">Meta only allows an <b>approved template</b> as the first message to someone. Automatic replies afterwards are normal messages.</p>
      <select className="input" value={value ? `${value.name}|${value.language}` : ''} aria-label="Template"
        onChange={(e) => { const t = (q.data || []).find((x) => `${x.name}|${x.language}` === e.target.value); onChange(t ? { name: t.name, language: t.language, body: t.body, vars: Array.from({ length: t.variables }, (_, i) => fields[Math.min(i, 1)][0]) } : null) }}>
        <option value="">{q.isLoading ? 'Loading your approved templates…' : (q.data || []).length ? 'Choose an approved template' : 'No approved templates yet — create one in WhatsApp Manager'}</option>
        {(q.data || []).map((t) => <option key={`${t.name}|${t.language}`} value={`${t.name}|${t.language}`}>{t.name} · {t.language} · {String(t.category || '').toLowerCase()}</option>)}
      </select>
      {tpl && vars.length > 0 && (
        <div className="grid gap-2 sm:grid-cols-2">
          {vars.map((v, i) => (
            <label key={i} className="text-sm flex items-center gap-2">
              <span className="text-muted-foreground w-12 shrink-0">{`{{${i + 1}}}`}</span>
              <select className="input h-9" value={v} onChange={(e) => onChange({ ...value, vars: vars.map((x, j) => (j === i ? e.target.value : x)) })}>
                {fields.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
          ))}
        </div>
      )}
      {tpl && <div className="rounded-xl bg-secondary/60 p-3.5 text-sm"><p className="text-2xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">Preview</p>{preview}</div>}
    </div>
  )
}

function PaceSettings({ settings }) {
  const qc = useQueryClient()
  const [f, setF] = useState(settings)
  useEffect(() => setF(settings), [settings])
  const save = useMutation({ mutationFn: () => whatsappApi.saveSettings(f), onSuccess: () => { toast.success('Saved'); qc.invalidateQueries({ queryKey: ['wa-status'] }) }, onError: (e) => toast.error(e.message) })
  const num = (k, label, hint) => (
    <div>
      <label className="label" htmlFor={k}>{label}</label>
      <input id={k} type="number" className="input tabular" value={f[k] ?? ''} onChange={(e) => setF({ ...f, [k]: e.target.value === '' ? '' : Number(e.target.value) })} />
      {hint && <p className="text-meta mt-1">{hint}</p>}
    </div>
  )
  if (!f) return null
  return (
    <form className="space-y-6 max-w-2xl" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <section className="card p-5 space-y-4">
        <h2 className="text-section">Sending pace</h2>
        <p className="text-support -mt-2">Steady and low volume protects your number. One message goes out per pause, only in sending hours.</p>
        <div className="grid gap-4 sm:grid-cols-2">
          {num('wa_daily_limit', 'Messages per day', 'Start low (20–40) with a new number.')}
          <label className="flex items-start gap-3 cursor-pointer sm:mt-6">
            <input type="checkbox" className="size-4 mt-0.5 accent-[rgb(var(--primary))]" checked={!!f.wa_safe_mode} onChange={(e) => setF({ ...f, wa_safe_mode: e.target.checked })} />
            <span className="text-sm">Safe mode<span className="block text-meta">Keeps a new number to a slow warm-up: 15 a day in week 1, 30 in week 2, 50 until week 4, then up to 100.</span></span>
          </label>
          {num('wa_min_gap', 'Shortest pause (seconds)')}
          {num('wa_max_gap', 'Longest pause (seconds)')}
          {num('wa_hours_start', 'Send from (hour, 0–23)')}
          {num('wa_hours_end', 'Send until (hour, 1–24)')}
        </div>
      </section>
      <section className="card p-5 space-y-4">
        <h2 className="text-section">Automatic replies</h2>
        <label className="flex items-center gap-3 cursor-pointer">
          <input type="checkbox" className="size-4 accent-[rgb(var(--primary))]" checked={!!f.wa_auto_reply} onChange={(e) => setF({ ...f, wa_auto_reply: e.target.checked })} />
          <span className="text-sm">Answer messages automatically with AI</span>
        </label>
        <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Who gets automatic replies">
          {[['everyone', 'Everyone who messages you', 'Every 1:1 chat. New contacts are saved as leads.'],
            ['leads', 'Only leads you messaged', 'Replies to your campaigns only.']].map(([id, label, hint]) => (
            <label key={id} className={clsx('cursor-pointer rounded-xl border p-3.5', f.wa_reply_scope === id ? 'border-primary bg-primary/5' : 'border-border')}>
              <input type="radio" name="wa_reply_scope" className="sr-only" checked={f.wa_reply_scope === id} onChange={() => setF({ ...f, wa_reply_scope: id })} />
              <span className="text-sm font-semibold flex items-center justify-between">{label}{f.wa_reply_scope === id && <Check size={14} className="text-primary" />}</span>
              <span className="text-meta block mt-1">{hint}</span>
            </label>
          ))}
        </div>
        <p className="text-meta">Never in groups, status updates or channels, and never to messages older than 30 minutes. Anyone who asks to stop is marked Do not contact and not answered. Several messages in a row get one reply.</p>
        <div className="grid gap-4 sm:grid-cols-3">
          {num('wa_auto_reply_per_chat', 'Replies per chat per day (loop guard)')}
          {num('wa_reply_delay_min', 'Reply after at least (s)')}
          {num('wa_reply_delay_max', 'Reply after at most (s)')}
        </div>
      </section>
      <button className="btn-primary h-10" disabled={save.isPending}><Check size={14} /> Save settings</button>
    </form>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────

export default function WhatsAppCampaigns() {
  const qc = useQueryClient()
  const [tab, setTab] = useState('monitor')
  const status = useQuery({ queryKey: ['wa-status'], queryFn: whatsappApi.status, refetchInterval: 4000 })
  const camps = useQuery({ queryKey: ['wa-campaigns'], queryFn: whatsappApi.campaigns, refetchInterval: 8000 })
  const toggleAuto = useMutation({
    mutationFn: (on) => whatsappApi.saveSettings({ wa_auto_reply: on }),
    onSuccess: (s) => { qc.invalidateQueries({ queryKey: ['wa-status'] }); toast.success(s.wa_auto_reply ? 'Automatic replies on' : 'Automatic replies off') },
    onError: (e) => toast.error(e.message),
  })
  const s = status.data
  return (
    <div className="px-4 sm:px-8 py-8 max-w-6xl mx-auto space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-page">WhatsApp</h1>
          <p className="text-support mt-1">Campaigns sent at a steady pace from your own WhatsApp, and every message answered automatically.</p>
        </div>
        {s && (
          <div className="flex flex-wrap items-center gap-2">
            <Pill state={s.whatsapp.state} />
            {s.edition !== 'client' && <span className={clsx('inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold', s.automation.n8n ? 'bg-success/10 text-success' : 'bg-error/10 text-error')}>
              <Workflow size={12} /> Automation {s.automation.n8n ? 'running' : 'off'}</span>}
            <button type="button" onClick={() => toggleAuto.mutate(!s.settings.wa_auto_reply)} disabled={toggleAuto.isPending}
              className={clsx('inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold border', s.settings.wa_auto_reply ? 'border-success/30 bg-success/10 text-success' : 'border-border text-muted-foreground')}>
              <Bot size={12} /> Auto-replies {s.settings.wa_auto_reply ? 'on' : 'off'}</button>
          </div>
        )}
      </header>
      {status.isError && <ErrorState message="Couldn't reach WhatsApp." onRetry={status.refetch} />}
      <div className="grid grid-cols-3 gap-1 p-1 rounded-xl bg-secondary max-w-sm" role="tablist">
        {[['monitor', 'Monitor'], ['campaigns', 'Campaigns'], ['settings', 'Settings']].map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={clsx('h-8 rounded-lg text-sm transition-all', tab === id ? 'bg-surface-elevated text-foreground font-semibold shadow-sm' : 'text-muted-foreground hover:text-foreground')}>
            {id === 'settings' && <Settings2 size={13} className="inline mr-1 -mt-0.5" />}{label}</button>
        ))}
      </div>
      {tab === 'monitor' && (
        <div className="grid gap-6 lg:grid-cols-[1.35fr_1fr] items-start">
          <div className="space-y-6"><LivePhone status={s} onSetup={() => setTab('settings')} /><Today status={s} campaigns={camps.data || []} /></div>
          <ActivityFeed />
        </div>
      )}
      {tab === 'campaigns' && <Campaigns engine={s?.engine} campaigns={camps.data || []} isError={camps.isError} refetch={camps.refetch} />}
      {tab === 'settings' && <div className="space-y-6 max-w-2xl"><ConnectionSettings status={s} /><PaceSettings settings={s?.settings} /></div>}
    </div>
  )
}
