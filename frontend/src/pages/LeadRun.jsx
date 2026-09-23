import { useParams, Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Zap, ScanSearch, PenLine, Check, Square, Download, RefreshCw, Users, Send, Globe } from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { leadRunsApi } from '../api/client'
import BackToFindLeads from '../components/BackToFindLeads'
import ScoreBadge from '../components/ScoreBadge'
import ErrorState from '../components/ui/ErrorState'
import { SkeletonTableRows } from '../components/ui/Skeleton'
import { stepsLabel, RUN_ACTIVE } from '../lib/leadRuns'

// One Find leads run: live step progress, then the results with next steps.
// Drafts are reviewed and sent in AI Lab — this page never sends.
const STEP_META = {
  collect:  { icon: Zap,        label: 'Find leads',     stage: 0 },
  research: { icon: ScanSearch, label: 'Deep research',  stage: 1 },
  outreach: { icon: PenLine,    label: 'Write outreach', stage: 2 },
}
const STAGE_INDEX = { COLLECTING: 0, RESEARCHING: 1, WRITING: 2, DONE: 3 }
const STATUS_LABEL = {
  QUEUED: 'Queued', RUNNING: 'Running', CANCEL_REQUESTED: 'Stopping…',
  COMPLETED: 'Finished', FAILED: 'Stopped with an error', CANCELLED: 'Cancelled',
}

function host(url) {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return url }
}

function stepFigure(id, run) {
  if (id === 'collect') return `${run.leads_found} of ${run.target_count} found`
  if (id === 'research') return `${run.leads_researched} of ${run.leads_found || run.target_count} researched`
  return `${run.drafts_written} drafted${run.drafts_skipped ? `, ${run.drafts_skipped} skipped` : ''}`
}

// Why a lead has no draft — so a skipped row explains itself.
function draftSkipReason(run, lead, active) {
  if (active && run.stage !== 'DONE') return 'Not written yet'
  if (run.channel === 'EMAIL' && !lead.email) return 'No email found'
  if (run.channel === 'WHATSAPP' && !lead.phone) return 'No phone found'
  if (!lead.email && !lead.phone) return 'No contact found'
  if (['SENT', 'REPLIED', 'SKIPPED', 'DO_NOT_CONTACT'].includes((lead.status || '').toUpperCase())) return 'Already contacted'
  if (run.hot_warm_only && (lead.score_label || '').toUpperCase() === 'COLD') return 'Cold lead'
  return '—'
}

function csvCell(v) {
  const s = v == null ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

function exportCsv(run, leads) {
  const header = ['Business', 'Website', 'Email', 'Phone', 'Decision maker', 'Title', 'Score', 'Draft ready']
  const rows = leads.map((l) => {
    const dm = l.decision_makers?.[0]
    return [l.business_name, l.website, l.email, l.phone, dm?.name, dm?.title,
      l.score_label ? `${l.score_label} ${l.score ?? ''}`.trim() : '', l.has_draft ? 'yes' : 'no']
  })
  const csv = [header, ...rows].map((r) => r.map(csvCell).join(',')).join('\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `leads-${run.niche}-${run.location}.csv`.replace(/\s+/g, '-').toLowerCase()
  a.click()
  URL.revokeObjectURL(url)
}

export default function LeadRun() {
  const { runId } = useParams()
  const qc = useQueryClient()

  const runQuery = useQuery({
    queryKey: ['lead-run', runId],
    queryFn: () => leadRunsApi.get(runId),
    retry: false,
    refetchInterval: (q) => (RUN_ACTIVE.has(q.state.data?.status) ? 2500 : false),
  })
  const run = runQuery.data
  const active = run && RUN_ACTIVE.has(run.status)

  const resultsQuery = useQuery({
    queryKey: ['lead-run-results', runId],
    queryFn: () => leadRunsApi.results(runId),
    enabled: !!run,
    refetchInterval: active ? 5000 : false,
  })
  const leads = resultsQuery.data?.leads || []

  const cancel = useMutation({
    mutationFn: () => leadRunsApi.cancel(runId),
    onSuccess: () => { toast.success('Stopping after the current step'); qc.invalidateQueries({ queryKey: ['lead-run', runId] }) },
    onError: (e) => toast.error(e.message || 'Could not cancel'),
  })

  if (runQuery.isError) {
    return (
      <div className="px-4 sm:px-8 py-8 max-w-6xl mx-auto space-y-6">
        <BackToFindLeads />
        <ErrorState message="This run could not be found." onRetry={runQuery.refetch} />
      </div>
    )
  }
  if (!run) {
    return <div className="px-4 sm:px-8 py-8 max-w-6xl mx-auto"><div className="h-40 surface-subtle animate-pulse" /></div>
  }

  const current = STAGE_INDEX[run.stage] ?? -1
  const showResearch = run.steps.includes('research')
  const showOutreach = run.steps.includes('outreach')
  const drafts = leads.filter((l) => l.has_draft).length

  function stepState(id) {
    const idx = STEP_META[id].stage
    if (run.status === 'COMPLETED') return 'done'
    if (idx < current) return 'done'
    if (idx === current) return active ? 'active' : 'stopped'
    return 'pending'
  }

  return (
    <div className="px-4 sm:px-8 py-8 max-w-6xl mx-auto space-y-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <BackToFindLeads />
          <h1 className="text-page truncate">{run.niche} in {run.location}</h1>
          <p className="text-support mt-1">{stepsLabel(run.steps)} · {run.target_count} leads</p>
        </div>
        <div className="flex items-center gap-4">
          <p className="flex items-center gap-2 text-sm">
            <span className={clsx('w-2 h-2 rounded-full',
              active ? 'bg-success animate-pulse' : run.status === 'COMPLETED' ? 'bg-primary'
                : run.status === 'FAILED' ? 'bg-error' : 'bg-slate-500')} />
            <span className="font-semibold">{STATUS_LABEL[run.status] || run.status}</span>
          </p>
          {active && run.status !== 'CANCEL_REQUESTED' && (
            <button className="btn-secondary h-9" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
              <Square size={13} /> Stop
            </button>
          )}
        </div>
      </header>

      {run.status === 'FAILED' && (
        <div className="rounded-xl bg-error/10 px-4 py-3 text-sm">
          <p className="font-semibold text-foreground">The run stopped before finishing</p>
          <p className="text-muted-foreground mt-0.5">{run.error_message || 'Something went wrong.'} Leads found so far are kept below.</p>
        </div>
      )}

      {/* Step progress */}
      <section>
        <ol className={clsx('grid gap-4', run.steps.length === 1 ? 'grid-cols-1 max-w-sm' : run.steps.length === 2 ? 'grid-cols-2' : 'grid-cols-3')}>
          {run.steps.map((id) => {
            const { icon: Icon, label } = STEP_META[id]
            const state = stepState(id)
            return (
              <li key={id} className="min-w-0">
                <div className={clsx('h-1 rounded-full transition-colors duration-500',
                  state === 'done' ? 'bg-primary' : state === 'active' ? 'bg-primary/60 animate-pulse'
                    : state === 'stopped' ? 'bg-error/60' : 'bg-secondary')} />
                <div className="mt-3 flex items-center gap-1.5">
                  {state === 'done'
                    ? <Check size={15} strokeWidth={2.4} className="text-primary shrink-0" />
                    : <Icon size={15} strokeWidth={1.9} className={clsx('shrink-0', state === 'active' ? 'text-primary' : 'text-muted-foreground')} />}
                  <span className={clsx('text-sm truncate', state === 'pending' ? 'text-muted-foreground' : 'font-semibold')}>{label}</span>
                </div>
                <p className="text-meta tabular mt-0.5">{state === 'pending' ? 'Waiting' : stepFigure(id, run)}</p>
                {state === 'active' && run.current_item && (
                  <p className="text-meta truncate mt-0.5">Working on <span className="text-foreground">{run.current_item}</span></p>
                )}
              </li>
            )
          })}
        </ol>
      </section>

      {/* Next steps */}
      {!active && leads.length > 0 && (
        <section className="flex flex-wrap items-center gap-3">
          {showOutreach && drafts > 0 && (
            <Link to="/ai-lab" className="btn-primary h-10">
              <Send size={15} /> Review &amp; send {drafts} draft{drafts === 1 ? '' : 's'}
            </Link>
          )}
          <button className="btn-secondary h-10" onClick={() => exportCsv(run, leads)}>
            <Download size={15} /> Export CSV
          </button>
          <Link to="/leads" className="btn-ghost h-10"><Users size={15} /> Open in Leads</Link>
          {showOutreach && (
            <p className="text-meta w-full">Drafts wait in AI Lab. Nothing has been sent.</p>
          )}
        </section>
      )}

      {/* Results */}
      <section>
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="text-section">Leads</h2>
          <span className="text-meta tabular">{leads.length}</span>
        </div>
        {resultsQuery.isLoading ? (
          <table className="w-full"><tbody><SkeletonTableRows rows={4} cols={5} /></tbody></table>
        ) : leads.length === 0 ? (
          <div className="py-10 border-y border-border-subtle">
            {active ? (
              <p className="text-support flex items-center gap-2"><RefreshCw size={14} className="animate-spin" /> Leads will appear here as they're found.</p>
            ) : (
              <>
                <p className="text-subheading">No leads found</p>
                <p className="text-support mt-1">Try a broader place, or a more common way to describe the business.</p>
              </>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto -mx-4 sm:mx-0">
            <table className="w-full text-sm min-w-[720px]">
              <thead>
                <tr className="text-left text-meta border-b border-border-subtle">
                  <th className="font-medium py-2.5 pl-4 sm:pl-0 pr-4">Business</th>
                  <th className="font-medium py-2.5 pr-4">Contact</th>
                  {showResearch && <th className="font-medium py-2.5 pr-4">Decision maker</th>}
                  <th className="font-medium py-2.5 pr-4">Score</th>
                  {showOutreach && <th className="font-medium py-2.5 pr-4">Draft</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {leads.map((l) => {
                  const dm = l.decision_makers?.[0]
                  return (
                    <tr key={l.id} className="align-top">
                      <td className="py-3 pl-4 sm:pl-0 pr-4">
                        <p className="font-medium text-foreground">{l.business_name}</p>
                        {l.website && (
                          <a href={l.website} target="_blank" rel="noreferrer"
                            className="text-meta hover:text-primary inline-flex items-center gap-1">
                            <Globe size={11} />{host(l.website)}
                          </a>
                        )}
                      </td>
                      <td className="py-3 pr-4 text-muted-foreground">
                        {l.email && <p className="text-foreground">{l.email}</p>}
                        {l.phone && <p className="tabular">{l.phone}</p>}
                        {!l.email && !l.phone && <p>Not found</p>}
                      </td>
                      {showResearch && (
                        <td className="py-3 pr-4">
                          {dm ? (
                            <>
                              <p className="text-foreground">{dm.name}</p>
                              <p className="text-meta">{dm.title}{l.decision_makers.length > 1 ? ` · +${l.decision_makers.length - 1} more` : ''}</p>
                            </>
                          ) : (
                            <p className="text-meta">{l.research_status ? 'Not listed publicly' : 'Not researched yet'}</p>
                          )}
                        </td>
                      )}
                      <td className="py-3 pr-4">
                        {l.score ? <ScoreBadge score={l.score} label={l.score_label} /> : <span className="text-meta">—</span>}
                      </td>
                      {showOutreach && (
                        <td className="py-3 pr-4">
                          {l.has_draft
                            ? <span className="badge bg-primary/10 text-primary">Ready to review</span>
                            : <span className="text-meta">{draftSkipReason(run, l, active)}</span>}
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
