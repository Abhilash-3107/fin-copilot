import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, Zap, Cpu } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { useStatement } from '../contexts/StatementContext.jsx'
import TransactionTable from '../components/TransactionTable.jsx'
import AnnotationPanel from '../components/AnnotationPanel.jsx'
import EmptyState from '../components/EmptyState.jsx'

const BATCH_SIZE = 10

async function buildAnnotationMap(txns) {
  const map = {}
  // Fetch full transaction detail (includes annotation join) in batches
  for (let i = 0; i < txns.length; i += BATCH_SIZE) {
    const batch = txns.slice(i, i + BATCH_SIZE)
    const results = await Promise.allSettled(
      batch.map(t => api.get(`/transactions/${t.id}`))
    )
    results.forEach((r, idx) => {
      if (r.status === 'fulfilled' && r.value?.annotation_id) {
        map[batch[idx].id] = {
          id: r.value.annotation_id,
          category: r.value.category,
          subcategory: r.value.subcategory,
          merchant: r.value.merchant,
          tags: r.value.tags,
          confidence: r.value.confidence,
          source: r.value.source,
        }
      }
    })
  }
  return map
}

export default function Transactions() {
  const toast = useToast()
  const { statements, activeStatement, setActiveStatement } = useStatement()
  const selectedStmt = activeStatement?.id ?? ''
  const [month, setMonth] = useState('')
  const [filter, setFilter] = useState('all') // all | annotated | unannotated
  const [search, setSearch] = useState('')
  const [transactions, setTransactions] = useState([])
  const [annotationMap, setAnnotationMap] = useState({})
  const [loading, setLoading] = useState(false)
  const [activeTxn, setActiveTxn] = useState(null)
  const [activeAnnotation, setActiveAnnotation] = useState(null)
  const [autoAnnotating, setAutoAnnotating] = useState(false)
  const [embedding, setEmbedding] = useState(false)
  const loadRef = useRef(0)

  const loadTransactions = useCallback(async () => {
    const id = ++loadRef.current
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (selectedStmt) params.set('statement_id', selectedStmt)
      if (month) params.set('month', month)
      if (filter === 'unannotated') params.set('unannotated', 'true')
      const qs = params.toString()
      const txns = await api.get(`/transactions${qs ? '?' + qs : ''}`)
      if (id !== loadRef.current) return
      setTransactions(txns)
      // Build annotation map
      const map = await buildAnnotationMap(txns)
      if (id !== loadRef.current) return
      setAnnotationMap(map)
    } catch (e) {
      toast(`Failed to load: ${e.message}`, 'error')
    } finally {
      if (id === loadRef.current) setLoading(false)
    }
  }, [selectedStmt, month, filter, toast])

  useEffect(() => { loadTransactions() }, [loadTransactions])

  // Keyboard navigation
  useEffect(() => {
    function handler(e) {
      if (e.key === 'Escape') setActiveTxn(null)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  async function openAnnotationPanel(txn) {
    setActiveTxn(txn)
    setActiveAnnotation(undefined) // sentinel: loading
    // Fetch full transaction to get annotation fields
    try {
      const full = await api.get(`/transactions/${txn.id}`)
      if (full.annotation_id) {
        setActiveAnnotation({
          id: full.annotation_id,
          category: full.category,
          subcategory: full.subcategory,
          merchant: full.merchant,
          tags: full.tags,
          confidence: full.confidence,
          source: full.source,
        })
      } else {
        setActiveAnnotation(null)
      }
    } catch (_) {
      setActiveAnnotation(null)
    }
  }

  async function autoAnnotate() {
    if (!selectedStmt) {
      toast('Select a statement first', 'error')
      return
    }
    setAutoAnnotating(true)
    try {
      const result = await api.post('/annotations/auto-annotate', { statement_id: selectedStmt })
      toast(
        `Done — ${result.rule_matched ?? 0} rule, ${result.rag_direct_annotated ?? 0} rag, ${result.llm_annotated ?? 0} llm`,
        'success',
        4000
      )
      loadTransactions()
    } catch (e) {
      toast(`Auto-annotate failed: ${e.message}`, 'error')
    } finally {
      setAutoAnnotating(false)
    }
  }

  async function generateEmbeddings() {
    if (!selectedStmt) {
      toast('Select a statement first', 'error')
      return
    }
    setEmbedding(true)
    try {
      const result = await api.post('/embeddings/generate', { statement_id: selectedStmt })
      toast(`Embedded ${result.embedded} transactions`, 'success')
    } catch (e) {
      toast(`Embedding failed: ${e.message}`, 'error')
    } finally {
      setEmbedding(false)
    }
  }

  // Client-side filter for annotated/search
  const displayed = transactions.filter(txn => {
    if (filter === 'annotated' && !annotationMap[txn.id]) return false
    if (filter === 'unannotated' && annotationMap[txn.id]) return false
    if (search && !txn.raw_description.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  return (
    <div className="flex flex-col h-full">
      {/* Filter bar */}
      <div className="sticky top-0 z-20 bg-[#0f1117] border-b border-[#2d3148] px-5 py-3 flex items-center gap-3 flex-wrap">
        <select
          value={selectedStmt}
          onChange={e => {
            const stmt = statements.find(s => s.id === e.target.value) ?? null
            setActiveStatement(stmt)
          }}
          className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1]"
        >
          <option value="">All statements</option>
          {statements.map(s => (
            <option key={s.id} value={s.id}>{s.bank_name} — {s.statement_month}</option>
          ))}
        </select>

        <input
          type="month"
          value={month}
          onChange={e => setMonth(e.target.value)}
          className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1]"
        />

        <input
          type="search"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search description…"
          className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1] w-52 placeholder:text-[#475569]"
        />

        <div className="flex rounded-md overflow-hidden border border-[#2d3148]">
          {['all', 'unannotated', 'annotated'].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
                filter === f ? 'bg-[#6366f1] text-white' : 'bg-[#13151f] text-[#94a3b8] hover:text-[#e2e8f0]'
              }`}
            >
              {f}
            </button>
          ))}
        </div>

        <span className="text-xs text-[#64748b] ml-auto">
          {loading ? 'Loading…' : `${displayed.length} of ${transactions.length}`}
        </span>

        <button
          onClick={loadTransactions}
          disabled={loading}
          className="text-[#64748b] hover:text-[#94a3b8] disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
        </button>

        <button
          onClick={generateEmbeddings}
          disabled={embedding || !selectedStmt}
          className="flex items-center gap-1.5 bg-[#13151f] border border-[#2d3148] text-[#94a3b8] px-3 py-1.5 rounded-md text-xs hover:text-[#e2e8f0] disabled:opacity-50 transition-colors"
          title="Generate embeddings for selected statement"
        >
          <Cpu size={13} />
          {embedding ? 'Embedding…' : 'Embed'}
        </button>

        <button
          onClick={autoAnnotate}
          disabled={autoAnnotating || !selectedStmt}
          className="flex items-center gap-1.5 bg-[#6366f1] text-white px-3 py-1.5 rounded-md text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          title="Auto-annotate this statement"
        >
          <Zap size={13} />
          {autoAnnotating ? 'Annotating…' : 'Auto-annotate'}
        </button>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {!loading && displayed.length === 0 ? (
          <EmptyState
            title="No transactions"
            description={transactions.length === 0 ? 'Upload a statement to get started.' : 'No transactions match the current filters.'}
          />
        ) : (
          <TransactionTable
            transactions={displayed}
            annotationMap={annotationMap}
            activeId={activeTxn?.id}
            onSelect={openAnnotationPanel}
          />
        )}
      </div>

      {/* Annotation panel */}
      {activeTxn && (
        <AnnotationPanel
          txn={activeTxn}
          annotation={activeAnnotation}
          onClose={() => { setActiveTxn(null); setActiveAnnotation(null) }}
          onSaved={loadTransactions}
        />
      )}
    </div>
  )
}
