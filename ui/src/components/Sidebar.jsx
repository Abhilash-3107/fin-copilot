import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, List, ClipboardCheck, Layers,
  Users, Upload, BarChart2, Settings,
} from 'lucide-react'

const NAV = [
  { to: '/dashboard',    icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/transactions', icon: List,             label: 'Transactions' },
  { to: '/review',       icon: ClipboardCheck,  label: 'Teach Me' },
  { to: '/groups',       icon: Layers,           label: 'Groups' },
  { to: '/people',       icon: Users,            label: 'People' },
  { to: '/upload',       icon: Upload,           label: 'Add Statement' },
  { to: '/insights',     icon: BarChart2,        label: 'Money Map' },
]

const linkClass = ({ isActive }) =>
  `flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors duration-150 ${
    isActive
      ? 'bg-[#1e2440] text-[#a5b4fc]'
      : 'text-[#94a3b8] hover:bg-[#1a1d27] hover:text-[#e2e8f0]'
  }`

export default function Sidebar() {
  return (
    <aside className="w-52 bg-[#13151f] border-r border-[#2d3148] flex flex-col shrink-0">
      <div className="px-4 py-4 border-b border-[#2d3148]">
        <span className="text-[#a5b4fc] font-semibold text-sm tracking-tight">Finance Copilot</span>
      </div>
      <nav className="flex-1 px-2 py-3 flex flex-col gap-0.5 overflow-y-auto">
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to} className={linkClass}>
            <Icon size={15} strokeWidth={1.75} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="px-2 py-3 border-t border-[#2d3148]">
        <NavLink to="/settings" className={linkClass}>
          <Settings size={15} strokeWidth={1.75} />
          Settings
        </NavLink>
      </div>
    </aside>
  )
}
