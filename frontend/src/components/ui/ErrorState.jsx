import { AlertTriangle, RefreshCw } from 'lucide-react'
import clsx from 'clsx'

// Standard "this API call failed" panel — every query that can fail should
// render this (with its own refetch) instead of silently showing stale/empty data.
export default function ErrorState({ message, onRetry, retrying, className }) {
  return (
    <div className={clsx(
      'flex flex-col items-center justify-center text-center py-10 px-6 rounded-xl',
      'bg-red-950/20 border border-red-900/40',
      className,
    )}>
      <div className="w-11 h-11 rounded-xl bg-red-500/10 border border-red-500/30 flex items-center justify-center mb-3">
        <AlertTriangle size={18} className="text-red-400" />
      </div>
      <p className="text-sm font-medium text-red-300">Something went wrong</p>
      <p className="text-xs text-slate-500 mt-1 max-w-sm">
        {message || 'Failed to load this data.'}
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          disabled={retrying}
          className="btn-secondary text-xs mt-4"
        >
          <RefreshCw size={12} className={clsx(retrying && 'animate-spin')} />
          {retrying ? 'Retrying…' : 'Retry'}
        </button>
      )}
    </div>
  )
}
