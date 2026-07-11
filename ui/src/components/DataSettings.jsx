import { useState } from 'react'
import { Download, Database, Trash2 } from 'lucide-react'
import { api, downloadFile } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import ConfirmDialog from './ConfirmDialog.jsx'

// Data management (4.14): export (CSV/JSON), full DB backup, and an irreversible
// delete-everything. Downloads go through the token-aware client so they work
// under API-token auth.
export default function DataSettings() {
  const toast = useToast()
  const [busy, setBusy] = useState(null) // 'csv' | 'json' | 'backup' | 'delete'
  const [confirmDelete, setConfirmDelete] = useState(false)

  async function download(kind, path, name) {
    setBusy(kind)
    try {
      await downloadFile(path, name)
    } catch (e) {
      toast(`Download failed - ${e.message}`, 'error')
    } finally {
      setBusy(null)
    }
  }

  async function deleteEverything() {
    setConfirmDelete(false)
    setBusy('delete')
    try {
      await api.post('/data/delete-all', {})
      toast('All data deleted. Your categories are kept.', 'info')
      // Hard reload so every page drops its now-empty caches.
      setTimeout(() => window.location.reload(), 600)
    } catch (e) {
      toast(`Couldn't delete - ${e.message}`, 'error')
      setBusy(null)
    }
  }

  const btn = 'flex items-center gap-1.5 bg-[#13151f] border border-[#2d3148] text-[#cbd5e1] px-4 py-2 rounded-md text-sm hover:border-[#6366f1] hover:text-[#e2e8f0] disabled:opacity-50 transition-colors'

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Export</h2>
        <p className="text-xs text-[#64748b] mb-3 leading-relaxed max-w-xl">
          Download your transactions with their current categories as a CSV or JSON file -
          your data stays yours and portable.
        </p>
        <div className="flex gap-3">
          <button className={btn} disabled={busy === 'csv'}
            onClick={() => download('csv', '/transactions/export?format=csv', 'transactions.csv')}>
            <Download size={14} /> {busy === 'csv' ? 'Preparing…' : 'Export CSV'}
          </button>
          <button className={btn} disabled={busy === 'json'}
            onClick={() => download('json', '/transactions/export?format=json', 'transactions.json')}>
            <Download size={14} /> {busy === 'json' ? 'Preparing…' : 'Export JSON'}
          </button>
        </div>
      </div>

      <div>
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Backup</h2>
        <p className="text-xs text-[#64748b] mb-3 leading-relaxed max-w-xl">
          Download a complete snapshot of your database - transactions, categories,
          learned rules, and memory - as a single file you can keep or restore.
        </p>
        <button className={btn} disabled={busy === 'backup'}
          onClick={() => download('backup', '/data/backup', 'finance-copilot-backup.db')}>
          <Database size={14} /> {busy === 'backup' ? 'Preparing…' : 'Download backup'}
        </button>
      </div>

      <div>
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Delete everything</h2>
        <p className="text-xs text-[#64748b] mb-3 leading-relaxed max-w-xl">
          Permanently remove all statements, transactions, categories you've assigned,
          people, groups, and the AI's memory. The category list itself is kept. This
          cannot be undone - back up first if you're unsure.
        </p>
        <button
          onClick={() => setConfirmDelete(true)}
          disabled={busy === 'delete'}
          className="flex items-center gap-1.5 bg-[#2a1518] border border-[#5b2731] text-[#f87171] px-4 py-2 rounded-md text-sm hover:bg-[#3a1a1e] disabled:opacity-50 transition-colors"
        >
          <Trash2 size={14} /> {busy === 'delete' ? 'Deleting…' : 'Delete all data'}
        </button>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        danger
        title="Delete all data?"
        description="This permanently removes every statement, transaction, category assignment, person, group, and all AI memory. Your category list is kept. This cannot be undone."
        confirmLabel="Delete everything"
        onConfirm={deleteEverything}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  )
}
