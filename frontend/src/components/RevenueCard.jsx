import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { statsApi } from '../api/client'
import { fmtMoney } from '../lib/money'
import { Skeleton } from './ui/Skeleton'

// Where the money comes from: won revenue by the source each lead was found
// in, and the latest wins. Only values the owner recorded on won deals.
const SOURCE_LABEL = {
  GOOGLE_MAPS: 'Google Maps', CSV_IMPORT: 'Imported file', MANUAL: 'Added by hand', UNKNOWN: 'Unknown source',
  WHATSAPP_INBOUND: 'WhatsApp message', YELP: 'Yelp', YELLOW_PAGES: 'Yellow Pages',
}
const label = (s) => SOURCE_LABEL[s] || s.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase())

export default function RevenueCard({ stats }) {
  const { data, isLoading } = useQuery({ queryKey: ['stats-revenue'], queryFn: statsApi.revenue, refetchInterval: 60_000 })
  const cur = data?.currency || stats?.currency || 'USD'

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-section">Revenue</h2>
        <Link to="/pipeline" className="text-meta hover:text-foreground">Open pipeline</Link>
      </div>
      {isLoading ? <Skeleton className="h-24 w-full" /> : !stats?.deals_won ? (
        <div className="rounded-xl border border-dashed border-border p-5">
          <p className="text-sm font-medium text-foreground">No won deals recorded yet</p>
          <p className="text-sm text-muted-foreground mt-1 max-w-[65ch]">
            Revenue here is only money you record. When a client says yes, drag the lead to <span className="text-foreground">Won</span> in the Pipeline and enter what the deal is worth. Replies and meetings never count as revenue.
          </p>
        </div>
      ) : (
        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-5">
            <div><dt className="text-meta">Revenue won</dt><dd className="text-2xl font-semibold tabular mt-1">{fmtMoney(stats.revenue_won, cur)}</dd></div>
            <div><dt className="text-meta">Deals won</dt><dd className="text-2xl font-semibold tabular mt-1">{stats.deals_won}</dd></div>
            <div><dt className="text-meta">Average deal</dt><dd className="text-2xl font-semibold tabular mt-1">{fmtMoney(stats.avg_won_deal, cur)}</dd></div>
            <div><dt className="text-meta">Win rate</dt><dd className="text-2xl font-semibold tabular mt-1">{stats.win_rate == null ? '-' : `${stats.win_rate}%`}</dd><dd className="text-meta">Won out of won + lost</dd></div>
            <div className="col-span-2"><dt className="text-meta">Open pipeline</dt><dd className="text-lg font-semibold tabular mt-1">{fmtMoney(stats.open_pipeline_value, cur)}</dd><dd className="text-meta">{stats.open_deals} open deal(s) in Interested, Meeting and Proposal; only values you entered</dd></div>
          </dl>
          <div className="space-y-5">
            <div>
              <p className="text-meta mb-2">By where the lead came from</p>
              <ul className="divide-y divide-border border-y border-border">
                {data.by_source.map((r) => (
                  <li key={r.source} className="py-2 flex items-center justify-between gap-3 text-sm">
                    <span className="text-foreground">{label(r.source)}</span>
                    <span className="tabular text-muted-foreground">{r.deals} deal(s) <span className="text-foreground font-semibold ml-2">{fmtMoney(r.revenue, cur)}</span></span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="text-meta mb-2">Latest wins</p>
              <ul className="space-y-1.5">
                {data.recent_wins.map((w) => (
                  <li key={w.id} className="flex items-center justify-between gap-3 text-sm">
                    <span className="truncate text-foreground">{w.business_name}</span>
                    <span className="tabular font-semibold shrink-0">{w.deal_value == null ? <span className="text-muted-foreground font-normal">No value</span> : fmtMoney(w.deal_value, cur)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
