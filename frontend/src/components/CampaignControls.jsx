import { useState } from 'react'
import { Send, RefreshCw, MailOpen, MessageSquare, Users, CheckCircle2 } from 'lucide-react'
import { campaignApi, aiApi } from '../api/client'
import toast from 'react-hot-toast'

export default function CampaignControls({ selectedIds = [], onComplete }) {
  const [loading, setLoading] = useState(null)

  const run = async (label, fn) => {
    setLoading(label)
    try {
      await fn()
      toast.success(`${label} completed`)
      onComplete?.()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setLoading(null)
    }
  }

  const noSelection = selectedIds.length === 0
  const count = selectedIds.length

  return (
    <div className="card p-4 space-y-4">
      <div className="flex items-center gap-2">
        <Users size={15} className="text-slate-400" />
        <h3 className="text-sm font-semibold text-slate-200">Campaign Controls</h3>
        {count > 0 && (
          <span className="ml-auto text-xs bg-brand-600/20 text-brand-400 border border-brand-600/30 rounded-full px-2 py-0.5">
            {count} selected
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <button
          disabled={noSelection || loading !== null}
          onClick={() => run('AI Generate', () => aiApi.generateBulk(selectedIds))}
          className="btn-primary text-xs py-2 justify-center disabled:opacity-50"
        >
          {loading === 'AI Generate' ? (
            <RefreshCw size={12} className="animate-spin" />
          ) : (
            <RefreshCw size={12} />
          )}
          Generate AI Msgs
        </button>

        <button
          disabled={noSelection || loading !== null}
          onClick={() => run('Send Email', () => campaignApi.bulkSend(selectedIds, 'EMAIL'))}
          className="btn-secondary text-xs py-2 justify-center"
        >
          {loading === 'Send Email' ? (
            <RefreshCw size={12} className="animate-spin" />
          ) : (
            <MailOpen size={12} />
          )}
          Send Emails
        </button>

        <button
          disabled={noSelection || loading !== null}
          onClick={() => run('Send WhatsApp', () => campaignApi.bulkSend(selectedIds, 'WHATSAPP'))}
          className="btn-success text-xs py-2 justify-center"
        >
          {loading === 'Send WhatsApp' ? (
            <RefreshCw size={12} className="animate-spin" />
          ) : (
            <MessageSquare size={12} />
          )}
          Send WhatsApp
        </button>

        <button
          disabled={noSelection || loading !== null}
          onClick={() => run('Send Both', () => campaignApi.bulkSend(selectedIds, 'BOTH'))}
          className="btn-primary text-xs py-2 justify-center"
        >
          {loading === 'Send Both' ? (
            <RefreshCw size={12} className="animate-spin" />
          ) : (
            <Send size={12} />
          )}
          Send Both
        </button>

        <button
          disabled={noSelection || loading !== null}
          onClick={() => run('Mark Replied', () =>
            Promise.all(selectedIds.map((id) => campaignApi.markReplied(id)))
          )}
          className="btn-success text-xs py-2 justify-center col-span-2"
        >
          {loading === 'Mark Replied' ? (
            <RefreshCw size={12} className="animate-spin" />
          ) : (
            <CheckCircle2 size={12} />
          )}
          Mark as Replied
        </button>
      </div>

      {noSelection && (
        <p className="text-xs text-slate-600 text-center">
          Select leads from the table to enable actions
        </p>
      )}
    </div>
  )
}
