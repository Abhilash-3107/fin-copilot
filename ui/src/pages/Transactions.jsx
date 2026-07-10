import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { RefreshCw, Zap, ChevronDown, X } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { useStatement } from '../contexts/StatementContext.jsx'
import { usePeriod, ALL_TIME } from '../contexts/PeriodContext.jsx'
import PeriodPicker from '../components/PeriodPicker.jsx'
import TransactionTable from '../components/TransactionTable.jsx'
import AnnotationPanel from '../components/AnnotationPanel.jsx'
import { useAnnotationJob } from '../contexts/AnnotationJobContext.jsx'
import EmptyState from '../components/EmptyState.jsx'
import Tooltip from '../components/Tooltip.jsx'
import { sourceFilterOptions } from '../lib/sources.js'

const PAGE_SIZE = 500

function MultiFilter({ label, options, selected, onChange }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    function handler(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const count = selected.size
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(v => !v)}
        className={`flex items-center gap-1.5 bg-[#13151f] border px-2.5 py-1.5 rounded-md text-sm transition-colors ${
          count > 0 ? 'border-[#6366f1] text-[#a5b4fc]' : 'border-[#2d3148] text-[#94a3b8] hover:text-[#e2e8f0]'
        }`}
      >
        <span>{label}{count > 0 ? ` (${count})` : ''}</span>
        {count > 0 && (
          <X
            size={12}
            className="opacity-60 hover:opacity-100"
            onClick={e => { e.stopPropagation(); onChange(new Set()) }}
          />
        )}
        <ChevronDown size={13} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 z-30 bg-[#13151f] border border-[#2d3148] rounded-lg shadow-lg py-1 min-w-[160px]">
          {options.map(opt => {
            // An option may stand for several raw values that share one label
            // (e.g. rag_direct + rag_prompted → "from history"); they toggle
            // as a unit.
            const values = opt.values ?? [opt.value]
            return (
              <label
                key={opt.label}
                className="flex items-center gap-2.5 px-3 py-1.5 text-sm cursor-pointer hover:bg-[#1e2235] text-[#cbd5e1]"
              >
                <input
                  type="checkbox"
                  checked={values.every(v => selected.has(v))}
                  onChange={e => {
                    const next = new Set(selected)
                    for (const v of values) {
                      if (e.target.checked) next.add(v)
                      else next.delete(v)
                    }
                    onChange(next)
                  }}
                  className="accent-[#6366f1]"
                />
                {opt.label}
              </label>
            )
          })}
        </div>
      )}
    </div>
  )
}

function buildAnnotationMap(txns) {
  const map = {}
  for (const t of txns) {
    if (t.annotation_id) {
      map[t.id] = {
        id: t.annotation_id,
        category: t.category,
        subcategory: t.subcategory,
        merchant: t.merchant,
        tags: t.tags,
        confidence: t.confidence,
        source: t.source,
      }
    }
  }
  return map
}

export default function Transactions() {
  const toast = useToast()
  const { statements, activeStatement, setActiveStatement } = useStatement()
  const { month: periodMonth, setMonth } = usePeriod()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedStmt = activeStatement?.id ?? ''
  // The shared period drives the month filter; ALL_TIME means "no month filter".
  const month = periodMonth && periodMonth !== ALL_TIME ? periodMonth : ''
  const [sourceFilter, setSourceFilter] = useState(new Set())
  const [categoryFilter, setCategoryFilter] = useState(new Set())
  const [merchantFilter, setMerchantFilter] = useState('')
  const [search, setSearch] = useState('')
  // The query the server actually sees; trails `search` by the debounce delay.
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [facets, setFacets] = useState({ categories: [], sources: [] })
  const [transactions, setTransactions] = useState([])
  const [annotationMap, setAnnotationMap] = useState({})
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [activeTxn, setActiveTxn] = useState(null)
  const [activeAnnotation, setActiveAnnotation] = useState(null)
  const { startAnnotation } = useAnnotationJob()
  const [autoAnnotating, setAutoAnnotating] = useState(false)
  const loadRef = useRef(0)

  // Stable serializations of the Set filters, for effect deps and API params.
  const sourceCsv = [...sourceFilter].sort().join(',')
  const categoryCsv = [...categoryFilter].sort().join(',')

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300)
    return () => clearTimeout(t)
  }, [search])

  const buildFilterParams = useCallback(() => {
    const params = new URLSearchParams()
    params.set('include', 'annotation')
    params.set('limit', PAGE_SIZE)
    if (selectedStmt) params.set('statement_id', selectedStmt)
    if (month) params.set('month', month)
    if (debouncedSearch) params.set('q', debouncedSearch)
    if (categoryCsv) params.set('category', categoryCsv)
    if (sourceCsv) params.set('source', sourceCsv)
    if (merchantFilter) params.set('merchant', merchantFilter)
    return params
  }, [selectedStmt, month, debouncedSearch, categoryCsv, sourceCsv, merchantFilter])

  const loadTransactions = useCallback(async () => {
    const id = ++loadRef.current
    setLoading(true)
    try {
      const txns = await api.get(`/transactions?${buildFilterParams()}`)
      if (id !== loadRef.current) return
      setTransactions(txns)
      setAnnotationMap(buildAnnotationMap(txns))
      setHasMore(txns.length === PAGE_SIZE)
    } catch (e) {
      toast(`Failed to load: ${e.message}`, 'error')
    } finally {
      if (id === loadRef.current) setLoading(false)
    }
  }, [buildFilterParams, toast])

  async function loadMore() {
    const last = transactions.at(-1)
    if (!last) return
    setLoadingMore(true)
    try {
      const params = buildFilterParams()
      params.set('after', last.id)
      const more = await api.get(`/transactions?${params}`)
      setTransactions(prev => [...prev, ...more])
      setAnnotationMap(prev => ({ ...prev, ...buildAnnotationMap(more) }))
      setHasMore(more.length === PAGE_SIZE)
    } catch (e) {
      toast(`Failed to load more: ${e.message}`, 'error')
    } finally {
      setLoadingMore(false)
    }
  }

  useEffect(() => { loadTransactions() }, [loadTransactions])

  // Filter dropdown options come from a scope-wide facets query, not from the
  // loaded page, so options that first appear beyond the current page (or that
  // an active filter would hide) are still offered.
  const loadFacets = useCallback(async () => {
    const params = new URLSearchParams()
    if (selectedStmt) params.set('statement_id', selectedStmt)
    if (month) params.set('month', month)
    try {
      setFacets(await api.get(`/transactions/facets?${params}`))
    } catch (_) {
      // Non-fatal: dropdowns just keep their previous options.
    }
  }, [selectedStmt, month])

  useEffect(() => { loadFacets() }, [loadFacets])

  // Seed filters from the /transactions deep-link convention (category,
  // merchant, source, q, month). Runs on mount and whenever a new deep link
  // lands here; a month param is pushed into the shared period. Our own
  // writeback below also changes searchParams — the ref lets us tell the two
  // apart so a writeback never re-seeds (or clears the statement scope).
  const lastWrittenParams = useRef(null)
  useEffect(() => {
    if (searchParams.toString() === lastWrittenParams.current) return
    const category = searchParams.get('category')
    const source = searchParams.get('source')
    const merchant = searchParams.get('merchant')
    const q = searchParams.get('q')
    const m = searchParams.get('month')
    setCategoryFilter(category ? new Set(category.split(',')) : new Set())
    setSourceFilter(source ? new Set(source.split(',')) : new Set())
    setMerchantFilter(merchant ?? '')
    setSearch(q ?? '')
    setDebouncedSearch(q ?? '') // skip the debounce so the load isn't briefly unfiltered
    if (m && m !== periodMonth) setMonth(m)
    // A deep link means "this slice of everything": a lingering statement
    // selection would AND with the link's month and silently empty the list.
    if (category || source || merchant || q || m) clearStatementForDeepLink.current = true
    // Deliberately only re-seed when the URL changes, not when the period or
    // user-edited filters change, so manual edits aren't clobbered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  // Reflect filter edits back into the URL so any filtered view survives a
  // refresh and can be shared. `replace` keeps keystrokes out of history; the
  // shared period deliberately isn't written (it lives in PeriodContext and
  // follows the user across pages), so an incoming month param is absorbed
  // into the period and then dropped from the URL.
  const writebackArmed = useRef(false)
  useEffect(() => {
    // On mount this still sees pre-seed (empty) state — the seed effect's
    // setState hasn't rendered yet — so writing here would erase a deep link.
    // Skip once; the seeded state re-runs this effect with real values.
    if (!writebackArmed.current) {
      writebackArmed.current = true
      return
    }
    const next = new URLSearchParams()
    if (categoryCsv) next.set('category', categoryCsv)
    if (sourceCsv) next.set('source', sourceCsv)
    if (merchantFilter) next.set('merchant', merchantFilter)
    if (search) next.set('q', search)
    const s = next.toString()
    if (s === searchParams.toString()) return
    lastWrittenParams.current = s
    setSearchParams(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryCsv, sourceCsv, merchantFilter, search])

  // The statement scope may already be set (in-session navigation) or arrive
  // later (hard load: StatementContext defaults to the latest statement once
  // /statements resolves), so the clear has to chase it. One-shot: a manual
  // statement pick after the deep link sticks.
  const clearStatementForDeepLink = useRef(false)
  useEffect(() => {
    if (clearStatementForDeepLink.current && activeStatement) {
      setActiveStatement(null)
      clearStatementForDeepLink.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStatement, searchParams])

  // PeriodPicker is shared across pages, so the period may already be set to
  // a month (eg. carried over from Dashboard) before this page's statement
  // scope resolves (StatementContext defaults to the latest statement once
  // /statements loads) or before the user changes the period directly. Either
  // way, a stale statement_id + month combo would AND into an empty result
  // with no explanation, so drop the stale scope whenever they disagree.
  useEffect(() => {
    if (activeStatement && month && activeStatement.statement_month !== month) {
      setActiveStatement(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [month, activeStatement])

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
          // dev mode only: backend attaches the reasoning trace when DEV_MODE is on
          reasoning: full.reasoning ?? null,
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
      const result = await startAnnotation({ statement_id: selectedStmt })
      toast(
        `All done! ${result.rule_matched ?? 0} matched by rules, ${result.rag_direct_annotated ?? 0} from history, ${result.llm_annotated ?? 0} by AI`,
        'success',
        4000
      )
      loadTransactions()
      loadFacets()
    } catch (e) {
      toast(`Auto-annotate failed: ${e.message}`, 'error')
    } finally {
      setAutoAnnotating(false)
    }
  }


  // Patch a saved annotation into the loaded rows in place. A full reload
  // would reset to page one and lose the scroll position, which punishes the
  // "walk down the list fixing categories" workflow. The edited row stays
  // visible even if it no longer matches an active filter — it drops out on
  // the next natural reload, which reads as less jarring than vanishing rows.
  function applySavedAnnotation(txnId, saved) {
    const ann = {
      id: saved.id,
      category: saved.category,
      subcategory: saved.subcategory,
      merchant: saved.merchant,
      tags: saved.tags,
      confidence: saved.confidence,
      source: saved.source,
    }
    setAnnotationMap(prev => ({ ...prev, [txnId]: ann }))
    setTransactions(prev => prev.map(t =>
      t.id === txnId ? { ...t, ...ann, id: t.id, annotation_id: saved.id } : t
    ))
    loadFacets() // a manual save can introduce a category new to this scope
  }

  const sourceOptions = sourceFilterOptions(facets.sources)
  const categoryOptions = facets.categories.map(c => ({ value: c, label: c }))

  const filtersActive = Boolean(
    debouncedSearch || sourceFilter.size || categoryFilter.size || merchantFilter
  )

  return (
    <div className="flex flex-col h-full">
      {/* Filter bar */}
      <div className="sticky top-0 z-20 bg-[#0f1117] border-b border-[#2d3148] px-5 py-3 flex items-center gap-3 flex-wrap">
        <select
          value={selectedStmt}
          onChange={e => {
            const stmt = statements.find(s => s.id === e.target.value) ?? null
            setActiveStatement(stmt)
            // Picking a statement is picking a month; keep the shared period in
            // sync so it doesn't silently AND against a different month and
            // empty the list (or clear it entirely for "All statements").
            setMonth(stmt ? stmt.statement_month : ALL_TIME)
          }}
          className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1]"
        >
          <option value="">All statements</option>
          {statements.map(s => (
            <option key={s.id} value={s.id}>{s.bank_name} — {s.statement_month}</option>
          ))}
        </select>

        <PeriodPicker />

        <input
          type="search"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search description…"
          className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1] w-52 placeholder:text-[#475569]"
        />

        <MultiFilter
          label="Source"
          options={sourceOptions}
          selected={sourceFilter}
          onChange={setSourceFilter}
        />

        <MultiFilter
          label="Category"
          options={categoryOptions}
          selected={categoryFilter}
          onChange={setCategoryFilter}
        />

        {merchantFilter && (
          <button
            onClick={() => setMerchantFilter('')}
            className="flex items-center gap-1.5 bg-[#13151f] border border-[#6366f1] text-[#a5b4fc] px-2.5 py-1.5 rounded-md text-sm transition-colors"
          >
            <span>{merchantFilter}</span>
            <X size={12} className="opacity-60 hover:opacity-100" />
          </button>
        )}

        <span className="text-xs text-[#64748b] ml-auto">
          {loading ? 'Loading…' : `${transactions.length}${hasMore ? '+' : ''} transaction${transactions.length === 1 ? '' : 's'}`}
        </span>

        <button
          onClick={loadTransactions}
          disabled={loading}
          className="text-[#64748b] hover:text-[#94a3b8] disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
        </button>

        <Tooltip content="Use AI to automatically categorize transactions in this statement">
          <button
            onClick={autoAnnotate}
            disabled={autoAnnotating || !selectedStmt}
            className="flex items-center gap-1.5 bg-[#6366f1] text-white px-3 py-1.5 rounded-md text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            <Zap size={13} />
            {autoAnnotating ? 'Annotating…' : 'Auto-annotate'}
          </button>
        </Tooltip>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {!loading && transactions.length === 0 ? (
          <EmptyState
            title="No transactions"
            description={filtersActive ? 'No transactions match the current filters.' : 'Upload a statement to get started.'}
          />
        ) : (
          <>
            <TransactionTable
              transactions={transactions}
              annotationMap={annotationMap}
              activeId={activeTxn?.id}
              onSelect={openAnnotationPanel}
            />
            {hasMore && (
              <div className="flex justify-center py-4">
                <button
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="bg-[#13151f] border border-[#2d3148] text-[#94a3b8] px-4 py-1.5 rounded-md text-xs hover:text-[#e2e8f0] disabled:opacity-50 transition-colors"
                >
                  {loadingMore ? 'Loading…' : 'Load more'}
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {/* Annotation panel */}
      {activeTxn && (
        <AnnotationPanel
          txn={activeTxn}
          annotation={activeAnnotation}
          onClose={() => { setActiveTxn(null); setActiveAnnotation(null) }}
          onSaved={applySavedAnnotation}
        />
      )}
    </div>
  )
}
