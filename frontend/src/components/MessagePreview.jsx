import { useState } from 'react'
import { Edit3, Save, X } from 'lucide-react'
import clsx from 'clsx'

export default function MessagePreview({ label, value, onSave, placeholder, multiline = true }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value || '')

  const handleSave = () => {
    onSave?.(draft)
    setEditing(false)
  }

  const handleCancel = () => {
    setDraft(value || '')
    setEditing(false)
  }

  return (
    <div className="space-y-1.5">
      {label && (
        <div className="flex items-center justify-between">
          <span className="label">{label}</span>
          {!editing && value && (
            <button
              onClick={() => setEditing(true)}
              className="text-xs text-slate-500 hover:text-brand-400 flex items-center gap-1 transition-colors"
            >
              <Edit3 size={11} /> Edit
            </button>
          )}
        </div>
      )}
      {editing ? (
        <div className="space-y-2">
          {multiline ? (
            <textarea
              rows={6}
              className="input resize-none"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              autoFocus
            />
          ) : (
            <input
              type="text"
              className="input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              autoFocus
            />
          )}
          <div className="flex gap-2">
            <button onClick={handleSave} className="btn-primary text-xs px-3 py-1.5 gap-1.5">
              <Save size={12} /> Save
            </button>
            <button onClick={handleCancel} className="btn-secondary text-xs px-3 py-1.5 gap-1.5">
              <X size={12} /> Cancel
            </button>
          </div>
        </div>
      ) : (
        <div
          className={clsx(
            'rounded-lg p-3 text-sm leading-relaxed',
            value
              ? 'bg-slate-900/60 border border-slate-700/50 text-slate-300'
              : 'bg-slate-900/30 border border-dashed border-slate-700 text-slate-600 italic'
          )}
        >
          {value || placeholder || 'Not generated yet'}
        </div>
      )}
    </div>
  )
}
