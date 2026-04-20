import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Wand2, RefreshCw, Send, MessageSquare,
  Mail, Clock, CheckCircle2, Zap, BookOpen, Save,
  Globe, Phone, AtSign, Copy, CheckSquare2, Square,
  AlertTriangle, Bot, Layers, Sparkles, X,
} from 'lucide-react'
import { aiApi, leadsApi, campaignApi, settingsApi } from '../api/client'
import toast from 'react-hot-toast'
import clsx from 'clsx'

// ── Constants ─────────────────────────────────────────────────────────────────

const MSG_TABS = [
  { id: 'whatsapp', label: 'WhatsApp', Icon: MessageSquare, regenType: 'whatsapp' },
  { id: 'email',    label: 'Email',    Icon: Mail,          regenType: 'email'    },
  { id: 'followup', label: 'Follow-Up', Icon: Clock,        regenType: 'followup' },
]

const FILTER_TABS = [
  { id: 'all',   label: 'All'        },
  { id: 'ready', label: 'Ready'      },
  { id: 'needs', label: 'Needs AI'   },
]

const CHANNEL_CLS = {
  EMAIL:    'bg-blue-500/15 text-blue-400 border-blue-500/25',
  WHATSAPP: 'bg-green-500/15 text-green-400 border-green-500/25',
  BOTH:     'bg-teal-500/15 text-teal-400 border-teal-500/25',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function hasAi(lead) {
  return !!(lead.ai_whatsapp_msg || lead.ai_email_subject || lead.ai_email_body || lead.ai_followup_msg)
}

function charLabel(text) {
  if (!text) return '0 ch'
  return `${text.length} ch`
}

function copyToClipboard(text) {
  if (!text) return
  navigator.clipboard.writeText(text).then(
    () => toast.success('Copied to clipboard'),
    () => toast.error('Copy failed'),
  )
}

// ── LeadCard ──────────────────────────────────────────────────────────────────

function LeadCard({ lead, checked, onCheck }) {
  const qc = useQueryClient()

  const [tab, setTab] = useState('whatsapp')
  const [drafts, setDrafts] = useState({
    whatsapp:      lead.ai_whatsapp_msg   ?? '',
    email_subject: lead.ai_email_subject  ?? '',
    email_body:    lead.ai_email_body     ?? '',
    followup:      lead.ai_followup_msg   ?? '',
  })

  const anyDraft = Object.keys(drafts).some(
    (k) => drafts[k] !== (
      k === 'whatsapp'      ? (lead.ai_whatsapp_msg  ?? '') :
      k === 'email_subject' ? (lead.ai_email_subject ?? '') :
      k === 'email_body'    ? (lead.ai_email_body    ?? '') :
                              (lead.ai_followup_msg   ?? '')
    )
  )

  const invalidate = useCallback(
    () => qc.invalidateQueries({ queryKey: ['pending-leads'] }),
    [qc],
  )

  // Sync fresh regen data into local drafts
  function applyRegenResult(data) {
    setDrafts((d) => ({
      whatsapp:      data.ai_whatsapp_msg   !== undefined ? (data.ai_whatsapp_msg   ?? '') : d.whatsapp,
      email_subject: data.ai_email_subject  !== undefined ? (data.ai_email_subject  ?? '') : d.email_subject,
      email_body:    data.ai_email_body     !== undefined ? (data.ai_email_body     ?? '') : d.email_body,
      followup:      data.ai_followup_msg   !== undefined ? (data.ai_followup_msg   ?? '') : d.followup,
    }))
  }

  const regenMut = useMutation({
    mutationFn: (type) => aiApi.generate(lead.id, type),
    onSuccess:  (data) => { applyRegenResult(data); toast.success('Regenerated') },
    onError:    (e)    => toast.error(e.message),
  })

  const skipMut = useMutation({
    mutationFn: () => leadsApi.skip(lead.id),
    onSuccess:  () => { invalidate(); toast.success('Lead skipped') },
    onError:    (e) => toast.error(e.message),
  })

  const sendMut = useMutation({
    mutationFn: async () => {
      // Persist edits to DB before sending so the server sends the latest text
      await leadsApi.update(lead.id, {
        ai_whatsapp_msg:  drafts.whatsapp,
        ai_email_subject: drafts.email_subject,
        ai_email_body:    drafts.email_body,
        ai_followup_msg:  drafts.followup,
      })

      if (tab === 'followup') {
        return campaignApi.sendFollowup(lead.id, lead.channel || 'EMAIL')
      }
      const channel = tab === 'whatsapp' ? 'WHATSAPP' : lead.channel || 'EMAIL'
      return leadsApi.resend(lead.id, channel)
    },
    onSuccess: (result) => {
      if (result?.success === false) {
        toast.error(result.error || 'Send failed')
      } else {
        toast.success(`Sent to ${lead.business_name}`)
        invalidate()
      }
    },
    onError: (e) => toast.error(e.message),
  })

  const isBusy     = regenMut.isPending || sendMut.isPending || skipMut.isPending
  const hasContent = hasAi(lead) || Object.values(drafts).some(Boolean)

  // Current tab char count
  const currentCharCount =
    tab === 'email'    ? charLabel(drafts.email_body) :
    tab === 'followup' ? charLabel(drafts.followup)   :
                         charLabel(drafts.whatsapp)

  // Tab-level content indicator
  function tabHas(tabId) {
    if (tabId === 'whatsapp') return !!drafts.whatsapp
    if (tabId === 'email')    return !!(drafts.email_subject || drafts.email_body)
    return !!drafts.followup
  }

  return (
    <div className={clsx(
      'card overflow-hidden transition-all duration-200 group',
      checked && 'ring-1 ring-brand-500/40 border-brand-500/30',
    )}>
      {/* ── Card header ──────────────────────────────────────────── */}
      <div className="flex items-start gap-3 px-4 py-3 border-b border-slate-700/40 bg-slate-800/30">
        {/* Checkbox */}
        <button
          onClick={onCheck}
          className="mt-0.5 shrink-0 text-slate-600 hover:text-brand-400 transition-colors"
        >
          {checked
            ? <CheckSquare2 size={16} className="text-brand-400" />
            : <Square size={16} />}
        </button>

        <div className="flex-1 min-w-0">
          {/* Name + badges */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-slate-100 text-sm truncate max-w-[200px]">
              {lead.business_name}
            </span>
            {lead.niche && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/60 text-slate-500">
                {lead.niche}
              </span>
            )}
            {lead.city && (
              <span className="text-[10px] text-slate-600">{lead.city}</span>
            )}
            {lead.channel && (
              <span className={clsx('text-[10px] px-1.5 py-0.5 rounded-full border font-medium', CHANNEL_CLS[lead.channel])}>
                {lead.channel}
              </span>
            )}
            {anyDraft && (
              <span className="flex items-center gap-1 text-[10px] text-amber-400/90">
                <span className="w-1 h-1 rounded-full bg-amber-400 inline-block" />
                edited
              </span>
            )}
          </div>

          {/* Contact meta */}
          <div className="flex items-center gap-3 mt-1.5 text-[11px] text-slate-500 flex-wrap">
            {lead.email && (
              <span className="flex items-center gap-1 max-w-[200px] truncate">
                <AtSign size={9} className="shrink-0" />
                {lead.email}
              </span>
            )}
            {lead.phone && (
              <span className="flex items-center gap-1">
                <Phone size={9} />
                {lead.phone}
              </span>
            )}
            {lead.website && (
              <a
                href={lead.website}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1 hover:text-slate-300 transition-colors max-w-[160px] truncate"
              >
                <Globe size={9} className="shrink-0" />
                {lead.website.replace(/^https?:\/\//, '').replace(/\/$/, '')}
              </a>
            )}
          </div>
        </div>

        {/* Skip */}
        <button
          onClick={() => skipMut.mutate()}
          disabled={isBusy}
          title="Skip lead"
          className="p-1.5 rounded-lg text-slate-600 hover:text-red-400 hover:bg-red-500/10
                     disabled:opacity-30 transition-all shrink-0"
        >
          <X size={14} />
        </button>
      </div>

      {/* ── No AI messages yet ───────────────────────────────────── */}
      {!hasContent ? (
        <div className="px-4 py-5 flex items-center justify-between gap-4">
          <p className="text-xs text-slate-500">No AI messages generated yet.</p>
          <button
            onClick={() => regenMut.mutate('all')}
            disabled={regenMut.isPending}
            className="btn-secondary text-xs shrink-0"
          >
            {regenMut.isPending
              ? <><RefreshCw size={11} className="animate-spin" /> Generating…</>
              : <><Wand2 size={11} /> Generate All</>}
          </button>
        </div>
      ) : (
        <>
          {/* ── Message tabs ─────────────────────────────────────── */}
          <div className="flex border-b border-slate-700/40 bg-slate-900/30">
            {MSG_TABS.map(({ id, label, Icon }) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={clsx(
                  'flex items-center gap-1.5 px-3 py-2 text-[11px] font-medium border-b-2 transition-all relative',
                  tab === id
                    ? 'border-brand-500 text-brand-400'
                    : 'border-transparent text-slate-500 hover:text-slate-300',
                )}
              >
                <Icon size={11} />
                {label}
                {tabHas(id) && tab !== id && (
                  <span className="absolute top-1.5 right-1 w-1 h-1 rounded-full bg-emerald-500" />
                )}
              </button>
            ))}

            {/* Char count */}
            <div className="ml-auto flex items-center pr-3 gap-2">
              <span className="text-[10px] text-slate-700 font-mono tabular-nums">
                {currentCharCount}
              </span>
              {/* Copy current tab content */}
              <button
                onClick={() => {
                  const text =
                    tab === 'email'
                      ? `${drafts.email_subject}\n\n${drafts.email_body}`
                      : tab === 'followup' ? drafts.followup : drafts.whatsapp
                  copyToClipboard(text)
                }}
                title="Copy message"
                className="text-slate-700 hover:text-slate-400 transition-colors"
              >
                <Copy size={11} />
              </button>
            </div>
          </div>

          {/* ── Tab content ──────────────────────────────────────── */}
          <div className="p-4">
            {tab === 'whatsapp' && (
              <textarea
                value={drafts.whatsapp}
                onChange={(e) => setDrafts((d) => ({ ...d, whatsapp: e.target.value }))}
                placeholder="WhatsApp message — click Regenerate to create one."
                rows={5}
                className="input w-full resize-y text-[12px] leading-relaxed font-mono"
              />
            )}

            {tab === 'email' && (
              <div className="space-y-2.5">
                <div>
                  <label className="label text-[10px]">Subject Line</label>
                  <input
                    value={drafts.email_subject}
                    onChange={(e) => setDrafts((d) => ({ ...d, email_subject: e.target.value }))}
                    placeholder="Email subject line…"
                    className="input w-full text-xs"
                  />
                  <p className="text-[10px] text-slate-700 mt-1 font-mono text-right">
                    {charLabel(drafts.email_subject)}
                  </p>
                </div>
                <div>
                  <label className="label text-[10px]">Email Body</label>
                  <textarea
                    value={drafts.email_body}
                    onChange={(e) => setDrafts((d) => ({ ...d, email_body: e.target.value }))}
                    placeholder="Email body — click Regenerate to create one."
                    rows={6}
                    className="input w-full resize-y text-[12px] leading-relaxed font-mono"
                  />
                </div>
              </div>
            )}

            {tab === 'followup' && (
              <textarea
                value={drafts.followup}
                onChange={(e) => setDrafts((d) => ({ ...d, followup: e.target.value }))}
                placeholder="Follow-up message — click Regenerate to create one."
                rows={4}
                className="input w-full resize-y text-[12px] leading-relaxed font-mono"
              />
            )}
          </div>

          {/* ── Action bar ───────────────────────────────────────── */}
          <div className="flex items-center gap-2 px-4 py-2.5 border-t border-slate-700/40 bg-slate-900/20">
            <button
              onClick={() => regenMut.mutate(MSG_TABS.find((t) => t.id === tab)?.regenType || 'all')}
              disabled={isBusy}
              className="btn-secondary text-xs py-1.5 px-3"
            >
              {regenMut.isPending
                ? <><RefreshCw size={11} className="animate-spin" /> Generating…</>
                : <><RefreshCw size={11} /> Regen {MSG_TABS.find((t) => t.id === tab)?.label}</>}
            </button>

            <button
              onClick={() => regenMut.mutate('all')}
              disabled={isBusy}
              title="Regenerate all message types"
              className="text-[11px] text-slate-600 hover:text-slate-400 px-2 py-1.5 rounded-lg
                         border border-transparent hover:border-slate-700/60 transition-all"
            >
              All types
            </button>

            <button
              onClick={() => sendMut.mutate()}
              disabled={isBusy || !tabHas(tab)}
              className="ml-auto btn-primary text-xs py-1.5 px-4"
            >
              {sendMut.isPending
                ? <><RefreshCw size={11} className="animate-spin" /> Sending…</>
                : tab === 'followup'
                ? <><Send size={11} /> Send Follow-Up</>
                : tab === 'email'
                ? <><Send size={11} /> Send Email</>
                : <><Send size={11} /> Send WhatsApp</>}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// ── Test AI Panel ─────────────────────────────────────────────────────────────

const TEST_RESULT_TABS = [
  { id: 'whatsapp', label: 'WhatsApp' },
  { id: 'email',    label: 'Email'    },
  { id: 'followup', label: 'Follow-Up'},
]

function TestAIPanel({ ollamaConnected }) {
  const [input,     setInput]     = useState('')
  const [results,   setResults]   = useState(null)
  const [resultTab, setResultTab] = useState('whatsapp')

  const testMut = useMutation({
    mutationFn: () => aiApi.testBusiness(input),
    onSuccess:  (data) => { setResults(data); setResultTab('whatsapp') },
    onError:    (e)    => toast.error(e.message),
  })

  const resultText =
    results
      ? resultTab === 'email'
        ? `Subject: ${results.ai_email_subject || ''}\n\n${results.ai_email_body || ''}`
        : resultTab === 'followup'
        ? results.ai_followup_msg || ''
        : results.ai_whatsapp_msg || ''
      : ''

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-slate-700/40">
        <div className="w-6 h-6 rounded-md bg-violet-500/20 border border-violet-500/30 flex items-center justify-center shrink-0">
          <Sparkles size={12} className="text-violet-400" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-slate-200">Test AI Generator</h3>
          <p className="text-[10px] text-slate-500">Preview messages without a lead record</p>
        </div>
      </div>

      <div className="p-4 space-y-3">
        {/* Ollama warning */}
        {!ollamaConnected && (
          <div className="flex items-start gap-2 text-[11px] text-amber-400 bg-amber-500/8 border border-amber-500/20 rounded-lg px-3 py-2.5">
            <AlertTriangle size={12} className="shrink-0 mt-0.5" />
            <span>
              Ollama offline — start it:{' '}
              <code className="font-mono bg-slate-800/80 px-1 py-0.5 rounded text-[10px]">
                ollama serve
              </code>
            </span>
          </div>
        )}

        {/* Input */}
        <div>
          <label className="label text-[10px]">Business Description</label>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              'e.g. Dubai Dental Center — a modern dental clinic in Jumeirah. ' +
              'They offer teeth whitening, braces, and family dentistry. ' +
              'Website: dubaidental.ae'
            }
            rows={4}
            className="input w-full resize-y text-xs"
          />
        </div>

        <button
          onClick={() => testMut.mutate()}
          disabled={!input.trim() || testMut.isPending || !ollamaConnected}
          className="btn-primary w-full justify-center text-xs py-2"
        >
          {testMut.isPending
            ? <><RefreshCw size={12} className="animate-spin" /> Generating all 4 messages…</>
            : <><Zap size={12} /> Generate Sample</>}
        </button>

        {/* Results */}
        {results && (
          <div className="border border-slate-700/40 rounded-xl overflow-hidden">
            {/* Result tabs */}
            <div className="flex border-b border-slate-700/40 bg-slate-900/50">
              {TEST_RESULT_TABS.map(({ id, label }) => (
                <button
                  key={id}
                  onClick={() => setResultTab(id)}
                  className={clsx(
                    'flex-1 py-2 text-[10px] font-medium transition-all',
                    resultTab === id
                      ? 'text-violet-400 bg-violet-500/5'
                      : 'text-slate-500 hover:text-slate-300',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Result body */}
            <div className="relative p-3 bg-slate-900/30">
              {resultTab === 'email' ? (
                <div className="space-y-2">
                  <div>
                    <p className="text-[9px] text-slate-600 uppercase tracking-widest mb-1">Subject</p>
                    <p className="text-[11px] text-slate-200 font-medium">
                      {results.ai_email_subject || <em className="text-slate-600 not-italic">—</em>}
                    </p>
                  </div>
                  <div>
                    <p className="text-[9px] text-slate-600 uppercase tracking-widest mb-1">Body</p>
                    <p className="text-[11px] text-slate-400 leading-relaxed whitespace-pre-wrap font-mono max-h-40 overflow-y-auto">
                      {results.ai_email_body || <em className="text-slate-600 not-italic">—</em>}
                    </p>
                  </div>
                </div>
              ) : (
                <p className="text-[11px] text-slate-400 leading-relaxed whitespace-pre-wrap font-mono max-h-40 overflow-y-auto">
                  {resultText || <em className="text-slate-600 not-italic">—</em>}
                </p>
              )}

              {/* Copy button */}
              <button
                onClick={() => copyToClipboard(
                  resultTab === 'email'
                    ? `Subject: ${results.ai_email_subject}\n\n${results.ai_email_body}`
                    : resultText
                )}
                title="Copy to clipboard"
                className="absolute top-2 right-2 p-1.5 rounded-lg text-slate-600
                           hover:text-slate-300 hover:bg-slate-700/60 transition-all"
              >
                <Copy size={11} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Company DNA Editor ────────────────────────────────────────────────────────

function DNAEditor() {
  const [content, setContent] = useState('')
  const [isDirty, setIsDirty] = useState(false)
  const [savedAt, setSavedAt] = useState(null)

  const { data, isLoading } = useQuery({
    queryKey: ['company-dna'],
    queryFn:  settingsApi.getDna,
  })

  useEffect(() => {
    if (data?.content !== undefined && !isDirty) {
      setContent(data.content)
    }
  }, [data])  // eslint-disable-line react-hooks/exhaustive-deps

  const saveMut = useMutation({
    mutationFn: () => settingsApi.saveDna(content),
    onSuccess: () => {
      setIsDirty(false)
      setSavedAt(new Date())
      toast.success('Company DNA saved — AI will use this for all new messages')
    },
    onError: (e) => toast.error(e.message),
  })

  function timeAgo(dt) {
    if (!dt) return null
    const s = Math.round((Date.now() - dt.getTime()) / 1000)
    if (s < 60)   return 'just now'
    if (s < 3600) return `${Math.floor(s / 60)}m ago`
    return `${Math.floor(s / 3600)}h ago`
  }

  const wordCount  = content.trim() ? content.trim().split(/\s+/).length : 0
  const charCount  = content.length

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-slate-700/40">
        <div className="w-6 h-6 rounded-md bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center shrink-0">
          <BookOpen size={12} className="text-emerald-400" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-slate-200">Company DNA</h3>
          <p className="text-[10px] text-slate-500">Injected into every AI prompt as brand context</p>
        </div>
        {isDirty && (
          <span className="text-[10px] text-amber-400 shrink-0">unsaved</span>
        )}
        {!isDirty && savedAt && (
          <span className="text-[10px] text-emerald-600 flex items-center gap-1 shrink-0">
            <CheckCircle2 size={9} />
            {timeAgo(savedAt)}
          </span>
        )}
      </div>

      <div className="p-4 space-y-3">
        {isLoading ? (
          <div className="flex items-center justify-center h-24 text-slate-600 text-xs gap-2">
            <RefreshCw size={12} className="animate-spin" /> Loading…
          </div>
        ) : (
          <textarea
            value={content}
            onChange={(e) => { setContent(e.target.value); setIsDirty(true) }}
            rows={9}
            placeholder={
              'Describe your company, services, tone, and unique value proposition.\n\n' +
              'The AI reads this before writing every outreach message.'
            }
            className="input w-full resize-y text-[11px] leading-relaxed font-mono min-h-[160px]"
          />
        )}

        <div className="flex items-center justify-between">
          <span className="text-[10px] text-slate-700 font-mono tabular-nums">
            {wordCount.toLocaleString()} words · {charCount.toLocaleString()} ch
          </span>
          <button
            onClick={() => saveMut.mutate()}
            disabled={!isDirty || saveMut.isPending}
            className="btn-primary text-xs py-1.5 px-4"
          >
            {saveMut.isPending
              ? <><RefreshCw size={11} className="animate-spin" /> Saving…</>
              : <><Save size={11} /> Save DNA</>}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main AILab ────────────────────────────────────────────────────────────────

export default function AILab() {
  const qc = useQueryClient()

  const [filter,      setFilter]      = useState('all')
  const [selected,    setSelected]    = useState(new Set())
  const [bulkSending, setBulkSending] = useState(false)

  // ── Queries ───────────────────────────────────────────────────────────────

  const { data: ollamaStatus } = useQuery({
    queryKey: ['ollama-status'],
    queryFn:  aiApi.status,
    refetchInterval: 15_000,
    retry: false,
  })

  const { data: leadsData, isLoading, refetch } = useQuery({
    queryKey: ['pending-leads'],
    queryFn:  () => leadsApi.list({ page_size: 100, status: 'PENDING', sort_by: 'created_at', sort_dir: 'desc' }),
    refetchInterval: 20_000,
  })

  const allLeads   = leadsData?.items ?? []
  const readyLeads = allLeads.filter(hasAi)
  const needsLeads = allLeads.filter((l) => !hasAi(l))
  const ollamaOk   = Boolean(ollamaStatus?.connected)

  // Client-side filter
  const visibleLeads =
    filter === 'ready' ? readyLeads :
    filter === 'needs' ? needsLeads :
    allLeads

  const filterCounts = { all: allLeads.length, ready: readyLeads.length, needs: needsLeads.length }

  // ── Selection helpers ─────────────────────────────────────────────────────

  function toggleSelect(id) {
    setSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }

  function selectAll()  { setSelected(new Set(visibleLeads.map((l) => l.id))) }
  function selectNone() { setSelected(new Set()) }

  // ── Bulk Generate (only leads without messages) ───────────────────────────

  const bulkGenMut = useMutation({
    mutationFn: () => {
      const targets = needsLeads.map((l) => l.id)
      if (!targets.length) throw new Error('All pending leads already have AI messages.')
      return aiApi.generateBulk(targets)
    },
    onSuccess: (data) => {
      toast.success(`Generating AI for ${data.queued} leads in background — queue refreshes automatically`)
      setTimeout(() => refetch(), 5000)
    },
    onError: (e) => toast.error(e.message),
  })

  // ── Bulk Approve — group by channel so each lead uses its own channel ─────

  async function handleBulkApprove() {
    const targets =
      selected.size > 0
        ? readyLeads.filter((l) => selected.has(l.id))
        : readyLeads

    if (!targets.length) {
      toast.error('No ready leads to send. Generate AI messages first.')
      return
    }

    setBulkSending(true)

    // Group by channel for efficient batching
    const groups = {}
    for (const lead of targets) {
      const ch = lead.channel || 'EMAIL'
      if (!groups[ch]) groups[ch] = []
      groups[ch].push(lead.id)
    }

    try {
      const promises = Object.entries(groups).map(([ch, ids]) =>
        campaignApi.bulkSend(ids, ch)
      )
      await Promise.all(promises)
      toast.success(`${targets.length} lead${targets.length !== 1 ? 's' : ''} queued for sending`)
      qc.invalidateQueries({ queryKey: ['pending-leads'] })
      setSelected(new Set())
    } catch (e) {
      toast.error(e.message || 'Bulk send failed')
    } finally {
      setBulkSending(false)
    }
  }

  // ── Bulk approve label ────────────────────────────────────────────────────

  const selectedReady = selected.size > 0
    ? readyLeads.filter((l) => selected.has(l.id)).length
    : readyLeads.length

  const bulkLabel =
    selected.size > 0
      ? `Approve ${selectedReady} Selected`
      : `Approve All Ready (${readyLeads.length})`

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 h-full flex flex-col gap-4 overflow-hidden">

      {/* ── Page header ──────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between shrink-0 flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-100 flex items-center gap-2.5">
            <Bot size={20} className="text-violet-400" />
            AI Content Studio
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Review, edit, and approve AI-generated outreach before it sends
          </p>
        </div>

        {/* Ollama status */}
        <div className={clsx(
          'flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium',
          ollamaOk
            ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400'
            : 'bg-red-500/10 border-red-500/25 text-red-400',
        )}>
          <div className={clsx(
            'w-1.5 h-1.5 rounded-full',
            ollamaOk ? 'bg-emerald-400 animate-pulse' : 'bg-red-400',
          )} />
          {ollamaOk ? `Ollama · ${ollamaStatus?.model}` : 'Ollama Offline'}
        </div>
      </div>

      {/* ── Main layout ──────────────────────────────────────────────────── */}
      <div className="flex gap-5 flex-1 min-h-0">

        {/* ══════ LEFT — Lead Queue ══════ */}
        <div className="flex-1 flex flex-col gap-3 min-h-0 min-w-0">

          {/* Toolbar */}
          <div className="flex items-center gap-2 shrink-0 flex-wrap">

            {/* Filter tabs */}
            <div className="flex items-center gap-0.5 bg-slate-800/60 rounded-lg p-1 border border-slate-700/40">
              {FILTER_TABS.map(({ id, label }) => (
                <button
                  key={id}
                  onClick={() => { setFilter(id); setSelected(new Set()) }}
                  className={clsx(
                    'px-3 py-1 rounded-md text-xs font-medium transition-all whitespace-nowrap',
                    filter === id
                      ? 'bg-slate-700 text-slate-100 shadow-sm'
                      : 'text-slate-500 hover:text-slate-300',
                  )}
                >
                  {label}
                  <span className={clsx(
                    'ml-1.5 font-mono text-[10px]',
                    filter === id ? 'text-slate-400' : 'text-slate-700',
                  )}>
                    {filterCounts[id]}
                  </span>
                </button>
              ))}
            </div>

            {/* Select helpers */}
            <div className="flex items-center text-xs text-slate-600">
              <button onClick={selectAll}  className="hover:text-slate-300 px-2 py-1 transition-colors">All</button>
              <span className="text-slate-800">·</span>
              <button onClick={selectNone} className="hover:text-slate-300 px-2 py-1 transition-colors">None</button>
            </div>

            {selected.size > 0 && (
              <span className="text-[11px] text-brand-400 font-mono">
                {selected.size} selected
              </span>
            )}

            {/* Generate missing — only when Needs AI leads exist */}
            {needsLeads.length > 0 && (
              <button
                onClick={() => bulkGenMut.mutate()}
                disabled={bulkGenMut.isPending || !ollamaOk}
                className="btn-secondary text-xs py-1.5 px-3"
                title={!ollamaOk ? 'Ollama must be running' : ''}
              >
                {bulkGenMut.isPending
                  ? <><RefreshCw size={11} className="animate-spin" /> Queuing…</>
                  : <><Wand2 size={11} /> Generate {needsLeads.length} Missing</>}
              </button>
            )}

            {/* Bulk Approve */}
            <button
              onClick={handleBulkApprove}
              disabled={bulkSending || readyLeads.length === 0}
              className="ml-auto btn-primary text-xs py-1.5 px-4"
            >
              {bulkSending
                ? <><RefreshCw size={12} className="animate-spin" /> Sending…</>
                : <><Layers size={12} /> {bulkLabel}</>}
            </button>
          </div>

          {/* Lead cards */}
          <div className="flex-1 overflow-y-auto space-y-3 pr-0.5">

            {isLoading && (
              <div className="flex items-center justify-center h-40 text-slate-500 text-sm gap-2">
                <RefreshCw size={14} className="animate-spin" />
                Loading pending leads…
              </div>
            )}

            {!isLoading && visibleLeads.length === 0 && (
              <div className="flex flex-col items-center justify-center h-48 gap-3">
                <CheckCircle2 size={36} className="text-slate-700" />
                <p className="text-slate-500 text-sm font-medium">
                  {filter === 'needs' ? 'All leads have AI messages' :
                   filter === 'ready' ? 'No leads ready to send' :
                   'Queue is empty — inbox zero!'}
                </p>
                <p className="text-slate-700 text-xs">
                  {filter === 'all'   && 'Run a campaign or import a CSV to add leads.'}
                  {filter === 'ready' && 'Click "Generate Missing" to create AI messages.'}
                  {filter === 'needs' && 'Switch to "Ready" to review messages and send.'}
                </p>
              </div>
            )}

            {visibleLeads.map((lead) => (
              <LeadCard
                key={lead.id}
                lead={lead}
                checked={selected.has(lead.id)}
                onCheck={() => toggleSelect(lead.id)}
              />
            ))}
          </div>
        </div>

        {/* ══════ RIGHT — Tools Sidebar ══════ */}
        <div className="w-80 shrink-0 flex flex-col gap-4 overflow-y-auto">
          <TestAIPanel ollamaConnected={ollamaOk} />
          <DNAEditor />
        </div>

      </div>
    </div>
  )
}
