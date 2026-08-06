import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Mail, RefreshCw, MessageSquare, TrendingUp, Users,
  Calendar, Send, Archive, ChevronRight, Inbox as InboxIcon,
  Clock, ExternalLink, BarChart3, Sparkles, Check, X,
} from 'lucide-react'
import { inboxApi, repliesApi } from '../api/client'
import StatCard from '../components/StatCard'
import toast from 'react-hot-toast'
import clsx from 'clsx'

// ── Intent config — handles both new (lowercase) and legacy (uppercase) values ─

const INTENT_CFG = {
  interested:      { label: 'Interested',       cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', dot: 'bg-emerald-400' },
  meeting_request: { label: 'Meeting Request',  cls: 'bg-blue-500/15 text-blue-400 border-blue-500/30',         dot: 'bg-blue-400'    },
  not_interested:  { label: 'Not Interested',   cls: 'bg-red-500/15 text-red-400 border-red-500/30',            dot: 'bg-red-400'     },
  auto_reply:      { label: 'Auto Reply',       cls: 'bg-slate-500/15 text-slate-400 border-slate-600/30',      dot: 'bg-slate-500'   },
  unknown:         { label: 'Unknown',          cls: 'bg-slate-500/15 text-slate-500 border-slate-700/30',      dot: 'bg-slate-600'   },
  // legacy uppercase
  POSITIVE:        { label: 'Positive',         cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', dot: 'bg-emerald-400' },
  INTERESTED:      { label: 'Interested',       cls: 'bg-sky-500/15 text-sky-400 border-sky-500/30',            dot: 'bg-sky-400'     },
  NEUTRAL:         { label: 'Neutral',          cls: 'bg-slate-500/15 text-slate-400 border-slate-600/30',      dot: 'bg-slate-500'   },
  NEGATIVE:        { label: 'Negative',         cls: 'bg-red-500/15 text-red-400 border-red-500/30',            dot: 'bg-red-400'     },
  SPAM:            { label: 'Spam',             cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30',      dot: 'bg-amber-400'   },
}

const FILTER_TABS = [
  { value: '',                label: 'All'             },
  { value: 'interested',      label: 'Interested'      },
  { value: 'meeting_request', label: 'Meeting Request' },
  { value: 'not_interested',  label: 'Not Interested'  },
]

function timeAgo(ts) {
  if (!ts) return '—'
  const secs = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (secs < 60)    return `${secs}s ago`
  if (secs < 3600)  return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

// ── Intent badge ──────────────────────────────────────────────────────────────

function IntentBadge({ intent }) {
  const cfg = INTENT_CFG[intent] || INTENT_CFG.unknown
  return (
    <span className={clsx(
      'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-semibold border whitespace-nowrap',
      cfg.cls,
    )}>
      <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', cfg.dot)} />
      {cfg.label}
    </span>
  )
}

// ── Reply list item ───────────────────────────────────────────────────────────

function ReplyItem({ item, active, onClick }) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'w-full flex items-start gap-3 p-3.5 rounded-xl border text-left transition-all group',
        active
          ? 'bg-brand-600/10 border-brand-500/40'
          : 'bg-slate-800/30 border-slate-700/30 hover:bg-slate-800/60 hover:border-slate-600/40',
        !item.processed && !active && 'border-l-2 border-l-brand-500 rounded-l-none',
      )}
    >
      <div className={clsx(
        'w-8 h-8 rounded-full border flex items-center justify-center shrink-0 mt-0.5',
        active
          ? 'bg-brand-600/20 border-brand-500/40'
          : 'bg-slate-700/60 border-slate-600/40',
      )}>
        <Mail size={13} className={active ? 'text-brand-400' : 'text-slate-400'} />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2 mb-1.5">
          <p className={clsx(
            'text-sm font-semibold truncate',
            active ? 'text-slate-100' : 'text-slate-200',
          )}>
            {item.business_name || item.from_email || 'Unknown'}
          </p>
          {!item.processed && (
            <span className="w-2 h-2 rounded-full bg-brand-500 shrink-0 mt-1" />
          )}
        </div>

        <div className="mb-1.5">
          <IntentBadge intent={item.intent} />
        </div>

        {item.body_snippet && (
          <p className="text-[11px] text-slate-500 truncate leading-relaxed">
            {item.body_snippet}
          </p>
        )}

        <p className="text-[10px] text-slate-600 mt-1.5">
          {timeAgo(item.created_at || item.received_at)}
        </p>
      </div>

      <ChevronRight
        size={12}
        className={clsx(
          'shrink-0 mt-1 transition-opacity',
          active
            ? 'text-brand-400 opacity-100'
            : 'text-slate-700 opacity-0 group-hover:opacity-60',
        )}
      />
    </button>
  )
}

// ── Thread detail panel ───────────────────────────────────────────────────────

function ThreadPanel({ item, onProcess }) {
  const navigate = useNavigate()

  if (!item) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <div className="w-14 h-14 rounded-full bg-slate-800/80 border border-slate-700/40 flex items-center justify-center">
          <MessageSquare size={22} className="text-slate-600" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium text-slate-400">Select a reply to view</p>
          <p className="text-xs text-slate-600 mt-1">Click any item on the left to open the thread</p>
        </div>
      </div>
    )
  }

  const handleBookCall = () => {
    onProcess(item.id, 'meeting_request')
    const title   = encodeURIComponent(`Call with ${item.business_name || 'Lead'}`)
    const details = encodeURIComponent(
      `Reply from: ${item.from_email || ''}\n\n${item.body_snippet || item.body || ''}`,
    )
    window.open(
      `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&details=${details}`,
      '_blank',
    )
  }

  const handleArchive = () => {
    onProcess(item.id, 'not_interested')
  }

  return (
    <div className="flex flex-col h-full">

      {/* ── Lead header ──────────────────────────────────────────────────── */}
      <div className="px-5 py-4 border-b border-slate-800 shrink-0">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-bold text-slate-100 truncate">
              {item.business_name || 'Unknown Business'}
            </h2>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              {item.niche && (
                <span className="text-xs text-slate-500">{item.niche}</span>
              )}
              {item.city && (
                <span className="text-xs text-slate-600">· {item.city}</span>
              )}
              {item.from_email && (
                <span className="text-xs text-slate-600">{item.from_email}</span>
              )}
            </div>
            {item.website && (
              <a
                href={item.website}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-300 mt-1.5 transition-colors"
              >
                <ExternalLink size={10} />
                {item.website.replace(/^https?:\/\//, '').replace(/\/$/, '')}
              </a>
            )}
          </div>
          <IntentBadge intent={item.intent} />
        </div>

        <div className="flex items-center gap-1.5 mt-2 text-[10px] text-slate-600">
          <Clock size={9} />
          Replied {timeAgo(item.created_at || item.received_at)}
          {item.subject && (
            <span className="ml-2 text-slate-700 truncate max-w-[240px]">· {item.subject}</span>
          )}
        </div>
      </div>

      {/* ── Scrollable thread ─────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto p-5 space-y-5">

        {/* Outreach we sent */}
        {(item.ai_email_subject || item.ai_email_body) && (
          <div className="space-y-2">
            <p className="text-[10px] font-bold uppercase tracking-widest text-slate-600 flex items-center gap-1.5">
              <span className="w-4 h-px bg-slate-700 inline-block" />
              Your Outreach
              <span className="flex-1 h-px bg-slate-800 inline-block" />
            </p>
            <div className="rounded-xl bg-slate-800/40 border border-slate-700/30 p-4 space-y-2.5">
              {item.ai_email_subject && (
                <p className="text-xs font-semibold text-slate-300">{item.ai_email_subject}</p>
              )}
              {item.ai_email_body && (
                <p className="text-xs text-slate-400 leading-relaxed whitespace-pre-wrap font-mono max-h-48 overflow-y-auto">
                  {item.ai_email_body}
                </p>
              )}
            </div>
          </div>
        )}

        {/* Their reply */}
        <div className="space-y-2">
          <p className="text-[10px] font-bold uppercase tracking-widest text-slate-600 flex items-center gap-1.5">
            <span className="w-4 h-px bg-slate-700 inline-block" />
            Their Reply
            <span className="flex-1 h-px bg-slate-800 inline-block" />
          </p>
          <div className="rounded-xl bg-slate-800/60 border border-brand-500/20 p-4">
            {item.body_snippet || item.body ? (
              <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap">
                {item.body_snippet || item.body}
              </p>
            ) : (
              <p className="text-xs text-slate-600 italic">No reply body captured</p>
            )}
          </div>
        </div>
      </div>

      {/* ── Action bar ────────────────────────────────────────────────────── */}
      <div className="px-5 py-4 border-t border-slate-800 flex items-center gap-2.5 shrink-0">
        <button
          onClick={handleBookCall}
          className="flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-lg
                     bg-emerald-600/15 border border-emerald-500/30 text-emerald-400
                     hover:bg-emerald-600/25 hover:border-emerald-500/50 transition-all text-xs font-semibold"
        >
          <Calendar size={12} /> Book Call
        </button>
        <button
          onClick={() => navigate('/ai-lab')}
          className="flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-lg
                     bg-brand-600/15 border border-brand-500/30 text-brand-400
                     hover:bg-brand-600/25 hover:border-brand-500/50 transition-all text-xs font-semibold"
        >
          <Send size={12} /> Send Proposal
        </button>
        <button
          onClick={handleArchive}
          title="Archive (mark not interested)"
          className="flex items-center justify-center gap-2 py-2 px-3 rounded-lg
                     bg-slate-700/40 border border-slate-600/30 text-slate-400
                     hover:bg-slate-700/70 hover:text-slate-300 transition-all text-xs font-semibold"
        >
          <Archive size={12} /> Archive
        </button>
      </div>
    </div>
  )
}

// ── Pending auto-reply drafts ────────────────────────────────────────────────
// Positive-intent replies get an AI-drafted response held here for approval
// before anything is sent — see reply_detector.py's draft-and-approve flow.

function DraftCard({ draft, onApprove, onDiscard, isBusy }) {
  const [subject, setSubject] = useState(draft.draft_subject || '')
  const [body, setBody]       = useState(draft.draft_body || '')
  const dirty = subject !== (draft.draft_subject || '') || body !== (draft.draft_body || '')

  return (
    <div className="rounded-xl bg-slate-800/40 border border-amber-500/20 p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-100 truncate">
            {draft.business_name || draft.email || 'Unknown lead'}
          </p>
          <p className="text-[10px] text-slate-500 mt-0.5">
            {timeAgo(draft.received_at)} · replying to their {draft.detected_intent === 'meeting_request' ? 'meeting request' : 'interest'}
          </p>
        </div>
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border bg-amber-500/15 text-amber-400 border-amber-500/30 shrink-0">
          <Sparkles size={10} /> Draft
        </span>
      </div>

      {draft.reply_text && (
        <div className="rounded-lg bg-slate-900/50 border border-slate-700/30 p-2.5">
          <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wide mb-1">Their reply</p>
          <p className="text-xs text-slate-400 leading-relaxed line-clamp-3">{draft.reply_text}</p>
        </div>
      )}

      <div className="space-y-2">
        <input
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder="Subject"
          className="w-full text-xs px-3 py-2 rounded-lg bg-slate-900/60 border border-slate-700/40
                     text-slate-200 focus:border-brand-500/50 focus:outline-none"
        />
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={4}
          className="w-full text-xs px-3 py-2 rounded-lg bg-slate-900/60 border border-slate-700/40
                     text-slate-200 leading-relaxed resize-y focus:border-brand-500/50 focus:outline-none"
        />
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => onApprove(draft.id, dirty ? { draft_subject: subject, draft_body: body } : null)}
          disabled={isBusy}
          className="flex-1 flex items-center justify-center gap-1.5 py-1.5 px-3 rounded-lg
                     bg-emerald-600/15 border border-emerald-500/30 text-emerald-400
                     hover:bg-emerald-600/25 hover:border-emerald-500/50 transition-all text-xs font-semibold disabled:opacity-50"
        >
          <Check size={12} /> {dirty ? 'Save & Send' : 'Send'}
        </button>
        <button
          onClick={() => onDiscard(draft.id)}
          disabled={isBusy}
          className="flex items-center justify-center gap-1.5 py-1.5 px-3 rounded-lg
                     bg-slate-700/40 border border-slate-600/30 text-slate-400
                     hover:bg-slate-700/70 hover:text-slate-300 transition-all text-xs font-semibold disabled:opacity-50"
        >
          <X size={12} /> Discard
        </button>
      </div>
    </div>
  )
}

function PendingDraftsPanel() {
  const qc = useQueryClient()

  const { data: drafts } = useQuery({
    queryKey: ['reply-drafts'],
    queryFn:  repliesApi.drafts,
    refetchInterval: 30_000,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['reply-drafts'] })

  const approveMut = useMutation({
    mutationFn: async ({ id, edits }) => {
      if (edits) await repliesApi.edit(id, edits)
      return repliesApi.approve(id)
    },
    onSuccess: () => { toast.success('Reply sent'); invalidate() },
    onError:   (e) => toast.error(e.response?.data?.detail || e.message),
  })

  const discardMut = useMutation({
    mutationFn: (id) => repliesApi.discard(id),
    onSuccess: () => { toast.success('Draft discarded'); invalidate() },
    onError:   (e) => toast.error(e.response?.data?.detail || e.message),
  })

  if (!drafts || drafts.length === 0) return null

  return (
    <div className="shrink-0 space-y-2.5">
      <p className="text-[10px] font-bold uppercase tracking-widest text-amber-500 flex items-center gap-1.5">
        <Sparkles size={11} />
        {drafts.length} auto-reply draft{drafts.length === 1 ? '' : 's'} awaiting approval
      </p>
      <div className="grid grid-cols-2 gap-3 max-h-80 overflow-y-auto pr-0.5">
        {drafts.map((d) => (
          <DraftCard
            key={d.id}
            draft={d}
            isBusy={approveMut.isPending || discardMut.isPending}
            onApprove={(id, edits) => approveMut.mutate({ id, edits })}
            onDiscard={(id) => discardMut.mutate(id)}
          />
        ))}
      </div>
    </div>
  )
}

// ── Main Inbox page ───────────────────────────────────────────────────────────

export default function Inbox() {
  const qc = useQueryClient()
  const [selectedId, setSelectedId]     = useState(null)
  const [intentFilter, setIntentFilter] = useState('')
  const [page, setPage]                 = useState(1)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['inbox', intentFilter, page],
    queryFn:  () => inboxApi.list({ page, page_size: 50, intent: intentFilter || undefined }),
    refetchInterval: 30_000,
  })

  const { data: stats } = useQuery({
    queryKey: ['inbox-stats'],
    queryFn:  inboxApi.stats,
    refetchInterval: 30_000,
  })

  const { data: summary } = useQuery({
    queryKey: ['inbox-summary'],
    queryFn:  inboxApi.summary,
    refetchInterval: 60_000,
    retry: false,
  })

  const processMut = useMutation({
    mutationFn: ({ id, intent }) => inboxApi.process(id, intent),
    onSuccess: (_, { intent }) => {
      qc.invalidateQueries({ queryKey: ['inbox'] })
      qc.invalidateQueries({ queryKey: ['inbox-stats'] })
      qc.invalidateQueries({ queryKey: ['inbox-summary'] })
      const label = INTENT_CFG[intent]?.label || intent
      toast.success(`Marked as ${label}`)
    },
    onError: (e) => toast.error(e.message),
  })

  const checkMut = useMutation({
    mutationFn: inboxApi.check,
    onSuccess: () => {
      toast.success('IMAP check started — new replies will appear shortly')
      setTimeout(() => refetch(), 4000)
    },
    onError: (e) => toast.error(e.message),
  })

  const items      = data?.items ?? []
  const activeItem = selectedId ? (items.find((i) => i.id === selectedId) ?? null) : null

  // Derive stats — prefer summary endpoint, fall back to stats + local counts
  const totalReplies    = summary?.total_replies    ?? stats?.total          ?? 0
  const interestedCount = summary?.interested_count ?? items.filter((i) => i.intent === 'interested' || i.intent === 'INTERESTED' || i.intent === 'POSITIVE').length
  const meetingCount    = summary?.meeting_requests ?? items.filter((i) => i.intent === 'meeting_request').length
  const replyRate       = summary?.reply_rate_percent ?? (stats?.reply_rate ?? null)
  const totalSent       = summary?.total_sent       ?? null

  const handleProcess = (id, intent) => {
    processMut.mutate({ id, intent })
    if (intent === 'not_interested' && id === selectedId) {
      setSelectedId(null)
    }
  }

  return (
    <div className="p-6 flex flex-col h-full gap-4 overflow-hidden">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Reply Inbox</h1>
          <p className="text-xs text-slate-500 mt-0.5">AI-classified email replies from your leads</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => refetch()}
            className="btn-secondary text-xs"
          >
            <RefreshCw size={12} /> Refresh
          </button>
          <button
            onClick={() => checkMut.mutate()}
            disabled={checkMut.isPending}
            className="btn-primary text-xs"
          >
            {checkMut.isPending
              ? <><RefreshCw size={12} className="animate-spin" /> Checking…</>
              : <><Mail size={12} /> Check for New Replies</>}
          </button>
        </div>
      </div>

      <PendingDraftsPanel />

      {/* ── Stats bar ──────────────────────────────────────────────────── */}
      <div className="grid grid-cols-4 gap-3 shrink-0">
        <StatCard
          variant="compact"
          icon={InboxIcon}
          label="Total Replies"
          value={totalReplies}
          valueColor="text-slate-200"
          iconBg="bg-slate-700/60"
        />
        <StatCard
          variant="compact"
          icon={TrendingUp}
          label="Interested Leads"
          value={interestedCount}
          valueColor="text-emerald-400"
          iconBg="bg-emerald-500/10"
        />
        <StatCard
          variant="compact"
          icon={Calendar}
          label="Meeting Requests"
          value={meetingCount}
          valueColor="text-blue-400"
          iconBg="bg-blue-500/10"
        />
        <StatCard
          variant="compact"
          icon={BarChart3}
          label="Reply Rate"
          value={replyRate !== null ? `${typeof replyRate === 'number' ? replyRate.toFixed(1) : replyRate}%` : '—'}
          valueColor="text-brand-400"
          iconBg="bg-brand-500/10"
          sub={totalSent ? `${totalSent} emails sent` : undefined}
        />
      </div>

      {/* ── Split panel ────────────────────────────────────────────────── */}
      <div className="flex gap-4 flex-1 min-h-0">

        {/* Left — reply list */}
        <div className="w-[340px] shrink-0 flex flex-col gap-3 min-h-0">

          {/* Intent filter tabs */}
          <div className="flex items-center gap-1.5 flex-wrap shrink-0">
            {FILTER_TABS.map(({ value, label }) => (
              <button
                key={value}
                onClick={() => { setIntentFilter(value); setPage(1); setSelectedId(null) }}
                className={clsx(
                  'px-3 py-1.5 rounded-lg border text-xs font-medium transition-all',
                  intentFilter === value
                    ? 'border-brand-500/50 bg-brand-500/15 text-brand-400'
                    : 'border-slate-700/50 text-slate-500 hover:border-slate-600 hover:text-slate-400',
                )}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Reply list */}
          <div className="flex-1 overflow-y-auto space-y-2 pr-0.5 min-h-0">
            {isLoading ? (
              <div className="flex items-center justify-center h-32 gap-2 text-slate-600 text-sm">
                <RefreshCw size={13} className="animate-spin" /> Loading replies…
              </div>
            ) : items.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-48 gap-3">
                <InboxIcon size={32} className="text-slate-700" />
                <p className="text-sm text-slate-500">No replies found</p>
                <p className="text-xs text-slate-700 text-center max-w-[200px]">
                  Configure IMAP in Settings then click "Check for New Replies"
                </p>
              </div>
            ) : (
              items.map((item) => (
                <ReplyItem
                  key={item.id}
                  item={item}
                  active={item.id === selectedId}
                  onClick={() => setSelectedId(item.id === selectedId ? null : item.id)}
                />
              ))
            )}
          </div>

          {/* Pagination */}
          {(data?.total_pages ?? 1) > 1 && (
            <div className="flex items-center justify-center gap-2 shrink-0">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="btn-secondary text-xs px-3 py-1"
              >
                Prev
              </button>
              <span className="text-xs text-slate-500">{page} / {data.total_pages}</span>
              <button
                onClick={() => setPage((p) => Math.min(data.total_pages, p + 1))}
                disabled={page >= data.total_pages}
                className="btn-secondary text-xs px-3 py-1"
              >
                Next
              </button>
            </div>
          )}
        </div>

        {/* Right — thread detail */}
        <div className="flex-1 card overflow-hidden">
          <ThreadPanel item={activeItem} onProcess={handleProcess} />
        </div>
      </div>
    </div>
  )
}
