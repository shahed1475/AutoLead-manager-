import { useEffect } from 'react'
import {
  X, Building2, User, Mail, Phone, Globe, MapPin, FileSearch,
  ShieldCheck, ShieldAlert, ShieldQuestion, Link2, Users, Star,
} from 'lucide-react'
import { useFocusTrap } from '../hooks/useFocusTrap'
import { SLabel } from './ui/DrawerPrimitives'

// One researched business, expanded: business + management + per-field evidence
// + research metadata. Read-only — no outreach action originates here.

const STATUS_META = {
  FOUND:             { icon: ShieldCheck,    cls: 'text-emerald-400', label: 'Found' },
  VERIFIED_BY_SOURCE:{ icon: ShieldCheck,    cls: 'text-emerald-400', label: 'Verified by source' },
  SECURE_WEB_FORM:   { icon: ShieldQuestion, cls: 'text-amber-400',   label: 'Secure web form only' },
  UNCONFIRMED:       { icon: ShieldQuestion, cls: 'text-slate-400',   label: 'Unconfirmed' },
  NOT_FOUND:         { icon: ShieldAlert,    cls: 'text-slate-500',   label: 'Not found' },
}

function StatusPill({ status }) {
  const meta = STATUS_META[status] || STATUS_META.UNCONFIRMED
  const Icon = meta.icon
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-semibold ${meta.cls}`}>
      <Icon size={11} /> {meta.label}
    </span>
  )
}

function Field({ label, value, status, fallback = 'Not found' }) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-bold uppercase tracking-widest text-slate-500">{label}</span>
        {status && <StatusPill status={status} />}
      </div>
      <p className={`text-sm mt-0.5 ${value ? 'text-slate-200' : 'text-slate-600 italic'}`}>
        {value || fallback}
      </p>
    </div>
  )
}

function EvidenceRow({ e }) {
  return (
    <li className="rounded-lg border border-slate-800 bg-slate-900/40 p-2.5 space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-slate-300">{e.field_name}</span>
        <span className="text-[10px] text-slate-500">
          {Math.round((e.confidence || 0) * 100)}% · {e.source_type}
        </span>
      </div>
      {e.snippet && <p className="text-xs text-slate-400 leading-relaxed">“{e.snippet}”</p>}
      {e.source_url && (
        <a href={e.source_url} target="_blank" rel="noreferrer"
          className="flex items-center gap-1 text-[11px] text-brand-400 hover:text-brand-300 truncate">
          <Link2 size={10} className="shrink-0" />
          <span className="truncate">{e.source_url}</span>
        </a>
      )}
      <StatusPill status={e.status} />
    </li>
  )
}

function DecisionMakerRow({ d }) {
  return (
    <li className="rounded-lg border border-slate-800 bg-slate-900/40 p-2.5 space-y-1">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm text-slate-200 flex items-center gap-1.5">
            {d.name}
            {!!d.is_primary && (
              <span className="inline-flex items-center gap-0.5 text-[9px] font-bold uppercase text-amber-300">
                <Star size={9} /> Primary
              </span>
            )}
          </p>
          <p className="text-xs text-slate-400">{d.title || '—'}</p>
        </div>
        {d.matched_title && (
          <span className="shrink-0 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-300">
            Matches “{d.matched_title}”
          </span>
        )}
      </div>
      {d.source_url && (
        <a href={d.source_url} target="_blank" rel="noreferrer"
          className="flex items-center gap-1 text-[11px] text-brand-400 hover:text-brand-300 truncate">
          <Link2 size={10} className="shrink-0" />
          <span className="truncate">{d.source_url}</span>
        </a>
      )}
    </li>
  )
}

export default function ResearchResultDrawer({ result, onClose }) {
  const ref = useFocusTrap(!!result)

  useEffect(() => {
    if (!result) return
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [result, onClose])

  if (!result) return null

  const loc = [result.city, result.state, result.country].filter(Boolean).join(', ')
  const evidence = result.evidence || []
  const decisionMakers = result.decision_makers || []

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose}>
      <div
        ref={ref}
        role="dialog" aria-modal="true" aria-label="Researched business detail"
        className="w-full max-w-md h-full overflow-y-auto bg-slate-950 border-l border-slate-800 p-5 space-y-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-bold text-slate-100">{result.business_name || 'Unnamed business'}</h2>
            {loc && <p className="text-xs text-slate-500 flex items-center gap-1 mt-0.5"><MapPin size={10} />{loc}</p>}
          </div>
          <button onClick={onClose} aria-label="Close" className="p-1.5 rounded hover:bg-slate-800 text-slate-400">
            <X size={16} />
          </button>
        </div>

        <div className="flex items-center gap-3 text-xs">
          <span className="rounded-full border border-slate-700 px-2 py-0.5 text-slate-300">
            {result.research_status}
          </span>
          <span className="text-slate-500">Confidence {Math.round((result.confidence || 0) * 100)}%</span>
        </div>

        {/* Business */}
        <section className="space-y-3">
          <SLabel icon={Building2}>Business</SLabel>
          <Field label="Phone" value={result.business_phone} />
          <Field label="Email" value={result.business_email} status={result.business_email_status} />
          <div>
            <span className="text-[10px] font-bold uppercase tracking-widest text-slate-500">Website</span>
            {result.business_website ? (
              <a href={result.business_website} target="_blank" rel="noreferrer"
                className="text-sm mt-0.5 flex items-center gap-1.5 text-brand-400 hover:text-brand-300 break-all">
                <Globe size={12} className="shrink-0" />{result.business_website}
              </a>
            ) : <p className="text-sm mt-0.5 text-slate-600 italic">Not found</p>}
          </div>
        </section>

        {/* Management */}
        <section className="space-y-3">
          <SLabel icon={User}>Management contact</SLabel>
          <Field label="Name" value={result.management_contact_name} />
          <Field label="Title" value={result.management_title} />
          <Field
            label="Phone"
            value={result.management_phone
              ? `${result.management_phone}${result.management_phone_type === 'BUSINESS' ? ' (business line)' : ''}`
              : null}
          />
          <Field label="Email" value={result.management_email} status={result.management_email_status} />
          {!result.management_contact_name && (
            <p className="text-xs text-slate-600 italic">
              No named owner/manager was publicly listed. Nothing is inferred — see evidence below for what was checked.
            </p>
          )}
        </section>

        {/* Decision makers */}
        {decisionMakers.length > 0 && (
          <section className="space-y-2">
            <SLabel icon={Users}>Decision makers ({decisionMakers.length})</SLabel>
            <p className="text-[11px] text-slate-500">
              Everyone named with a role on the business's public pages. Emails aren't guessed.
            </p>
            <ul className="space-y-2">
              {decisionMakers.map((d) => <DecisionMakerRow key={d.id ?? d.name} d={d} />)}
            </ul>
          </section>
        )}

        {/* Research */}
        <section className="space-y-2">
          <SLabel icon={FileSearch}>Research</SLabel>
          {result.research_notes && <p className="text-xs text-slate-400 leading-relaxed">{result.research_notes}</p>}
          {result.lead_id && (
            <p className="text-[11px] text-slate-500">Merged into Leads as lead #{result.lead_id}</p>
          )}
        </section>

        {/* Evidence */}
        <section className="space-y-2">
          <SLabel icon={Link2}>Evidence ({evidence.length})</SLabel>
          {evidence.length === 0 ? (
            <p className="text-xs text-slate-600 italic">No evidence rows recorded for this result.</p>
          ) : (
            <ul className="space-y-2">
              {evidence.map((e, i) => <EvidenceRow key={i} e={e} />)}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}
