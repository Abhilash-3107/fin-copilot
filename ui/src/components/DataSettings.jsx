import { Download } from 'lucide-react'

// Data management. Export is live; backup/delete-everything land in Phase 4
// (roadmap 4.14) alongside the backend endpoints they need.
export default function DataSettings() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Export</h2>
        <p className="text-xs text-[#64748b] mb-3 leading-relaxed max-w-xl">
          Download your transactions with their current categories as a CSV or JSON file -
          your data stays yours and portable.
        </p>
        <div className="flex gap-3">
          <a
            href="/api/transactions/export?format=csv"
            className="flex items-center gap-1.5 bg-[#13151f] border border-[#2d3148] text-[#cbd5e1] px-4 py-2 rounded-md text-sm hover:border-[#6366f1] hover:text-[#e2e8f0] transition-colors"
          >
            <Download size={14} /> Export CSV
          </a>
          <a
            href="/api/transactions/export?format=json"
            className="flex items-center gap-1.5 bg-[#13151f] border border-[#2d3148] text-[#cbd5e1] px-4 py-2 rounded-md text-sm hover:border-[#6366f1] hover:text-[#e2e8f0] transition-colors"
          >
            <Download size={14} /> Export JSON
          </a>
        </div>
      </div>

      <div>
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Backup &amp; reset</h2>
        <p className="text-xs text-[#64748b] leading-relaxed max-w-xl">
          Full database backup and delete-everything are coming in a later update.
        </p>
      </div>
    </div>
  )
}
