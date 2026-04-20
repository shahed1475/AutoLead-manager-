import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, Users, Send, Cpu, Settings, Zap
} from 'lucide-react'
import clsx from 'clsx'

const nav = [
  { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/leads',     icon: Users,           label: 'Leads'     },
  { to: '/campaign',  icon: Send,            label: 'Campaign'  },
  { to: '/ai-lab',    icon: Cpu,             label: 'AI Lab'    },
  { to: '/settings',  icon: Settings,        label: 'Settings'  },
]

export default function Sidebar() {
  return (
    <aside className="w-56 shrink-0 flex flex-col bg-slate-900 border-r border-slate-800 h-screen">
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-slate-800">
        <div className="w-7 h-7 bg-brand-600 rounded-lg flex items-center justify-center">
          <Zap size={14} className="text-white" />
        </div>
        <div>
          <p className="font-semibold text-sm text-slate-100 leading-none">AutoLead</p>
          <p className="text-[10px] text-slate-500 mt-0.5">Marketing Engine</p>
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {nav.map(({ to, icon: Icon, label }) => (
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
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="px-4 py-4 border-t border-slate-800">
        <p className="text-[10px] text-slate-600 text-center">Zero-Cost Engine v1.0</p>
      </div>
    </aside>
  )
}
