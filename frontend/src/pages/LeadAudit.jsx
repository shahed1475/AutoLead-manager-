import { Link, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Printer, RotateCcw, ClipboardCheck, ExternalLink, Loader2, UserSearch, Star,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { auditApi, leadsApi } from '../api/client'
import { Skeleton, SkeletonText } from '../components/ui/Skeleton'
import ErrorState from '../components/ui/ErrorState'
import EmptyState from '../components/ui/EmptyState'

// The business report for one lead: who they are, who runs it and how to
// reach them, what their website does well or badly, and what it's built
// with. Every line comes from a source the app checked itself (listing,
// their website, research); gaps say so. Printable as PDF.

function fmtDate(iso) {
  if (!iso) return ''
  const d = new Date(iso.includes('T') || iso.endsWith('Z') ? iso : `${iso.replace(' ', 'T')}Z`)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })
}
const host = (u) => (u || '').replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')

function Ext({ href, children }) {
  if (!href) return null
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline break-all">
      {children || host(href)} <ExternalLink size={10} className="shrink-0 print:hidden" />
    </a>
  )
}

function Source({ source }) {
  if (!source) return null
  return /^https?:\/\//.test(source) ? <Ext href={source} /> : <span>{source}</span>
}

function Section({ title, note, children, hidden }) {
  if (hidden) return null
  return (
    <section className="pt-8 break-inside-avoid-page">
      <h2 className="text-base font-semibold text-foreground">{title}</h2>
      {note && <p className="text-sm text-muted-foreground mt-1 max-w-[70ch]">{note}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function Fact({ label, children }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm text-foreground mt-0.5 break-words">{children}</dd>
    </div>
  )
}

const Missing = ({ children }) => <span className="text-muted-foreground">{children}</span>

// ── People & contacts ────────────────────────────────────────────────────
function Contacts({ report, onResearch, researching }) {
  const c = report.contacts
  if (!c.researched) {
    return (
      <div className="rounded-xl border border-dashed border-border p-5 print:hidden">
        <p className="text-sm text-foreground font-medium">The owner and managers haven't been looked up yet.</p>
        <p className="text-sm text-muted-foreground mt-1 max-w-[65ch]">
          Research reads their website and public listings to find who runs the business, their title and how to reach them. It runs in the background and takes a few minutes.
        </p>
        <button className="btn-primary mt-4 active:scale-[0.98]" onClick={onResearch} disabled={researching}>
          {researching ? <Loader2 size={14} className="animate-spin" /> : <UserSearch size={14} />} Find owner and contacts
        </button>
      </div>
    )
  }
  const p = c.primary
  return (
    <div className="space-y-5">
      {p && (
        <div className="rounded-xl border border-border p-5 grid gap-4 sm:grid-cols-3">
          <Fact label="Main contact">
            <span className="font-semibold">{p.name}</span>{p.title && <span className="block text-muted-foreground">{p.title}</span>}
          </Fact>
          <Fact label="Phone">
            {p.phone ? <>{p.phone}{p.phone_is_business_line && <span className="block text-xs text-muted-foreground">Main business line, not a direct number</span>}</> : <Missing>Not published</Missing>}
          </Fact>
          <Fact label="Email">{p.email || <Missing>{p.email_note}</Missing>}</Fact>
        </div>
      )}
      {c.people.length > 0 ? (
        <ul className="divide-y divide-border border-y border-border">
          {c.people.map((m) => (
            <li key={`${m.name}-${m.title}`} className="py-3 grid gap-1 sm:grid-cols-[1fr_1fr_1.2fr] sm:gap-6">
              <p className="text-sm font-medium text-foreground">{m.name}{m.is_primary && <span className="ml-2 text-xs font-normal text-primary">Main contact</span>}</p>
              <p className="text-sm text-muted-foreground">{m.title || 'Title not listed'}</p>
              <p className="text-xs text-muted-foreground">Seen on <Source source={m.source_url} /></p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">No owner or manager names were published on their website or listings.</p>
      )}
      <p className="text-xs text-muted-foreground">
        Business email: {c.business_email || c.business_email_note || 'Not published'}{c.researched_at && <> | Researched {fmtDate(c.researched_at)}</>}
      </p>
    </div>
  )
}

// ── Website checks ───────────────────────────────────────────────────────
function CheckList({ items, tone }) {
  const mark = { issue: 'text-error', pass: 'text-success', unknown: 'text-muted-foreground' }[tone]
  return (
    <ul className="divide-y divide-border border-y border-border">
      {items.map((c) => (
        <li key={c.key} className="py-3 grid grid-cols-1 sm:grid-cols-[13rem_1fr] gap-1 sm:gap-6 break-inside-avoid">
          <p className={`text-sm font-semibold ${mark}`}>{c.label}</p>
          <div className="space-y-1">
            <p className="text-sm text-foreground">{c.detail}</p>
            {c.tip && <p className="text-sm text-muted-foreground max-w-[65ch]">{c.tip}</p>}
            <p className="text-xs text-muted-foreground">Source: <Source source={c.source} /></p>
          </div>
        </li>
      ))}
    </ul>
  )
}

function Technology({ items }) {
  if (!items.length) return <p className="text-sm text-muted-foreground">No known technology was recognised on their homepage.</p>
  const groups = items.reduce((acc, t) => { (acc[t.category] ||= []).push(t); return acc }, {})
  return (
    <div className="space-y-4">
      <dl className="divide-y divide-border border-y border-border">
        {Object.entries(groups).map(([cat, list]) => (
          <div key={cat} className="py-3 grid gap-2 sm:grid-cols-[13rem_1fr] sm:gap-6">
            <dt className="text-sm text-muted-foreground">{cat}</dt>
            <dd className="flex flex-wrap gap-1.5">
              {list.map((t) => <span key={t.name} title={t.evidence} className="rounded-md border border-border px-2 py-0.5 text-sm text-foreground">{t.name}</span>)}
            </dd>
          </div>
        ))}
      </dl>
      <details className="text-xs text-muted-foreground print:hidden">
        <summary className="cursor-pointer select-none">How we know</summary>
        <ul className="mt-2 space-y-1.5">
          {items.map((t) => <li key={t.name}><span className="text-foreground">{t.name}:</span> <code className="break-all">{t.evidence}</code></li>)}
        </ul>
      </details>
    </div>
  )
}

function OnTheSite({ d }) {
  return (
    <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
      <Fact label="Page title">{d.page_title || <Missing>None</Missing>}</Fact>
      <Fact label="Search description">{d.meta_description || <Missing>None</Missing>}</Fact>
      <Fact label="Homepage size">{d.word_count} words, {d.image_count} images</Fact>
      <Fact label="Main headings">{d.headings?.length ? d.headings.slice(0, 4).join(' / ') : <Missing>None</Missing>}</Fact>
      <Fact label="Social profiles">
        {d.social_profiles?.length
          ? <span className="flex flex-col gap-0.5">{d.social_profiles.map((u) => <Ext key={u} href={u} />)}</span>
          : <Missing>No profile links on the homepage</Missing>}
      </Fact>
      <Fact label="Contact details on the page">
        {[...(d.phones_on_page || []), ...(d.emails_on_page || [])].join(', ') || <Missing>No clickable phone or email</Missing>}
      </Fact>
    </dl>
  )
}

const SUMMARY_LABELS = {
  business_summary: 'What they do', target_audience: 'Who they serve', service_level: 'Service level',
  brand_positioning: 'How they position themselves', growth_potential: 'Growth potential',
}

// ── Page ─────────────────────────────────────────────────────────────────
export default function LeadAudit() {
  const { id } = useParams()
  const qc = useQueryClient()
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['lead-audit', id],
    queryFn: () => auditApi.get(id),
    retry: false,
  })
  const run = useMutation({
    mutationFn: () => auditApi.run(id),
    onSuccess: (audit) => { qc.setQueryData(['lead-audit', id], audit); qc.invalidateQueries({ queryKey: ['leads'] }) },
    onError: (e) => toast.error(e.message || "The audit couldn't run"),
  })
  const research = useMutation({
    mutationFn: () => leadsApi.researchOne(Number(id)),
    onSuccess: () => toast.success('Research started. Come back in a few minutes and press Check again.'),
    onError: (e) => toast.error(e.message || "Research couldn't start"),
  })
  const missing = isError && error?.status === 404   // api/client.js puts the HTTP status on e.status

  const toolbar = (
    <div className="flex flex-wrap items-center justify-between gap-3 print:hidden">
      <Link to="/leads" className="btn-ghost"><ArrowLeft size={14} /> Leads</Link>
      {data && (
        <div className="flex gap-2">
          <button className="btn-secondary active:scale-[0.98]" onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isPending ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />} Check again
          </button>
          <button className="btn-primary active:scale-[0.98]" onClick={() => window.print()}><Printer size={14} /> Save as PDF</button>
        </div>
      )}
    </div>
  )

  if (isLoading) {
    return <div className="max-w-4xl mx-auto space-y-6">{toolbar}<Skeleton className="h-8 w-72" /><SkeletonText lines={8} /></div>
  }
  if (missing) {
    return (
      <div className="max-w-4xl mx-auto space-y-6">
        {toolbar}
        <EmptyState
          icon={ClipboardCheck}
          title="No report for this lead yet"
          description="The report checks their listing and website (HTTPS, phone layout, contact options, WhatsApp, booking, reviews), lists the technology the site uses, and shows the owner and contacts research found. It takes a few seconds."
          action={
            <button className="btn-primary" onClick={() => run.mutate()} disabled={run.isPending}>
              {run.isPending ? <Loader2 size={14} className="animate-spin" /> : <ClipboardCheck size={14} />} {run.isPending ? 'Checking their website…' : 'Create report'}
            </button>
          }
        />
      </div>
    )
  }
  if (isError || !data) {
    return <div className="max-w-4xl mx-auto space-y-6">{toolbar}<ErrorState message="Couldn't load the report." onRetry={refetch} retrying={isFetching} /></div>
  }

  const b = data.business || {}
  const d = data.details || {}
  const by = (s) => data.checks.filter((c) => c.status === s)
  const issues = by('issue'), passes = by('pass'), unknown = by('unknown')
  const known = passes.length + issues.length
  const place = [b.address || b.city, !b.address && b.country].filter(Boolean).join(', ')

  return (
    <div className="max-w-4xl mx-auto space-y-6 audit-print">
      {toolbar}
      <article className="bg-surface-elevated border border-border rounded-xl p-6 sm:p-10 print:border-0 print:p-0">
        <header className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-6 items-end border-b border-border pb-6">
          <div className="min-w-0">
            <p className="text-sm text-muted-foreground">Business report</p>
            <h1 className="text-2xl sm:text-3xl font-semibold text-foreground tracking-tight mt-1 break-words">{data.business_name}</h1>
            <p className="text-sm text-muted-foreground mt-2">{[b.niche, place].filter(Boolean).join(', ')}</p>
          </div>
          <div className="sm:text-right">
            <p className="text-4xl font-semibold tabular text-foreground">{data.score ?? '-'}<span className="text-lg text-muted-foreground">/100</span></p>
            <p className="text-sm text-muted-foreground">{passes.length} of {known} website checks passed</p>
          </div>
        </header>

        <dl className="grid gap-x-6 gap-y-4 grid-cols-2 sm:grid-cols-4 pt-6">
          <Fact label="Phone">{b.phone || <Missing>Not listed</Missing>}</Fact>
          <Fact label="Email">{b.email || data.contacts?.business_email || <Missing>{data.contacts?.business_email_note || 'Not listed'}</Missing>}</Fact>
          <Fact label="Website">{b.website ? <Ext href={b.website} /> : <Missing>None</Missing>}</Fact>
          <Fact label="Google rating">
            {b.rating != null ? <span className="inline-flex items-center gap-1"><Star size={12} className="text-warning" />{Number(b.rating).toFixed(1)}{b.reviews != null && <span className="text-muted-foreground">({b.reviews})</span>}</span> : <Missing>Not rated</Missing>}
          </Fact>
        </dl>

        <Section title="People and contacts" note="Who runs the business, as published on their website and listings.">
          <Contacts report={data} onResearch={() => research.mutate()} researching={research.isPending} />
        </Section>

        <Section title="What to fix" hidden={!issues.length}><CheckList items={issues} tone="issue" /></Section>
        <Section title="What's working" hidden={!passes.length}><CheckList items={passes} tone="pass" /></Section>
        <Section title="Couldn't check" hidden={!unknown.length}><CheckList items={unknown} tone="unknown" /></Section>

        <Section title="Technology on their website" hidden={!b.website} note="Recognised from the code of their homepage.">
          <Technology items={d.technology || []} />
        </Section>

        <Section title="On their website" hidden={!b.website || !d.final_url}>
          <OnTheSite d={d} />
        </Section>

        <Section title="About the business" hidden={!data.summary} note="Written by AI from their website text. Check it before quoting it.">
          <dl className="divide-y divide-border border-y border-border">
            {Object.entries(data.summary || {}).map(([k, v]) => (
              <div key={k} className="py-3 grid gap-1 sm:grid-cols-[13rem_1fr] sm:gap-6">
                <dt className="text-sm text-muted-foreground">{SUMMARY_LABELS[k] || k}</dt>
                <dd className="text-sm text-foreground max-w-[65ch]">{v}</dd>
              </div>
            ))}
          </dl>
        </Section>

        <footer className="mt-10 pt-4 border-t border-border text-xs text-muted-foreground max-w-[75ch]">
          Website checked automatically on {fmtDate(data.created_at)}. Sources: the business listing{b.source ? ` (${b.source})` : ''}, their public homepage, and HOM's research of their website and listings.
          Items that couldn't be checked are listed separately and not counted in the score.
        </footer>
      </article>
    </div>
  )
}
