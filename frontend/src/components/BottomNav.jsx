import { NavLink, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { LayoutDashboard, Search, Users, Inbox, Menu } from 'lucide-react'
import clsx from 'clsx'
import { inboxApi } from '../api/client'

// Phone / tablet navigation: the four places people go most, plus More
// (opens the full menu). Hidden on large screens, where the sidebar shows.
const TABS = [
  { to: '/dashboard',   icon: LayoutDashboard, label: 'Overview' },
  { to: '/lead-search', icon: Search,          label: 'Find', also: ['/research-agent', '/campaign'] },
  { to: '/leads',       icon: Users,           label: 'Leads' },
  { to: '/inbox',       icon: Inbox,           label: 'Inbox', badge: true },
]

export default function BottomNav({ onMore }) {
  const { pathname } = useLocation()
  const { data: inboxStats } = useQuery({
    queryKey: ['inbox-stats'], queryFn: inboxApi.stats, refetchInterval: 60_000, retry: false,
  })
  const unread = inboxStats?.unread ?? 0
  const within = (p) => pathname === p || pathname.startsWith(`${p}/`)
  const onTab = TABS.some((t) => within(t.to) || (t.also || []).some(within))

  const item = 'relative flex flex-col items-center justify-center gap-1 text-2xs font-medium transition-colors'
  return (
    <nav aria-label="Main"
      className="lg:hidden fixed bottom-0 inset-x-0 z-20 bg-background/90 backdrop-blur-md border-t border-border-subtle pb-safe">
      <div className="grid grid-cols-5 h-16">
        {TABS.map(({ to, icon: Icon, label, badge, also }) => {
          const active = within(to) || (also || []).some(within)
          return (
            <NavLink key={to} to={to} className={clsx(item, active ? 'text-primary' : 'text-muted-foreground')}>
              <span className="relative">
                <Icon size={21} strokeWidth={active ? 2.1 : 1.8} />
                {badge && unread > 0 && (
                  <span className="absolute -top-1.5 -right-2.5 min-w-4 h-4 px-1 grid place-items-center rounded-full bg-primary text-primary-foreground text-[10px] font-bold tabular">
                    {unread > 99 ? '99+' : unread}
                  </span>
                )}
              </span>
              {label}
            </NavLink>
          )
        })}
        <button type="button" onClick={onMore}
          className={clsx(item, !onTab ? 'text-primary' : 'text-muted-foreground')}>
          <Menu size={21} strokeWidth={!onTab ? 2.1 : 1.8} />
          More
        </button>
      </div>
    </nav>
  )
}
