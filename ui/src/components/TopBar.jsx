import { Eye, EyeOff, Menu } from 'lucide-react'
import { usePrivacy } from '../contexts/PrivacyContext.jsx'
import Tooltip from './Tooltip.jsx'

export default function TopBar({ onMenu }) {
  const { hidden, toggle } = usePrivacy()

  return (
    <header className="h-12 shrink-0 bg-[#0f1117] border-b border-[#2d3148] flex items-center px-4 md:px-6 gap-2">
      <button
        onClick={onMenu}
        aria-label="Open menu"
        className="md:hidden text-[#94a3b8] hover:text-[#e2e8f0] -ml-1 p-1"
      >
        <Menu size={20} strokeWidth={1.75} />
      </button>
      <div className="flex-1" />
      <Tooltip content={`${hidden ? 'Show' : 'Hide'} all amounts (Shift+P)`}>
        <button
          onClick={toggle}
          aria-label={hidden ? 'Show amounts' : 'Hide amounts'}
          aria-pressed={hidden}
          className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-md border transition-colors ${
            hidden
              ? 'border-[#4b5268] text-[#e2e8f0] bg-[#1a1d27]'
              : 'border-[#2d3148] text-[#94a3b8] hover:text-[#e2e8f0] hover:border-[#4b5268]'
          }`}
        >
          {hidden ? <EyeOff size={14} strokeWidth={1.75} /> : <Eye size={14} strokeWidth={1.75} />}
          {hidden ? 'Amounts hidden' : 'Amounts'}
        </button>
      </Tooltip>
    </header>
  )
}
