import clsx from 'clsx'

// Base shimmer block — compose the variants below from this rather than
// hand-rolling `animate-pulse bg-slate-800` divs on every page.
export function Skeleton({ className }) {
  return <div className={clsx('animate-pulse rounded-md bg-slate-800/80', className)} />
}

export function SkeletonText({ lines = 1, className }) {
  return (
    <div className={clsx('space-y-2', className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={clsx('h-3', i === lines - 1 && lines > 1 ? 'w-2/3' : 'w-full')} />
      ))}
    </div>
  )
}

// Matches StatCard's default (stacked) layout dimensions.
export function SkeletonStatCard() {
  return (
    <div className="card p-5 border border-slate-700/30">
      <div className="flex items-start justify-between">
        <div className="space-y-2 flex-1">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-7 w-16" />
        </div>
        <Skeleton className="w-10 h-10 rounded-lg" />
      </div>
    </div>
  )
}

export function SkeletonCard({ className }) {
  return (
    <div className={clsx('card p-5 space-y-3', className)}>
      <Skeleton className="h-4 w-1/3" />
      <SkeletonText lines={3} />
    </div>
  )
}

export function SkeletonTableRows({ rows = 6, cols = 6 }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className="border-b border-slate-800/60">
          {Array.from({ length: cols }).map((__, c) => (
            <td key={c} className="px-4 py-3">
              <Skeleton className="h-3.5 w-full max-w-[140px]" />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}

// Full-page fallback for React.lazy(Suspense) route transitions.
export function PageSkeleton() {
  return (
    <div className="p-6 space-y-5">
      <Skeleton className="h-6 w-48" />
      <div className="grid grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => <SkeletonStatCard key={i} />)}
      </div>
      <SkeletonCard />
    </div>
  )
}
