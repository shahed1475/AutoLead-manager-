import { Link, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Printer, RotateCcw, ClipboardCheck, ExternalLink, Loader2 } from 'lucide-react'
import { auditApi } from '../api/client'
import { Skeleton, SkeletonText } from '../components/ui/Skeleton'
import ErrorState from '../components/ui/ErrorState'
import EmptyState from '../components/ui/EmptyState'

// A one-page audit of a lead's online presence, meant to be saved as PDF and
// attached to outreach. Every line is a check the app ran itself, with its
// source; anything it couldn't check is listed separately, never counted.

function fmtDate(iso) {
  if (!iso) return ''
  const d = new Date(iso.includes('T') || iso.endsWith('Z') ? iso : `${iso.replace(' ', 'T')}Z`)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })
}

function Source({ source }) {
  if (!source) return null
  return /^https?:\/\//.test(source)
    ? <a href={source} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:underline">{source.replace(/^https?:\/\//, '').replace(/\/$/, '')} <ExternalLink size={10} className="print:hidden" /></a>
    : <span>{source}</span>
}

function CheckList({ items, tone }) {
  const mark = { issue: 'text-error', pass: 'text-success', unknown: 'text-muted-foreground' }[tone]
  return (
    <ul className="divide-y divide-border">
      {items.map((c) => (
        <li key={c.key} className="py-3 grid grid-cols-1 sm:grid-cols-[14rem_1fr] gap-1 sm:gap-6 break-inside-avoid">
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

function Section({ title, count, children }) {
  if (!count) return null
  return (
    <section className="mt-8 break-inside-avoid-page">
      <h2 className="text-base font-semibold text-foreground">{title} <span className="text-muted-foreground font-normal tabular">({count})</span></h2>
      <div className="mt-2 border-t border-border">{children}</div>
    </section>
  )
}

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
    onSuccess: (audit) => qc.setQueryData(['lead-audit', id], audit),
  })
  const missing = isError && error?.response?.status === 404

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
    return (
      <div className="max-w-4xl mx-auto space-y-6">
        {toolbar}
        <Skeleton className="h-8 w-72" />
        <SkeletonText lines={6} />
      </div>
    )
  }
  if (missing) {
    return (
      <div className="max-w-4xl mx-auto space-y-6">
        {toolbar}
        <EmptyState
          icon={ClipboardCheck}
          title="No audit for this lead yet"
          description="The audit checks the business listing and homepage: website, HTTPS, phone layout, contact options, WhatsApp, booking, reviews. It takes a few seconds."
          action={
            <button className="btn-primary" onClick={() => run.mutate()} disabled={run.isPending}>
              {run.isPending ? <Loader2 size={14} className="animate-spin" /> : <ClipboardCheck size={14} />} Run audit
            </button>
          }
        />
        {run.isError && <ErrorState message="The audit couldn't run. Try again." onRetry={() => run.mutate()} />}
      </div>
    )
  }
  if (isError || !data) {
    return <div className="max-w-4xl mx-auto space-y-6">{toolbar}<ErrorState message="Couldn't load the audit." onRetry={refetch} retrying={isFetching} /></div>
  }

  const by = (s) => data.checks.filter((c) => c.status === s)
  const issues = by('issue'), passes = by('pass'), unknown = by('unknown')
  const known = passes.length + issues.length

  return (
    <div className="max-w-4xl mx-auto space-y-6 audit-print">
      {toolbar}
      <article className="bg-surface-elevated border border-border rounded-xl p-6 sm:p-10 print:border-0 print:p-0">
        <header className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-6 items-end border-b border-border pb-6">
          <div className="min-w-0">
            <p className="text-sm text-muted-foreground">Online presence audit</p>
            <h1 className="text-2xl sm:text-3xl font-semibold text-foreground tracking-tight mt-1 break-words">{data.business_name}</h1>
            <p className="text-sm text-muted-foreground mt-2">
              {[data.niche, data.city].filter(Boolean).join(', ')}
              {data.website && <>{data.niche || data.city ? ' | ' : ''}<Source source={data.website} /></>}
            </p>
          </div>
          <div className="sm:text-right">
            <p className="text-4xl font-semibold tabular text-foreground">{data.score ?? '-'}<span className="text-lg text-muted-foreground">/100</span></p>
            <p className="text-sm text-muted-foreground">{passes.length} of {known} checks passed</p>
          </div>
        </header>

        <Section title="What to fix" count={issues.length}><CheckList items={issues} tone="issue" /></Section>
        <Section title="What's working" count={passes.length}><CheckList items={passes} tone="pass" /></Section>
        <Section title="Couldn't check" count={unknown.length}><CheckList items={unknown} tone="unknown" /></Section>

        <footer className="mt-10 pt-4 border-t border-border text-xs text-muted-foreground max-w-[75ch]">
          Checked automatically on {fmtDate(data.created_at)} from the business listing and the public homepage.
          Items that couldn't be checked are listed separately and not counted in the score.
        </footer>
      </article>
    </div>
  )
}
