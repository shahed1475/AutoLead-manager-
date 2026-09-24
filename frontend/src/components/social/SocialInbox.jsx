import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, EyeOff, Inbox as InboxIcon, MessageCircle, MessageSquare, RefreshCw, Send, Sparkles, UserPlus } from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { socialApi } from '../../api/client'
import ErrorState from '../ui/ErrorState'

// Comments + DMs from your Facebook Pages and Instagram accounts (Meta API).
// With automatic replies on, the AI answers new ones from your Company DNA;
// you can always answer (or skip) by hand here.

const STATE = {
  NEW: ['New', 'bg-info/10 text-info'],
  SEEN: ['Seen', 'bg-secondary text-muted-foreground'],
  REPLIED: ['Answered', 'bg-success/10 text-success'],
  OPTOUT: ['Asked to stop', 'bg-error/10 text-error'],
  SKIPPED: ['Skipped', 'bg-secondary text-muted-foreground'],
  FAILED: ['Couldn’t answer', 'bg-warning/10 text-warning'],
}
const utc = (iso) => (iso ? new Date(`${String(iso).replace(' ', 'T')}Z`) : null)
const when = (iso) => utc(iso)?.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) || ''

function Thread({ item, onChanged }) {
  const [text, setText] = useState('')
  useEffect(() => setText(''), [item.id])
  const thread = useQuery({ queryKey: ['social-thread', item.id], queryFn: () => socialApi.thread(item.id), refetchInterval: 8000 })
  const draft = useMutation({ mutationFn: () => socialApi.draft(item.id), onSuccess: setText, onError: (e) => toast.error(e.message) })
  const reply = useMutation({
    mutationFn: () => socialApi.reply(item.id, text),
    onSuccess: () => { setText(''); toast.success('Sent'); thread.refetch(); onChanged() },
    onError: (e) => toast.error(e.message),
  })
  const skip = useMutation({ mutationFn: () => socialApi.mark(item.id, 'SKIPPED'), onSuccess: onChanged })
  const blocked = item.status === 'OPTOUT' || item.lead_status === 'DO_NOT_CONTACT'
  const limit = item.kind === 'comment' ? 300 : 700
  return (
    <section className="card flex flex-col min-h-[28rem]">
      <div className="px-5 py-3.5 border-b border-border-subtle">
        <p className="text-sm font-semibold truncate">{item.author_name || 'Someone'}</p>
        <p className="text-meta">{item.kind === 'comment' ? 'Public comment' : 'Direct message'} on {item.account_name} · {item.platform === 'instagram' ? 'Instagram' : 'Facebook'}</p>
      </div>
      <ul className="flex-1 overflow-y-auto p-5 space-y-3 max-h-[26rem]">
        {thread.isLoading && <li className="text-support">Loading…</li>}
        {(thread.data || []).map((m) => (
          <li key={m.id} className={clsx('max-w-[85%] rounded-2xl px-3.5 py-2 text-sm whitespace-pre-line break-words',
            m.direction === 'OUT' ? 'ml-auto bg-primary/10 text-foreground' : 'bg-secondary')}>
            {m.text || <span className="text-muted-foreground">(no text)</span>}
            <span className="block text-2xs text-muted-foreground mt-1">
              {m.direction === 'OUT' ? (m.status === 'AUTO' ? 'Automatic reply · ' : 'You · ') : ''}{when(m.created_at)}
            </span>
          </li>
        ))}
      </ul>
      <form className="border-t border-border-subtle p-4 space-y-2" onSubmit={(e) => { e.preventDefault(); reply.mutate() }}>
        {blocked ? <p className="text-sm text-error">This person asked not to be contacted — HOM won’t answer them.</p> : (
          <>
            <textarea rows={3} className="input" maxLength={limit} value={text} onChange={(e) => setText(e.target.value)}
              placeholder={item.kind === 'comment' ? 'Your public reply (keep it short; invite them to message you for details)' : 'Your reply'} aria-label="Reply" />
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-secondary h-9 text-sm" disabled={draft.isPending} onClick={() => draft.mutate()}>
                {draft.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />} {draft.isPending ? 'Writing…' : 'Write with AI'}
              </button>
              <span className="flex-1" />
              {['NEW', 'SEEN', 'FAILED'].includes(item.status) && <button type="button" className="btn-ghost h-9 text-sm" onClick={() => skip.mutate()}><EyeOff size={14} /> Skip</button>}
              <button className="btn-primary h-9 text-sm" disabled={!text.trim() || reply.isPending}><Send size={14} /> Send</button>
            </div>
          </>
        )}
      </form>
    </section>
  )
}

export default function SocialInbox({ hasMeta }) {
  const qc = useQueryClient()
  const [sel, setSel] = useState(null)
  const q = useQuery({ queryKey: ['social-inbox'], queryFn: socialApi.inbox, refetchInterval: 10000, enabled: hasMeta })
  const sync = useMutation({
    mutationFn: socialApi.inboxSync,
    onSuccess: (r) => { q.refetch(); toast.success(r.new ? `${r.new} new` : 'Up to date') },
    onError: (e) => toast.error(e.message),
  })
  const settings = useMutation({
    mutationFn: socialApi.inboxSettings,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['social-inbox'] }),
    onError: (e) => toast.error(e.message),
  })
  if (!hasMeta) return <p className="text-support py-8">Connect a Facebook Page or Instagram account (Accounts tab) to see comments and messages here. LinkedIn and X don’t let apps read comments or messages, so their inbox isn’t available.</p>
  if (q.isError) return <ErrorState message="Couldn't load the inbox." onRetry={q.refetch} />
  const d = q.data
  const s = d?.settings || {}
  const threads = d?.threads || []
  const current = threads.find((t) => t.id === sel) || null
  return (
    <div className="space-y-4">
      <section className="card p-4 flex flex-wrap items-center gap-x-6 gap-y-3">
        <label className="flex items-center gap-2.5 cursor-pointer text-sm">
          <input type="checkbox" className="size-4 accent-[rgb(var(--primary))]" checked={!!s.social_auto_reply}
            onChange={(e) => settings.mutate({ social_auto_reply: e.target.checked })} />
          <Bot size={15} className="text-primary" /> Answer automatically with AI
        </label>
        <label className={clsx('flex items-center gap-2.5 cursor-pointer text-sm', !s.social_auto_reply && 'opacity-50')}>
          <input type="checkbox" className="size-4 accent-[rgb(var(--primary))]" checked={!!s.social_reply_comments} disabled={!s.social_auto_reply}
            onChange={(e) => settings.mutate({ social_reply_comments: e.target.checked })} />
          Also public comments
        </label>
        <label className="flex items-center gap-2 text-sm">
          Per person per day
          <input type="number" min={0} max={50} className="input h-8 w-16" defaultValue={s.social_reply_per_person} key={s.social_reply_per_person}
            onBlur={(e) => Number(e.target.value) !== s.social_reply_per_person && settings.mutate({ social_reply_per_person: Number(e.target.value) })} />
        </label>
        <span className="flex-1" />
        <span className="text-meta tabular">{d?.summary?.new || 0} new · {d?.summary?.auto_today || 0} answered today</span>
        <button className="btn-secondary h-9 text-sm" disabled={sync.isPending} onClick={() => sync.mutate()}>
          <RefreshCw size={14} className={clsx(sync.isPending && 'animate-spin')} /> {sync.isPending ? 'Checking…' : 'Check now'}
        </button>
        <p className="basis-full text-meta">HOM checks every 2 minutes. Earlier comments and messages (before connecting, or older than 12 hours) are only shown. Anyone who asks to stop is never answered again and is marked Do not contact. Replies to public comments stay short and never mention prices.</p>
      </section>
      <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr] items-start">
        <ul className="card divide-y divide-border-subtle overflow-hidden max-h-[36rem] overflow-y-auto">
          {q.isLoading && <li className="p-5 text-support">Loading…</li>}
          {!q.isLoading && threads.length === 0 && (
            <li className="p-8 text-center text-support space-y-2"><InboxIcon size={22} className="mx-auto text-muted-foreground" /><p>No comments or messages yet.</p></li>
          )}
          {threads.map((t) => {
            const [label, cls] = STATE[t.status] || STATE.SEEN
            const Icon = t.kind === 'comment' ? MessageSquare : MessageCircle
            return (
              <li key={t.id}>
                <button className={clsx('w-full text-left px-4 py-3 flex gap-3 hover:bg-secondary/60', sel === t.id && 'bg-primary/5')} onClick={() => setSel(t.id)}>
                  <Icon size={15} className="mt-0.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="text-sm font-medium truncate">{t.author_name || 'Someone'}</span>
                      {t.lead_id && <UserPlus size={12} className="text-success shrink-0" aria-label="Saved as a lead" />}
                      <span className="flex-1" />
                      <span className="text-2xs text-muted-foreground tabular shrink-0">{when(t.created_at)}</span>
                    </span>
                    <span className="block text-sm text-muted-foreground truncate">{t.text}</span>
                    <span className="flex items-center gap-2 mt-1">
                      <span className={clsx('rounded-full px-2 py-0.5 text-2xs font-semibold', cls)}>{label}</span>
                      <span className="text-2xs text-muted-foreground">{t.platform === 'instagram' ? 'Instagram' : 'Facebook'} · {t.kind === 'comment' ? 'comment' : 'message'}</span>
                    </span>
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
        {current ? <Thread item={current} onChanged={() => q.refetch()} />
          : <div className="card p-8 text-center text-support hidden lg:block">Choose a comment or message to see the conversation and answer it.</div>}
      </div>
    </div>
  )
}
