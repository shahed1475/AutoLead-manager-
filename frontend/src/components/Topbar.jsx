import { useQuery } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'
import { Zap, Wifi, WifiOff, Clock, Activity } from 'lucide-react'
import { engineApi } from '../api/client'
import clsx from 'clsx'

const PAGE_TITLES = {
  '/dashboard': 'Dashboard',
  '/leads':     'Leads',
  '/campaign':  'Campaign',
  '/ai-lab':    'AI Lab',
  '/settings':  'Settings',
}

function EngineStatus({ status }) {
  if (!status) {
    return (
      <div className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium
                      bg-slate-700/30 border border-slate-700/50 text-slate-500">
        <div className="w-1.5 h-1.5 rounded-full bg-slate-600" />
        Connecting...
      </div>
    )
  }

  const isScraping = status.scraper_running
  const isRunning  = status.scheduler_running

  const cfg = isScraping
    ? { cls: 'bg-amber-500/10 border-amber-500/30 text-amber-400', dot: 'bg-amber-400 animate-pulse', label: 'SCRAPING' }
    : isRunning
      ? { cls: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400', dot: 'bg-emerald-400 animate-pulse', label: 'RUNNING' }
      : { cls: 'bg-red-500/10 border-red-500/30 text-red-400', dot: 'bg-red-500', label: 'STOPPED' }

  return (
    <div className={clsx('flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border', cfg.cls)}>
      <div className={clsx('w-1.5 h-1.5 rounded-full', cfg.dot)} />
      Engine: {cfg.label}
    </div>
  )
}

export default function Topbar() {
  const { pathname } = useLocation()
  const pageTitle = PAGE_TITLES[pathname] || 'Dashboard'

  const { data: engineStatus } = useQuery({
    queryKey: ['engine-status'],
    queryFn: engineApi.status,
    refetchInterval: 5_000,
    retry: false,
  })

  const nextRun = engineStatus?.next_run
    ? new Date(engineStatus.next_run).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : null

  return (
    <header className="h-12 shrink-0 flex items-center px-5 gap-4
                       bg-slate-900/90 border-b border-slate-800 backdrop-blur-sm z-10">

      {/* Brand */}
      <div className="flex items-center gap-2">
        <div className="w-6 h-6 bg-brand-600 rounded-md flex items-center justify-center shadow-lg shadow-brand-900/40">
          <Zap size={11} className="text-white" />
        </div>
        <span className="font-bold text-sm text-slate-100 tracking-tight">MarketBot</span>
        <span className="hidden sm:block text-slate-700 select-none">/</span>
        <span className="hidden sm:block text-sm text-slate-400">{pageTitle}</span>
      </div>

      <div className="flex-1" />

      {/* Next run */}
      {nextRun && !engineStatus?.scraper_running && (
        <div className="hidden md:flex items-center gap-1.5 text-xs text-slate-500">
          <Clock size={11} />
          Next run {nextRun} UTC
        </div>
      )}

      {/* Scraper progress pill */}
      {engineStatus?.scraper_running && engineStatus.scraper_total > 0 && (
        <div className="hidden md:flex items-center gap-2 px-3 py-1 rounded-full
                        bg-amber-500/10 border border-amber-500/30 text-xs text-amber-400">
          <Activity size={11} className="animate-pulse" />
          {engineStatus.scraper_progress} / {engineStatus.scraper_total} scraped
        </div>
      )}

      {/* Engine status badge */}
      <EngineStatus status={engineStatus} />
    </header>
  )
}
