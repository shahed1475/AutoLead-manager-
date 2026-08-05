import { useEffect, useState } from 'react'
import {
  X, Copy, Edit2, Check, Send, RefreshCw,
  MessageSquare, Mail, Reply, Clock,
} from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { leadsApi, aiApi } from '../api/client'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import EnrichmentCard from './EnrichmentCard'
import { useFocusTrap } from '../hooks/useFocusTrap'

const TABS = [
  { id: 'whatsapp', label: 'WhatsApp', icon: MessageSquare, channel: 'WHATSAPP', regenType: 'whatsapp' },
  { id: 'email',    label: 'Email',    icon: Mail,          channel: 'EMAIL',    regenType: 'email' },
  { id: 'followup', label: 'Follow-Ups', icon: Reply,       channel: 'EMAIL',    regenType: 'followups' },
]

const FOLLOWUP_STEPS = [
  { key: 'ai_follow_up_1', label: 'FU #1', day: 'Day 3',  color: 'text-sky-400'    },
  { key: 'ai_follow_up_2', label: 'FU #2', day: 'Day 10', color: 'text-violet-400' },
  { key: 'ai_follow_up_3', label: 'FU #3', day: 'Day 17', color: 'text-amber-400'  },
]

function CopyBtn({ text }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text || '')
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }}
      title="Copy"
      aria-label={copied ? 'Copied' : 'Copy to clipboard'}
      className="p-1.5 rounded text-slate-500 hover:text-slate-300 hover:bg-slate-700 transition-all"
    >
      {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
    </button>
  )
}

export default function ViewMessagesModal({ lead, onClose }) {
  const qc = useQueryClient()
  const [tab, setTab] = useState('whatsapp')
  const [editMode, setEditMode] = useState(false)
  const [draft, setDraft] = useState({
    ai_whatsapp_msg:   lead.ai_whatsapp_msg   || '',
    ai_email_subject:  lead.ai_email_subject  || '',
    ai_email_body:     lead.ai_email_body     || '',
    ai_followup_msg:   lead.ai_followup_msg   || '',
    ai_follow_up_1:    lead.ai_follow_up_1    || lead.ai_followup_msg || '',
    ai_follow_up_2:    lead.ai_follow_up_2    || '',
    ai_follow_up_3:    lead.ai_follow_up_3    || '',
  })

  const tabConf = TABS.find((t) => t.id === tab)

  const regenMut = useMutation({
    mutationFn: ({ id, type }) => aiApi.generate(id, type),
    onSuccess: (updated) => {
      // Only overwrite fields that the API actually returned — don't clear others
      setDraft((prev) => {
        const patch = {}
        if (updated.ai_whatsapp_msg  != null) patch.ai_whatsapp_msg  = updated.ai_whatsapp_msg
        if (updated.ai_email_subject != null) patch.ai_email_subject = updated.ai_email_subject
        if (updated.ai_email_body    != null) patch.ai_email_body    = updated.ai_email_body
        if (updated.ai_followup_msg  != null) patch.ai_followup_msg  = updated.ai_followup_msg
        if (updated.ai_follow_up_1   != null) patch.ai_follow_up_1   = updated.ai_follow_up_1 || updated.ai_followup_msg || ''
        if (updated.ai_follow_up_2   != null) patch.ai_follow_up_2   = updated.ai_follow_up_2
        if (updated.ai_follow_up_3   != null) patch.ai_follow_up_3   = updated.ai_follow_up_3
        return { ...prev, ...patch }
      })
      qc.invalidateQueries({ queryKey: ['leads'] })
      toast.success('Messages regenerated')
    },
    onError: (e) => toast.error(e.message),
  })

  const saveMut = useMutation({
    mutationFn: () => leadsApi.update(lead.id, {
      ai_whatsapp_msg:  draft.ai_whatsapp_msg,
      ai_email_subject: draft.ai_email_subject,
      ai_email_body:    draft.ai_email_body,
      ai_followup_msg:  draft.ai_follow_up_1 || draft.ai_followup_msg,
      ai_follow_up_1:   draft.ai_follow_up_1,
      ai_follow_up_2:   draft.ai_follow_up_2,
      ai_follow_up_3:   draft.ai_follow_up_3,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['leads'] })
      toast.success('Saved')
      setEditMode(false)
    },
    onError: (e) => toast.error(e.message),
  })

  const resendMut = useMutation({
    mutationFn: ({ id, channel }) => leadsApi.resend(id, channel),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['leads'] })
      toast.success('Message sent')
    },
    onError: (e) => toast.error(e.message),
  })

  const isEmailTab   = tab === 'email'
  const isFollowupTab = tab === 'followup'
  const hasContent = isEmailTab
    ? (draft.ai_email_subject || draft.ai_email_body)
    : isFollowupTab
    ? (draft.ai_follow_up_1 || draft.ai_follow_up_2 || draft.ai_follow_up_3)
    : draft.ai_whatsapp_msg

  const dialogRef = useFocusTrap(true)

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="view-messages-title"
        className="card w-full max-w-2xl max-h-[82vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-slate-700/50">
          <div className="min-w-0">
            <h2 id="view-messages-title" className="font-semibold text-slate-100 truncate">{lead.business_name}</h2>
            <div className="flex items-center gap-3 mt-1 flex-wrap">
              <p className="text-xs text-slate-500">AI-Generated Messages</p>
              <EnrichmentCard lead={lead} />
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="p-1.5 rounded hover:bg-slate-700 text-slate-400 transition-all shrink-0 ml-3"
          >
            <X size={16} />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-700/50 px-1">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => { setTab(id); setEditMode(false) }}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-all ${
                tab === id
                  ? 'border-brand-500 text-brand-400'
                  : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}
            >
              <Icon size={13} /> {label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {!hasContent && !editMode ? (
            <div className="text-center py-10 text-slate-500">
              <p className="text-sm">No message generated yet</p>
              <button
                onClick={() => regenMut.mutate({ id: lead.id, type: tabConf.regenType })}
                disabled={regenMut.isPending}
                className="btn-primary text-xs mt-3 mx-auto"
              >
                {regenMut.isPending && <RefreshCw size={13} className="animate-spin" />}
                Generate Now
              </button>
            </div>
          ) : isEmailTab ? (
            <>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <p className="text-xs text-slate-400 uppercase tracking-wide font-medium">Subject</p>
                  <CopyBtn text={draft.ai_email_subject} />
                </div>
                {editMode ? (
                  <input
                    className="input text-xs"
                    value={draft.ai_email_subject}
                    onChange={(e) => setDraft((d) => ({ ...d, ai_email_subject: e.target.value }))}
                  />
                ) : (
                  <p className="text-sm text-slate-200 bg-slate-900/60 rounded-lg px-3 py-2.5 border border-slate-700/40">
                    {draft.ai_email_subject || <span className="text-slate-600 italic">empty</span>}
                  </p>
                )}
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <p className="text-xs text-slate-400 uppercase tracking-wide font-medium">Body</p>
                  <CopyBtn text={draft.ai_email_body} />
                </div>
                {editMode ? (
                  <textarea
                    className="input text-xs resize-none"
                    rows={9}
                    value={draft.ai_email_body}
                    onChange={(e) => setDraft((d) => ({ ...d, ai_email_body: e.target.value }))}
                  />
                ) : (
                  <pre className="text-xs text-slate-300 bg-slate-900/60 rounded-lg px-3 py-2.5 whitespace-pre-wrap font-sans leading-relaxed max-h-56 overflow-y-auto border border-slate-700/40">
                    {draft.ai_email_body || <span className="text-slate-600 italic">empty</span>}
                  </pre>
                )}
              </div>
            </>
          ) : isFollowupTab ? (
            <div className="space-y-4">
              {FOLLOWUP_STEPS.map(({ key, label, day, color }) => (
                <div key={key} className="rounded-lg border border-slate-700/40 bg-slate-900/40 p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Clock size={11} className={color} />
                      <span className={clsx('text-xs font-semibold', color)}>{label}</span>
                      <span className="text-[10px] text-slate-600 font-mono">{day}</span>
                    </div>
                    <CopyBtn text={draft[key]} />
                  </div>
                  {editMode ? (
                    <textarea
                      className="input text-xs resize-none w-full"
                      rows={4}
                      value={draft[key]}
                      onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                      placeholder={`${label} message…`}
                    />
                  ) : (
                    <pre className="text-xs text-slate-300 bg-slate-900/60 rounded-lg px-3 py-2.5 whitespace-pre-wrap font-sans leading-relaxed max-h-40 overflow-y-auto border border-slate-700/40">
                      {draft[key] || <span className="text-slate-600 italic">not generated yet</span>}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <p className="text-xs text-slate-400 uppercase tracking-wide font-medium">WhatsApp Message</p>
                <CopyBtn text={draft.ai_whatsapp_msg} />
              </div>
              {editMode ? (
                <textarea
                  className="input text-xs resize-none"
                  rows={9}
                  value={draft.ai_whatsapp_msg}
                  onChange={(e) => setDraft((d) => ({ ...d, ai_whatsapp_msg: e.target.value }))}
                />
              ) : (
                <pre className="text-xs text-slate-300 bg-slate-900/60 rounded-lg px-3 py-2.5 whitespace-pre-wrap font-sans leading-relaxed max-h-64 overflow-y-auto border border-slate-700/40">
                  {draft.ai_whatsapp_msg || <span className="text-slate-600 italic">empty</span>}
                </pre>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-slate-700/50">
          <div className="flex items-center gap-2">
            <button
              onClick={() => regenMut.mutate({ id: lead.id, type: tabConf.regenType })}
              disabled={regenMut.isPending}
              className="btn-secondary text-xs"
            >
              <RefreshCw size={13} className={regenMut.isPending ? 'animate-spin' : ''} />
              Regenerate
            </button>
            {editMode ? (
              <>
                <button
                  onClick={() => saveMut.mutate()}
                  disabled={saveMut.isPending}
                  className="btn-success text-xs"
                >
                  <Check size={13} />
                  {saveMut.isPending ? 'Saving…' : 'Save'}
                </button>
                <button onClick={() => setEditMode(false)} className="btn-secondary text-xs">
                  Cancel
                </button>
              </>
            ) : (
              <button onClick={() => setEditMode(true)} className="btn-secondary text-xs">
                <Edit2 size={13} /> Edit
              </button>
            )}
          </div>
          <button
            onClick={() => resendMut.mutate({ id: lead.id, channel: tabConf.channel })}
            disabled={resendMut.isPending || !hasContent}
            className="btn-primary text-xs"
          >
            <Send size={13} className={resendMut.isPending ? 'opacity-50' : ''} />
            {resendMut.isPending ? 'Sending…' : `Send ${tab === 'whatsapp' ? 'WhatsApp' : tab === 'email' ? 'Email' : 'Follow-up'}`}
          </button>
        </div>
      </div>
    </div>
  )
}
