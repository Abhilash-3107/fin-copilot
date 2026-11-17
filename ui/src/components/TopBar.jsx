import { Eye, EyeOff, Keyboard, Menu, Search } from 'lucide-react'
import { usePrivacy } from '../contexts/PrivacyContext.jsx'
import { modKeyLabel, openPalette, openShortcuts } from '../lib/shortcuts.js'
import Tooltip from './Tooltip.jsx'

export default function TopBar({ onMenu }) {
  const { hidden, toggle } = usePrivacy()
  const mod = modKeyLabel()

  return (
    <header className="h-12 shrink-0 bg-[#0f1117] border-b border-[#2d3148] flex items-center px-4 md:px-6 gap-2">
      <button
        onClick={onMenu}
        aria-label="Open menu"
        className="md:hidden text-[#94a3b8] hover:text-[#e2e8f0] -ml-1 p-1"
      >
        <Menu size={20} strokeWidth={1.75} />
      </button>
      {/* A visible way into the command palette; Cmd/Ctrl+K does the same. */}
      <button
        type="button"
        onClick={openPalette}
        aria-label="Search"
        className="flex items-center gap-2 text-sm text-[#94a3b8] hover:text-[#e2e8f0] bg-[#13151f] border border-[#2d3148] hover:border-[#4b5268] rounded-md px-2.5 py-1.5 md:w-72 transition-colors"
      >
        <Search size={14} strokeWidth={1.75} className="shrink-0" />
        <span className="hidden md:inline flex-1 text-left truncate">Search</span>
        <kbd className="hidden md:inline text-[10px] text-[#94a3b8] border border-[#2d3148] rounded px-1.5 py-0.5">{mod === 'Ctrl' ? 'Ctrl K' : '⌘K'}</kbd>
      </button>
      <div className="flex-1" />
      <Tooltip content="Keyboard shortcuts (?)" position="left">
        <button
          type="button"
          onClick={openShortcuts}
          aria-label="Keyboard shortcuts"
          className="text-[#94a3b8] hover:text-[#e2e8f0] p-1.5 rounded-md transition-colors"
        >
          <Keyboard size={16} strokeWidth={1.75} />
        </button>
      </Tooltip>
      <Tooltip content={`${hidden ? 'Show' : 'Hide'} all amounts (Shift+P)`} position="left">
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
