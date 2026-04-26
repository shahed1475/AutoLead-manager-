import { NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutDashboard, Users, Send, Cpu, Settings, Zap, Inbox,
} from 'lucide-react'
import clsx from 'clsx'
import { inboxApi } from '../api/client'

const nav = [
  { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/leads',     icon: Users,           label: 'Leads'     },
  { to: '/campaign',  icon: Send,            label: 'Campaign'  },
  { to: '/ai-lab',    icon: Cpu,             label: 'AI Lab'    },
  { to: '/inbox',     icon: Inbox,           label: 'Inbox', badge: true },
  { to: '/settings',  icon: Settings,        label: 'Settings'  },
]

export default function Sidebar() {
  const { data: inboxStats } = useQuery({
    queryKey: ['inbox-stats'],
    queryFn: inboxApi.stats,
    refetchInterval: 60_000,
    retry: false,
  })
  const unread = inboxStats?.unread ?? 0

  return (
    <aside className="w-56 shrink-0 flex flex-col bg-slate-900 border-r border-slate-800 h-screen">
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-slate-800">
        <div className="w-7 h-7 bg-brand-600 rounded-lg flex items-center justify-center">
          <Zap size={14} className="text-white" />
        </div>
        <div>
          <p className="font-semibold text-sm text-slate-100 leading-none">AutoLead</p>
          <p className="text-[10px] text-slate-500 mt-0.5">Marketing Engine v3</p>
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {nav.map(({ to, icon: Icon, label, badge }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              clsx(
                'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all',
                isActive
                  ? 'bg-brand-600/20 text-brand-400 border border-brand-600/20'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
              )
            }
          >
            <Icon size={16} />
            <span className="flex-1">{label}</span>
            {badge && unread > 0 && (
              <span className="ml-auto text-[9px] px-1.5 py-0.5 rounded-full bg-brand-600 text-white font-bold">
                {unread > 99 ? '99+' : unread}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="px-4 py-4 border-t border-slate-800">
        <p className="text-[10px] text-slate-600 text-center">AutoLead v3 · Self-Hosted</p>
      </div>
    </aside>
  )
}
