import { useEffect, useRef, useState } from 'react'
import { Upload as UploadIcon, FileText, Trash2, Zap, Cpu, RotateCcw } from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import ConfirmDialog from '../components/ConfirmDialog.jsx'

function EmbedStats({ statementId }) {
  const [stats, setStats] = useState(null)
  useEffect(() => {
    api.get(`/embeddings/stats/${statementId}`).then(setStats).catch(() => {})
  }, [statementId])
  if (!stats) return <span className="text-[#475569]">—</span>
  const pct = stats.total > 0 ? Math.round((stats.embedded / stats.total) * 100) : 0
  return <span>{pct}%</span>
}

export default function Upload() {
  const toast = useToast()
  const [statements, setStatements] = useState([])
  const [file, setFile] = useState(null)
  const [password, setPassword] = useState('')
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [resetTarget, setResetTarget] = useState(null)
  const [annotatingId, setAnnotatingId] = useState(null)
  const [embeddingId, setEmbeddingId] = useState(null)
  const [lastResult, setLastResult] = useState(null)
  const fileRef = useRef(null)

  async function loadStatements() {
    try {
      const data = await api.get('/statements')
      setStatements(data)
    } catch (e) {
      toast(`Failed to load statements: ${e.message}`, 'error')
    }
  }

  useEffect(() => { loadStatements() }, [])

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
  }

  async function handleUpload() {
    if (!file) return
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const url = `/statements/upload${password ? `?password=${encodeURIComponent(password)}` : ''}`
      const result = await api.upload(url, form)
      toast(`Uploaded: ${result.bank_name} — ${result.statement_month}`, 'success')
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
      toast(`Reset ${resetTarget.bank_name} — ${resetTarget.statement_month}`, 'info')
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
      const result = await api.post('/annotations/auto-annotate', { statement_id: stmt.id })
      setLastResult(result)
      toast(
        `Done — ${result.rule_matched ?? 0} rule, ${result.rag_direct_annotated ?? 0} rag, ${result.llm_annotated ?? 0} llm`,
        'success',
        5000
      )
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error')
    } finally {
      setAnnotatingId(null)
    }
  }

  async function generateEmbeddings(stmt) {
    setEmbeddingId(stmt.id)
    try {
      const result = await api.post('/embeddings/generate', { statement_id: stmt.id })
      toast(`Embedded ${result.embedded} transactions`, 'success')
      loadStatements()
    } catch (e) {
      toast(`Embedding failed: ${e.message}`, 'error')
    } finally {
      setEmbeddingId(null)
    }
  }

  return (
    <div className="px-6 py-5 space-y-6 max-w-4xl">
      <h1 className="text-base font-semibold text-[#e2e8f0]">Upload Statement</h1>

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
            <p className="text-sm text-[#94a3b8]">Drop a PDF here or click to browse</p>
            <p className="text-xs text-[#475569] mt-1">Accepts .pdf bank statements</p>
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
            placeholder="PDF password (if encrypted)"
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
          <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] mb-3">Pipeline Result</p>
          <div className="flex flex-wrap gap-2">
            {[
              { label: 'Total', value: lastResult.total_processed, color: 'bg-[#1e2235] text-[#94a3b8]' },
              { label: 'Rule', value: lastResult.rule_matched, color: 'bg-[#1e3a5f] text-[#7dd3fc]' },
              { label: 'RAG direct', value: lastResult.rag_direct_annotated, color: 'bg-[#164e63] text-[#67e8f9]' },
              { label: 'RAG prompted', value: lastResult.rag_prompted_annotated, color: 'bg-[#164e63] text-[#67e8f9]' },
              { label: 'LLM', value: lastResult.llm_annotated, color: 'bg-[#3b1f5e] text-[#c4b5fd]' },
              { label: 'Failed', value: lastResult.llm_failed, color: 'bg-[#450a0a] text-[#fca5a5]' },
              { label: 'Low conf', value: lastResult.low_confidence, color: 'bg-[#451a03] text-[#fdba74]' },
              { label: 'Already annotated', value: lastResult.already_annotated, color: 'bg-[#14532d] text-[#86efac]' },
            ].map(({ label, value, color }) => (
              <span key={label} className={`text-xs px-3 py-1 rounded-full font-medium ${color}`}>
                {label}: {value ?? 0}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Statement history */}
      <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
        <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] px-4 py-3 border-b border-[#2d3148]">
          Statement History
        </p>
        {statements.length === 0 ? (
          <p className="px-4 py-5 text-sm text-[#475569]">No statements uploaded yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr>
                {['Bank', 'Month', 'Uploaded', 'Embeddings', 'Actions'].map(h => (
                  <th key={h} className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {statements.map(s => (
                <tr key={s.id} className="border-b border-[#1a1d27] hover:bg-[#1a1d27] transition-colors">
                  <td className="px-4 py-3 text-[#e2e8f0]">
                    <div className="flex items-center gap-2">
                      <FileText size={14} className="text-[#6366f1] shrink-0" />
                      {s.bank_name}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-[#94a3b8]">{s.statement_month}</td>
                  <td className="px-4 py-3 text-[#94a3b8]">{dayjs(s.uploaded_at).format('DD MMM YYYY HH:mm')}</td>
                  <td className="px-4 py-3 text-[#94a3b8]">
                    <EmbedStats statementId={s.id} />
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => generateEmbeddings(s)}
                        disabled={embeddingId === s.id}
                        className="flex items-center gap-1 text-[#64748b] hover:text-[#a5b4fc] text-xs disabled:opacity-50 transition-colors"
                        title="Generate embeddings"
                      >
                        <Cpu size={13} />
                        {embeddingId === s.id ? 'Embedding…' : 'Embed'}
                      </button>
                      <button
                        onClick={() => autoAnnotate(s)}
                        disabled={annotatingId === s.id}
                        className="flex items-center gap-1 text-[#64748b] hover:text-[#a5b4fc] text-xs disabled:opacity-50 transition-colors"
                        title="Auto-annotate"
                      >
                        <Zap size={13} />
                        {annotatingId === s.id ? 'Annotating…' : 'Annotate'}
                      </button>
                      <button
                        onClick={() => setResetTarget(s)}
                        className="text-[#475569] hover:text-amber-400 transition-colors"
                        title="Reset transactions & annotations (keeps statement)"
                      >
                        <RotateCcw size={14} />
                      </button>
                      <button
                        onClick={() => setDeleteTarget(s)}
                        className="text-[#475569] hover:text-red-400 transition-colors"
                        title="Delete statement"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <ConfirmDialog
        open={!!resetTarget}
        title="Reset statement data?"
        description={`This will permanently delete all annotations and embeddings for "${resetTarget?.bank_name} — ${resetTarget?.statement_month}". Transactions and the statement record will be kept. This cannot be undone.`}
        confirmLabel="Reset"
        onConfirm={resetStatementData}
        onCancel={() => setResetTarget(null)}
        danger
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete statement?"
        description={`This will permanently delete "${deleteTarget?.bank_name} — ${deleteTarget?.statement_month}" and all associated transactions.`}
        confirmLabel="Delete"
        onConfirm={deleteStatement}
        onCancel={() => setDeleteTarget(null)}
        danger
      />
    </div>
  )
}
