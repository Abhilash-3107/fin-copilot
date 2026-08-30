import { useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { api } from '../lib/api.js'

// Persistent banner shown only on the hosted demo instance (health.demo_mode).
// The message is the local-first promise stated plainly: this box holds sample
// data and can't accept real statements - the API blocks them, not just the UI.
export default function DemoBanner() {
  const [demo, setDemo] = useState(false)

  useEffect(() => {
    api.get('/health').then(h => setDemo(!!h.demo_mode)).catch(() => {})
  }, [])

  if (!demo) return null
  return (
    <div className="flex items-center justify-center gap-3 bg-[#1e1b4b] border-b border-[#312e81] px-4 py-2 text-xs text-[#c7d2fe]">
      <Sparkles size={13} className="shrink-0 text-[#818cf8]" />
      <span>Sample data. Yours never comes here - this demo can't accept uploads.</span>
      <a
        href="https://github.com/Abhilash-3107/finance-copilot#finance-copilot"
        target="_blank"
        rel="noreferrer"
        className="shrink-0 rounded-md bg-[#6366f1] px-2.5 py-1 font-semibold text-white transition-opacity hover:opacity-90"
      >
        Run it locally
      </a>
    </div>
  )
}
