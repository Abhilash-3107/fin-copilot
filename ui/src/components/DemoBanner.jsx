import { Sparkles } from 'lucide-react'
import { useDemoData } from '../contexts/DemoDataContext.jsx'
import RemoveSampleData from './RemoveSampleData.jsx'

const strip = 'flex items-center justify-center gap-3 bg-[#1e1b4b] border-b border-[#312e81] px-4 py-2 text-xs text-[#c7d2fe]'

// The persistent strip above every page while the ledger holds sample data.
// On the hosted demo instance it is the local-first promise stated plainly:
// this box holds sample data and can't accept real statements - the API blocks
// them, not just the UI. On your own install it says the same about the sample
// ledger and offers the way out.
export default function DemoBanner() {
  const { hosted, sample, mixed } = useDemoData()

  if (hosted) {
    return (
      <div className={strip}>
        <Sparkles size={13} className="shrink-0 text-[#818cf8]" />
        <span>Sample data. Yours never comes here - this demo can't accept uploads.</span>
        <a
          href="https://github.com/Abhilash-3107/fin-copilot#finance-copilot"
          target="_blank"
          rel="noreferrer"
          className="shrink-0 rounded-md bg-[#6366f1] px-2.5 py-1 font-semibold text-white transition-opacity hover:opacity-90"
        >
          Run it locally
        </a>
      </div>
    )
  }
  if (!sample) return null
  return (
    <div className={strip}>
      <Sparkles size={13} className="shrink-0 text-[#818cf8]" />
      <span>{mixed ? 'Sample data is mixed in with your statements.' : "You're looking at sample data."}</span>
      <RemoveSampleData className="shrink-0 rounded-md border border-[#4f46e5] px-2.5 py-1 font-semibold text-[#e0e7ff] transition-colors hover:bg-[#312e81] disabled:opacity-50" />
    </div>
  )
}
