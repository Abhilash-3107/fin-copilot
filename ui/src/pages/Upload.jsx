import { useCallback, useEffect, useRef, useState } from 'react'
import { Upload as UploadIcon, FileText, Trash2, Zap, RotateCcw, DatabaseZap, Pencil, Check, X, AlertTriangle } from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { useAnnotationJob } from '../contexts/AnnotationJobContext.jsx'
import ConfirmDialog from '../components/ConfirmDialog.jsx'
import Tooltip from '../components/Tooltip.jsx'
import { formatMonth } from '../lib/dates.js'
import { annotationSummary, summaryDuration } from '../lib/annotationSummary.js'

// Running-balance reconciliation badge (4.20): turns the parser's balance-chain
// rigor into visible trust. Green check when every consecutive pair reconciles,
// amber warning when any break is found; nothing when there's no chain to check.
function BalancedBadge({ check }) {
  if (!check || check.checked === 0) return <span className="text-xs text-[#475569]">—</span>
  const ok = check.balanced
  return (
    <Tooltip content={ok
      ? 'Every running-balance step reconciles with its amount - the parse is internally consistent.'
      : "Some rows don't reconcile against the running balance; this statement may have been parsed imperfectly."}>
      <span className={`inline-flex items-center gap-1 whitespace-nowrap text-xs px-2 py-0.5 rounded-full font-medium ${
        ok ? 'bg-[#14532d] text-[#86efac]' : 'bg-[#451a03] text-[#fdba74]'
      }`}>
        {ok ? <Check size={12} /> : <AlertTriangle size={12} />}
        {check.consistent}/{check.checked}
      </span>
    </Tooltip>
  )
}

// Coverage badge: full (green), partial (amber), none (grey).
function CoverageBadge({ done, total }) {
  if (!total) return <span className="text-xs text-[#475569]">—</span>
  const full = done >= total
  const none = done === 0
  const cls = full
    ? 'bg-[#14532d] text-[#86efac]'
    : none
      ? 'bg-[#1e2235] text-[#64748b]'
      : 'bg-[#451a03] text-[#fdba74]'
  const label = full ? 'Full' : none ? 'None' : 'Partial'
  return (
    <span className={`whitespace-nowrap text-xs px-2 py-0.5 rounded-full font-medium ${cls}`}>
      {label} · {done}/{total}
    </span>
  )
}

export default function Upload() {
  const toast = useToast()
  // job is non-null while any run is tracked (here, another page, or
  // re-attached after a reload): one run at a time, so every Categorize waits.
  const { job, startAnnotation } = useAnnotationJob()
  const [statements, setStatements] = useState([])
  const [file, setFile] = useState(null)
  const [password, setPassword] = useState('')
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [resetTarget, setResetTarget] = useState(null)
  const [clearEmbedTarget, setClearEmbedTarget] = useState(null)
  const [annotatingId, setAnnotatingId] = useState(null)
  const [embeddingId, setEmbeddingId] = useState(null)
  const [lastResult, setLastResult] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [editingName, setEditingName] = useState('')
  const fileRef = useRef(null)

  const loadStatements = useCallback(async () => {
    try {
      const data = await api.get('/statements')
      setStatements(data)
    } catch (e) {
      toast(`Couldn't load your statements - ${e.message}`, 'error')
    }
  }, [toast])

  useEffect(() => { loadStatements() }, [loadStatements])

  function onDrop(e) {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files[0]
    if (f?.type === 'application/pdf') setFile(f)
    else toast('Please drop a PDF file', 'error')
  }

  function onFileChange(e) {
    const f = e.target.files[0]
    if (f) setFile(f)
    // Clear the input so picking the same file again (after an upload, a
    // delete or Cancel) still fires a change event.
    e.target.value = ''
  }

  async function handleUpload() {
    if (!file) return
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      if (password) form.append('password', password)
      const result = await api.upload('/statements/upload', form)
      toast(`Uploaded: ${result.bank_name} - ${formatMonth(result.statement_month)}`, 'success')
      for (const w of result.warnings ?? []) toast(w, 'error', 8000)
      setFile(null)
      setPassword('')
      loadStatements()
    } catch (e) {
      toast(`Upload failed: ${e.message}`, 'error')
    } finally {
      setUploading(false)
    }
  }

  async function deleteStatement() {
    if (!deleteTarget) return
    try {
      await api.delete(`/statements/${deleteTarget.id}`)
      toast(`Deleted ${deleteTarget.bank_name}`, 'info')
      setDeleteTarget(null)
      loadStatements()
    } catch (e) {
      toast(`Delete failed: ${e.message}`, 'error')
    }
  }

  async function resetStatementData() {
    if (!resetTarget) return
    try {
      await api.delete(`/statements/${resetTarget.id}/data`)
      toast(`Reset ${resetTarget.bank_name} - ${formatMonth(resetTarget.statement_month)}`, 'info')
      setResetTarget(null)
      loadStatements()
    } catch (e) {
      toast(`Reset failed: ${e.message}`, 'error')
    }
  }

  async function autoAnnotate(stmt) {
    setAnnotatingId(stmt.id)
    setLastResult(null)
    try {
      // The job flow, like every other entry point: one run at a time, progress
      // in the floating card, and the stale-job reaper behind it.
      const result = await startAnnotation({ statement_id: stmt.id })
      setLastResult(result)
      const summary = annotationSummary(result)
      toast(summary.message, summary.type, summaryDuration(summary))
      loadStatements()
    } catch (e) {
      toast(`Categorization failed - ${e.message}`, 'error')
    } finally {
      setAnnotatingId(null)
    }
  }

  function startEditName(s) {
    setEditingId(s.id)
    setEditingName(s.bank_name)
  }

  function cancelEditName() {
    setEditingId(null)
    setEditingName('')
  }

  async function saveEditName(id) {
    const name = editingName.trim()
    if (!name) return
    try {
      await api.patch(`/statements/${id}`, { bank_name: name })
      setEditingId(null)
      setEditingName('')
      loadStatements()
    } catch (e) {
      toast(`Couldn't rename - ${e.message}`, 'error')
    }
  }

  async function clearEmbeddings() {
    if (!clearEmbedTarget) return
    try {
      await api.delete(`/embeddings/statement/${clearEmbedTarget.id}`)
      toast(`Memory cleared - rebuild it when you're ready`, 'info')
      setClearEmbedTarget(null)
      loadStatements()
    } catch (e) {
      toast(`Couldn't clear the memory - ${e.message}`, 'error')
    }
  }

  // Build the AI's memory (embeddings) for a statement so it can learn from the
  // categories here. Invoked from the Memory status chip when it isn't full.
  async function buildMemory(s) {
    setEmbeddingId(s.id)
    try {
      const result = await api.post('/embeddings/generate', { statement_id: s.id })
      toast(`Added ${result.embedded} transactions to memory`, 'success')
      loadStatements()
    } catch (e) {
      toast(`Couldn't build memory - ${e.message}`, 'error')
    } finally {
      setEmbeddingId(null)
    }
  }

  return (
    <div className="px-6 py-5 space-y-6 max-w-5xl">
      <h1 className="text-base font-semibold text-[#e2e8f0]">Add Statement</h1>

      {/* Drop zone */}
      <div
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => fileRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors ${
          dragOver ? 'border-[#6366f1] bg-[#1e2440]' : 'border-[#2d3148] hover:border-[#3d4158] hover:bg-[#13151f]'
        }`}
      >
        <UploadIcon size={32} className="mx-auto mb-3 text-[#475569]" />
        {file ? (
          <p className="text-sm text-[#a5b4fc] font-medium">{file.name}</p>
        ) : (
          <>
            <p className="text-sm text-[#94a3b8]">Drop your bank statement here, or click to browse</p>
            <p className="text-xs text-[#475569] mt-1">PDF format - we'll handle the rest</p>
          </>
        )}
        <input ref={fileRef} type="file" accept=".pdf" onChange={onFileChange} className="hidden" />
      </div>

      {file && (
        <div className="flex items-center gap-3">
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="Password (if your PDF is locked)"
            className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] placeholder:text-[#475569] w-64"
          />
          <button
            onClick={handleUpload}
            disabled={uploading}
            className="flex items-center gap-2 bg-[#6366f1] text-white px-5 py-2 rounded-md text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            <UploadIcon size={14} />
            {uploading ? 'Uploading…' : 'Upload'}
          </button>
          <button
            onClick={() => setFile(null)}
            className="text-[#475569] hover:text-[#94a3b8] text-sm"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Pipeline result */}
      {lastResult && (
        <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] mb-3">Categorization Results</p>
          <div className="flex flex-wrap gap-2">
            {[
              { label: 'Processed', value: lastResult.total_processed, color: 'bg-[#1e2235] text-[#94a3b8]' },
              { label: 'Matched by rules', value: lastResult.rule_matched, color: 'bg-[#1e3a5f] text-[#7dd3fc]' },
              { label: 'Matched from history', value: (lastResult.rag_direct_annotated ?? 0) + (lastResult.rag_prompted_annotated ?? 0), color: 'bg-[#164e63] text-[#67e8f9]' },
              // With no AI configured the LLM stage never ran: the remainder is
              // "waiting for you", not "couldn't figure out".
              ...(lastResult.ai_active === false
                ? [{ label: 'Waiting for you', value: lastResult.llm_failed, color: 'bg-[#451a03] text-[#fdba74]' }]
                : [
                    { label: 'Figured out by AI', value: lastResult.llm_annotated, color: 'bg-[#3b1f5e] text-[#c4b5fd]' },
                    { label: 'Couldn\'t figure out', value: lastResult.llm_failed, color: 'bg-[#450a0a] text-[#fca5a5]' },
                  ]),
              { label: 'Needs your review', value: lastResult.low_confidence, color: 'bg-[#451a03] text-[#fdba74]' },
              { label: 'Already done', value: lastResult.already_annotated, color: 'bg-[#14532d] text-[#86efac]' },
            ].map(({ label, value, color }) => (
              <span key={label} className={`text-xs px-3 py-1 rounded-full font-medium ${color}`}>
                {label}: {value ?? 0}
              </span>
            ))}
          </div>
          {lastResult.llm_error && (
            <p className="text-xs text-[#fca5a5] mt-3">
              The AI model isn&apos;t available ({lastResult.llm_error}), so this run stopped using it.
              Check it in{' '}
              <a href="/settings" className="text-[#818cf8] hover:underline">Settings › AI &amp; Model</a>{' '}
              and run again to categorize what&apos;s left.
            </p>
          )}
          {lastResult.ai_active === false && (
            <p className="text-xs text-[#64748b] mt-3">
              AI is off - your rules and history did the work above. Add a provider in{' '}
              <a href="/settings" className="text-[#818cf8] hover:underline">Settings › AI &amp; Model</a>{' '}
              to categorize the rest automatically.
            </p>
          )}
        </div>
      )}

      {/* Statement history */}
      <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-x-auto">
        <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] px-4 py-3 border-b border-[#2d3148]">
          Your Statements
        </p>
        {statements.length === 0 ? (
          <p className="px-4 py-5 text-sm text-[#475569]">No statements yet - drop one above to get started</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr>
                {['Bank', 'Month', 'Balanced', 'Categorized', 'Memory', 'Uploaded', 'Actions'].map(h => (
                  <th key={h} className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {statements.map(s => (
                <tr key={s.id} className="border-b border-[#1a1d27] hover:bg-[#1a1d27] transition-colors">
                  <td className="px-4 py-3 text-[#e2e8f0]">
                    {editingId === s.id ? (
                      <div className="flex items-center gap-1.5">
                        <input
                          autoFocus
                          value={editingName}
                          onChange={e => setEditingName(e.target.value)}
                          onKeyDown={e => {
                            if (e.key === 'Enter') saveEditName(s.id)
                            if (e.key === 'Escape') cancelEditName()
                          }}
                          className="bg-[#1e2235] border border-[#6366f1] text-[#e2e8f0] px-2 py-0.5 rounded text-sm focus:outline-none w-40"
                        />
                        <button onClick={() => saveEditName(s.id)} className="text-[#86efac] hover:opacity-80">
                          <Check size={13} />
                        </button>
                        <button onClick={cancelEditName} className="text-[#64748b] hover:text-[#94a3b8]">
                          <X size={13} />
                        </button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 group">
                        <FileText size={14} className="text-[#6366f1] shrink-0" />
                        <span>{s.bank_name}</span>
                        <button
                          onClick={() => startEditName(s)}
                          className="opacity-0 group-hover:opacity-100 text-[#475569] hover:text-[#94a3b8] transition-opacity"
                        >
                          <Pencil size={12} />
                        </button>
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-[#94a3b8]">{formatMonth(s.statement_month)}</td>
                  <td className="px-4 py-3">
                    <BalancedBadge check={s.balance_check} />
                  </td>
                  <td className="px-4 py-3">
                    <Tooltip content="Transactions that have been categorized">
                      <CoverageBadge done={s.annotated_count} total={s.txn_count} />
                    </Tooltip>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Tooltip content="How much of this statement the AI has memorized. It learns from these to categorize your future statements.">
                        <CoverageBadge done={s.embedded_count} total={s.txn_count} />
                      </Tooltip>
                      {s.txn_count > 0 && s.embedded_count < s.txn_count && (
                        <Tooltip content="Add the rest of this statement to the AI's memory so it can learn from your categories here">
                          <button
                            onClick={() => buildMemory(s)}
                            disabled={embeddingId === s.id}
                            className="text-[10px] font-medium text-[#6366f1] hover:text-[#818cf8] disabled:opacity-50 transition-colors"
                          >
                            {embeddingId === s.id ? 'Building…' : 'Build'}
                          </button>
                        </Tooltip>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-[#94a3b8]">{dayjs(s.uploaded_at).format('DD MMM YYYY HH:mm')}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Tooltip content="Use AI to automatically categorize transactions in this statement">
                        <button
                          onClick={() => autoAnnotate(s)}
                          disabled={annotatingId === s.id || job != null}
                          className="flex items-center gap-1 text-[#64748b] hover:text-[#a5b4fc] text-xs disabled:opacity-50 transition-colors"
                        >
                          <Zap size={13} />
                          {annotatingId === s.id ? 'Categorizing…' : 'Categorize'}
                        </button>
                      </Tooltip>
                      <Tooltip content="Rebuild the AI's memory - do this after making corrections">
                        <button
                          onClick={() => setClearEmbedTarget(s)}
                          className="text-[#475569] hover:text-sky-400 transition-colors"
                        >
                          <DatabaseZap size={14} />
                        </button>
                      </Tooltip>
                      <Tooltip content="Reset all categories - keeps the statement data">
                        <button
                          onClick={() => setResetTarget(s)}
                          className="text-[#475569] hover:text-amber-400 transition-colors"
                        >
                          <RotateCcw size={14} />
                        </button>
                      </Tooltip>
                      <Tooltip content="Delete this statement and all its data">
                        <button
                          onClick={() => setDeleteTarget(s)}
                          className="text-[#475569] hover:text-red-400 transition-colors"
                        >
                          <Trash2 size={14} />
                        </button>
                      </Tooltip>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <ConfirmDialog
        open={!!clearEmbedTarget}
        title="Rebuild the AI's memory?"
        description={`This will reset the AI's memory for "${clearEmbedTarget?.bank_name} - ${formatMonth(clearEmbedTarget?.statement_month)}". Your categories are kept. Rebuild after making corrections so the AI learns from your changes.`}
        confirmLabel="Rebuild memory"
        onConfirm={clearEmbeddings}
        onCancel={() => setClearEmbedTarget(null)}
        danger
      />

      <ConfirmDialog
        open={!!resetTarget}
        title="Start over with this statement?"
        description={`This will remove all categories and the AI's memory for "${resetTarget?.bank_name} - ${formatMonth(resetTarget?.statement_month)}". Your transactions stay - you can re-categorize anytime. This can't be undone.`}
        confirmLabel="Reset"
        onConfirm={resetStatementData}
        onCancel={() => setResetTarget(null)}
        danger
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete this statement?"
        description={`This will permanently remove "${deleteTarget?.bank_name} - ${formatMonth(deleteTarget?.statement_month)}" and all its transactions. This can't be undone.`}
        confirmLabel="Delete"
        onConfirm={deleteStatement}
        onCancel={() => setDeleteTarget(null)}
        danger
      />
    </div>
  )
}
