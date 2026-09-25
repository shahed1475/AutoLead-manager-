import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { statsApi } from '../api/client'
import { Skeleton } from './ui/Skeleton'
import ErrorState from './ui/ErrorState'

// What the work produced this period, next to the period before, so an
// owner (or a client in their workspace) sees results, not just activity.
const ROWS = [
  ['leads_found', 'Leads found'],
  ['messages_sent', 'Messages sent'],
  ['replies', 'Replies'],
  ['interested', 'Interested'],
  ['meetings', 'Meetings'],
  ['won', 'Won'],
]

function Change({ now, before, days }) {
  const d = now - before
  if (!now && !before) return <span className="text-meta">None in the last {days} days</span>
  return (
    <span className={clsx('text-meta', d > 0 && 'text-success', d < 0 && 'text-error')}>
      {d === 0 ? 'Same as' : `${d > 0 ? '+' : ''}${d} vs`} the {days} days before
    </span>
  )
}

export default function ResultsCard() {
  const [days, setDays] = useState(7)
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['stats-results', days],
    queryFn: () => statsApi.results(days),
    refetchInterval: 60_000,
  })

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-section">Results</h2>
        <div className="inline-flex rounded-lg border border-border p-0.5" role="group" aria-label="Period">
          {[7, 30].map((d) => (
            <button key={d} type="button" onClick={() => setDays(d)} aria-pressed={days === d}
              className={clsx('px-3 py-1 text-xs font-semibold rounded-md', days === d ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:text-foreground')}>
              {d} days
            </button>
          ))}
        </div>
      </div>
      {isError ? (
        <ErrorState message="Couldn't load results." onRetry={refetch} retrying={isFetching} />
      ) : (
        <dl className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-x-6 gap-y-5">
          {ROWS.map(([key, label]) => (
            <div key={key}>
              <dt className="text-meta">{label}</dt>
              {isLoading ? <Skeleton className="h-8 w-14 mt-1.5" /> : (
                <>
                  <dd className="text-2xl font-semibold tabular mt-1">{data.current[key]}</dd>
                  <dd><Change now={data.current[key]} before={data.previous[key]} days={days} /></dd>
                </>
              )}
            </div>
          ))}
        </dl>
      )}
    </section>
  )
}
