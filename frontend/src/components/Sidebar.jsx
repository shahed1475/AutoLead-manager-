import { useEffect } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutDashboard, Users, GitBranch, Sparkles, Settings, Inbox, Search, Mail, X,
} from 'lucide-react'
import clsx from 'clsx'
import { inboxApi } from '../api/client'
import Logo from './Logo'
import InstallApp from './InstallApp'

// Grouped by what the user is doing, not by how the backend is built.
const groups = [
  { label: 'Workspace', items: [
    { to: '/dashboard',       icon: LayoutDashboard, label: 'Overview' },
    // One entry for every way of finding leads; its detail pages stay "inside" it.
    { to: '/lead-search',     icon: Search,          label: 'Find leads', also: ['/research-agent', '/campaign'] },
    { to: '/leads',           icon: Users,           label: 'Leads' },
    { to: '/pipeline',        icon: GitBranch,       label: 'Pipeline' },
    { to: '/inbox',           icon: Inbox,           label: 'Inbox', badge: true },
  ] },
  { label: 'Engage', items: [
    { to: '/email-campaigns', icon: Mail,            label: 'Email Campaigns' },
    { to: '/ai-lab',          icon: Sparkles,        label: 'AI Lab' },
  ] },
]

function NavItem({ to, icon: Icon, label, badge, unread, also }) {
  const { pathname } = useLocation()
  const alsoActive = (also || []).some((p) => pathname === p || pathname.startsWith(`${p}/`))
  return (
    <NavLink
      to={to}
      className={({ isActive: routeActive }) => { const isActive = routeActive || alsoActive; return clsx(
        'group relative flex items-center gap-2.5 h-8 px-2.5 rounded-lg text-sm transition-colors',
        isActive
          ? 'bg-surface-elevated text-foreground font-semibold shadow-sm ring-1 ring-border-subtle'
          : 'text-muted-foreground hover:text-foreground hover:bg-secondary/70'
      ) }}
    >
      {({ isActive: routeActive }) => { const isActive = routeActive || alsoActive; return (
        <>
          <Icon size={16} strokeWidth={isActive ? 2.1 : 1.75}
            className={clsx('shrink-0', isActive ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground')} />
          <span className="flex-1 truncate">{label}</span>
          {badge && unread > 0 && (
            <span className="min-w-5 h-5 px-1.5 grid place-items-center rounded-full bg-primary text-primary-foreground text-2xs font-semibold tabular">
              {unread > 99 ? '99+' : unread}
            </span>
          )}
        </>
      ) }}
    </NavLink>
  )
}

export default function Sidebar({ open, onClose }) {
  const { pathname } = useLocation()
  const { data: inboxStats } = useQuery({
    queryKey: ['inbox-stats'],
    queryFn: inboxApi.stats,
    refetchInterval: 60_000,
    retry: false,
  })
  const unread = inboxStats?.unread ?? 0

  // Close the mobile drawer after navigating.
  useEffect(() => { onClose?.() }, [pathname]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      {/* Mobile scrim */}
      <div
        onClick={onClose}
        className={clsx('fixed inset-0 z-30 bg-black/30 lg:hidden transition-opacity',
          open ? 'opacity-100' : 'opacity-0 pointer-events-none')}
      />
      <aside
        className={clsx(
          'fixed lg:static inset-y-0 left-0 z-40 w-64 lg:w-60 shrink-0 flex flex-col h-full',
          'bg-sidebar border-r border-border-subtle transition-transform duration-200 ease-out',
          open ? 'translate-x-0 shadow-lg' : '-translate-x-full lg:translate-x-0'
        )}
      >
        <div className="flex items-center justify-between h-14 px-4 mt-[env(safe-area-inset-top,0px)] lg:mt-0">
          <Logo size={26} />
          <button onClick={onClose} className="btn-ghost h-8 w-8 px-0 lg:hidden" aria-label="Close menu">
            <X size={16} />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 pt-2 pb-4 space-y-6" aria-label="Main">
          {groups.map((g) => (
            <div key={g.label}>
              <p className="px-2.5 pb-1.5 text-2xs font-semibold text-muted-foreground/80">{g.label}</p>
              <div className="space-y-0.5">
                {g.items.map((item) => <NavItem key={item.to} {...item} unread={unread} />)}
              </div>
            </div>
          ))}
        </nav>

        <div className="px-3 py-3 border-t border-border-subtle space-y-0.5 pb-[calc(0.75rem+env(safe-area-inset-bottom,0px))] lg:pb-3">
          <InstallApp />
          <NavItem to="/settings" icon={Settings} label="Settings" />
        </div>
      </aside>
    </>
  )
}
