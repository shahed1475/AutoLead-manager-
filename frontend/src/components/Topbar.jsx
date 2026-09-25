import { useQuery } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'
import { Clock, Activity, Sun, Moon, Menu, LogOut } from 'lucide-react'
import { useTheme } from '../lib/theme'
import { LogoMark } from './Logo'
import { engineApi } from '../api/client'
import clsx from 'clsx'
import { setSessionToken } from '../api/client'
import { CLIENT_SIGN_OUT, isClientEdition } from '../lib/edition'

const PAGE_TITLES = {
  '/dashboard': 'Overview',
  '/leads':     'Leads',
  '/pipeline':  'Pipeline',
  '/lead-search':            'Find leads',
  '/lead-search/automation': 'Daily automation',
  '/lead-search/manual':     'Quick search',
  '/research-agent':         'Deep research',
  '/campaign':  'Outreach campaign',
  '/email-campaigns':        'Email Campaigns',
  '/ai-lab':    'AI Lab',
  '/inbox':     'Inbox',
  '/settings':  'Settings',
  '/clients':   'Clients',
  '/whatsapp':  'WhatsApp',
  '/social':    'Social media',
}

function EngineStatus({ status }) {
  const cfg = !status
    ? { dot: 'bg-slate-500', label: 'Connecting…' }
    : status.scraper_running
      ? { dot: 'bg-warning animate-pulse', label: 'Scraping' }
      : status.scheduler_running
        ? { dot: 'bg-success', label: 'Engine running' }
        : { dot: 'bg-error', label: 'Engine stopped' }
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
      <span className={clsx('w-1.5 h-1.5 rounded-full', cfg.dot)} />
      <span className="hidden sm:inline">{cfg.label}</span>
    </div>
  )
}

// Day / night switch: a segmented control, the active option raised.
function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const opt = (value, Icon, label) => (
    <button
      type="button"
      onClick={() => setTheme(value)}
      aria-pressed={theme === value}
      aria-label={label}
      title={label}
      className={clsx(
        'grid place-items-center w-7 h-6 rounded-md transition-all',
        theme === value
          ? 'bg-surface-elevated text-foreground shadow-sm ring-1 ring-border-subtle'
          : 'text-muted-foreground hover:text-foreground'
      )}
    >
      <Icon size={14} strokeWidth={1.9} />
    </button>
  )
  return (
    <div className="flex items-center gap-0.5 p-0.5 rounded-lg bg-secondary">
      {opt('light', Sun, 'Day mode')}
      {opt('dark', Moon, 'Night mode')}
    </div>
  )
}

export default function Topbar({ onMenu }) {
  const { pathname } = useLocation()
  const pageTitle = PAGE_TITLES[pathname]
    || (pathname.startsWith('/lead-search/runs/') ? 'Find leads'
      : /^\/leads\/\d+\/audit$/.test(pathname) ? 'Lead audit' : 'Overview')

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
    <header className="shrink-0 pt-safe bg-background/85 backdrop-blur-md border-b border-border-subtle z-10">
      <div className="h-14 flex items-center gap-3 px-4 lg:px-8">
      {/* Phones: the menu lives in the bottom bar (More); show the brand here. */}
      <LogoMark size={26} className="lg:hidden shrink-0" />
      <p className="text-subheading truncate">{pageTitle}</p>

      <div className="flex-1" />

      {nextRun && !engineStatus?.scraper_running && (
        <div className="hidden md:flex items-center gap-1.5 text-xs text-muted-foreground tabular">
          <Clock size={12} />
          Next run {nextRun} UTC
        </div>
      )}

      {engineStatus?.scraper_running && engineStatus.scraper_total > 0 && (
        <div className="hidden md:flex items-center gap-1.5 text-xs text-warning tabular">
          <Activity size={12} className="animate-pulse" />
          {engineStatus.scraper_progress} / {engineStatus.scraper_total} scraped
        </div>
      )}

      <span className="hidden md:block w-px h-4 bg-border" />
      <EngineStatus status={engineStatus} />
      <ThemeToggle />
      {isClientEdition() && (
        <button type="button" aria-label="Sign out" title="Sign out"
          onClick={() => { setSessionToken(''); window.location.replace(CLIENT_SIGN_OUT) }}
          className="btn-ghost h-9 px-2.5 text-sm">
          <LogOut size={15} /> <span className="hidden sm:inline">Sign out</span>
        </button>
      )}
      </div>
    </header>
  )
}
