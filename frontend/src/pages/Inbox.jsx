import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Inbox as InboxIcon, RefreshCw, CheckCircle2, Mail,
  TrendingUp, ThumbsUp, ThumbsDown, MessageSquare, Minus,
} from 'lucide-react'
import { inboxApi } from '../api/client'
import toast from 'react-hot-toast'
import clsx from 'clsx'

const INTENTS = [
  { value: '',           label: 'All',        icon: InboxIcon,      color: 'text-slate-400' },
  { value: 'POSITIVE',  label: 'Positive',   icon: ThumbsUp,       color: 'text-emerald-400' },
  { value: 'INTERESTED',label: 'Interested', icon: TrendingUp,     color: 'text-sky-400' },
  { value: 'NEUTRAL',   label: 'Neutral',    icon: Minus,          color: 'text-slate-400' },
  { value: 'NEGATIVE',  label: 'Negative',   icon: ThumbsDown,     color: 'text-red-400' },
  { value: 'SPAM',      label: 'Spam',       icon: MessageSquare,  color: 'text-amber-400' },
]

const INTENT_STYLES = {
  POSITIVE:   'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  INTERESTED: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
  NEUTRAL:    'bg-slate-500/15 text-slate-400 border-slate-600/30',
  NEGATIVE:   'bg-red-500/15 text-red-400 border-red-500/30',
  SPAM:       'bg-amber-500/15 text-amber-400 border-amber-500/30',
}

function timeAgo(ts) {
  if (!ts) return '—'
  const secs = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (secs < 60)    return `${secs}s ago`
  if (secs < 3600)  return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

function ReplyCard({ item, onProcess }) {
  const [expanded, setExpanded] = useState(false)
  const intentStyle = INTENT_STYLES[item.intent] || INTENT_STYLES.NEUTRAL

  return (
    <div
      className={clsx(
        'card p-4 space-y-3 transition-all cursor-pointer hover:border-slate-600/60',
        !item.processed && 'border-l-2 border-l-brand-500',
      )}
      onClick={() => setExpanded((e) => !e)}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-full bg-brand-600/20 border border-brand-600/30 flex items-center justify-center shrink-0">
            <Mail size={13} className="text-brand-400" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-200 truncate">
              {item.business_name || item.from_email}
            </p>
            <p className="text-xs text-slate-500 truncate">{item.from_email}</p>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <span className={clsx('text-[10px] px-2 py-0.5 rounded-md border font-medium', intentStyle)}>
            {item.intent}
          </span>
          {!item.processed && (
            <span className="w-2 h-2 rounded-full bg-brand-500" title="Unread" />
          )}
          <span className="text-[10px] text-slate-600">{timeAgo(item.created_at)}</span>
        </div>
      </div>

      {item.subject && (
        <p className="text-xs font-medium text-slate-300 truncate">{item.subject}</p>
      )}

      {expanded && (
        <div className="space-y-3 pt-1">
          {item.body_snippet && (
            <p className="text-xs text-slate-400 bg-slate-900/40 rounded-lg p-3 leading-relaxed border border-slate-700/30">
              {item.body_snippet}
            </p>
          )}

          {item.niche && (
            <p className="text-[10px] text-slate-600">
              {item.niche}{item.city ? ` · ${item.city}` : ''}
            </p>
          )}

          <div className="flex items-center gap-2 flex-wrap" onClick={(e) => e.stopPropagation()}>
            <p className="text-[10px] text-slate-500 mr-1">Mark as:</p>
            {['POSITIVE', 'INTERESTED', 'NEUTRAL', 'NEGATIVE', 'SPAM'].map((intent) => (
              <button
                key={intent}
                onClick={() => onProcess(item.id, intent)}
                className={clsx(
                  'text-[10px] px-2 py-0.5 rounded border transition-all',
                  item.intent === intent
                    ? INTENT_STYLES[intent]
                    : 'border-slate-700 text-slate-500 hover:border-slate-600 hover:text-slate-300',
                )}
              >
                {intent}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Inbox() {
  const qc = useQueryClient()
  const [intentFilter, setIntentFilter] = useState('')
  const [page, setPage] = useState(1)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['inbox', intentFilter, page],
    queryFn: () => inboxApi.list({ page, page_size: 30, intent: intentFilter || undefined }),
    refetchInterval: 30_000,
  })

  const { data: stats } = useQuery({
    queryKey: ['inbox-stats'],
    queryFn: inboxApi.stats,
    refetchInterval: 30_000,
  })

  const processMut = useMutation({
    mutationFn: ({ id, intent }) => inboxApi.process(id, intent),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['inbox'] })
      qc.invalidateQueries({ queryKey: ['inbox-stats'] })
      qc.invalidateQueries({ queryKey: ['dashboard-stats'] })
      toast.success('Reply classified')
    },
    onError: (e) => toast.error(e.message),
  })

  const checkMut = useMutation({
    mutationFn: inboxApi.check,
    onSuccess: () => toast.success('Reply check queued — may take a moment'),
    onError: (e) => toast.error(e.message),
  })

  const items = data?.items ?? []

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Reply Inbox</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Auto-detected email replies from leads
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => checkMut.mutate()}
            disabled={checkMut.isPending}
            className="btn-secondary text-xs"
          >
            {checkMut.isPending
              ? <><RefreshCw size={12} className="animate-spin" /> Checking...</>
              : <><RefreshCw size={12} /> Check IMAP</>}
          </button>
          <button onClick={() => refetch()} className="btn-secondary text-xs">
            <RefreshCw size={12} /> Refresh
          </button>
        </div>
      </div>

      {/* Stats strip */}
      <div className="grid grid-cols-2 gap-3">
        <div className="card p-4 border border-slate-700/30">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider">Total Replies</p>
          <p className="text-2xl font-bold text-slate-200 mt-1">{stats?.total ?? '—'}</p>
        </div>
        <div className="card p-4 border border-slate-700/30">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider">Unread</p>
          <p className="text-2xl font-bold text-brand-400 mt-1">{stats?.unread ?? '—'}</p>
        </div>
      </div>

      {/* Intent filter */}
      <div className="flex items-center gap-2 flex-wrap">
        {INTENTS.map(({ value, label, icon: Icon, color }) => (
          <button
            key={value}
            onClick={() => { setIntentFilter(value); setPage(1) }}
            className={clsx(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-all',
              intentFilter === value
                ? 'border-brand-500/50 bg-brand-500/15 text-brand-400'
                : 'border-slate-700/50 text-slate-500 hover:border-slate-600 hover:text-slate-300',
            )}
          >
            <Icon size={11} className={intentFilter === value ? '' : color} />
            {label}
          </button>
        ))}
      </div>

      {/* Reply list */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16 text-slate-600 text-sm gap-2">
          <div className="w-4 h-4 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
          Loading replies...
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3">
          <InboxIcon size={36} className="text-slate-700" />
          <p className="text-slate-500 text-sm">No replies yet</p>
          <p className="text-slate-600 text-xs text-center max-w-xs">
            Configure IMAP in Settings and click "Check IMAP" to detect replies from leads.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <ReplyCard
              key={item.id}
              item={item}
              onProcess={(id, intent) => processMut.mutate({ id, intent })}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {data?.total_pages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="btn-secondary text-xs px-3"
          >
            Prev
          </button>
          <span className="text-xs text-slate-500">
            {page} / {data.total_pages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(data.total_pages, p + 1))}
            disabled={page >= data.total_pages}
            className="btn-secondary text-xs px-3"
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
