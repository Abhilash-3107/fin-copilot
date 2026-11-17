import { useCallback, useEffect, useRef, useState } from 'react'
import { LEDGER_KEYS } from '../lib/shortcuts.js'
import { useSearchParams, useLocation } from 'react-router-dom'
import { RefreshCw, Zap, ChevronDown, X } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { useStatement } from '../contexts/StatementContext.jsx'
import { usePeriod, ALL_TIME } from '../contexts/PeriodContext.jsx'
import PeriodPicker from '../components/PeriodPicker.jsx'
import TransactionTable from '../components/TransactionTable.jsx'
import TransactionCards from '../components/TransactionCards.jsx'
import AnnotationPanel from '../components/AnnotationPanel.jsx'
import { formatMonth } from '../lib/dates.js'
import { useAnnotationJob } from '../contexts/AnnotationJobContext.jsx'
import EmptyState from '../components/EmptyState.jsx'
import Tooltip from '../components/Tooltip.jsx'
import Amount from '../components/Amount.jsx'
import InlineError from '../components/InlineError.jsx'
import { sourceFilterOptions } from '../lib/sources.js'
import { annotationSummary, summaryDuration } from '../lib/annotationSummary.js'
import { couldnt } from '../lib/errors.js'
import { count } from '../lib/plural.js'

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

// What the server resolves for a payment's name; a row is refreshed from the
// payment's own read after an edit instead of working the name out here.
const NAME_FIELDS = ['payee', 'payee_all', 'payee_name', 'display_name', 'note', 'channel']

function nameFields(txn) {
  return Object.fromEntries(NAME_FIELDS.map(f => [f, txn[f]]))
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
  const { statements, activeStatement, setActiveStatement, loading: statementsLoading } = useStatement()
  const { month: periodMonth, setMonth, months } = usePeriod()
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  // The shared period drives the month filter; ALL_TIME means "no month filter".
  const month = periodMonth && periodMonth !== ALL_TIME ? periodMonth : ''
  // The statement scope is whatever the period allows. StatementContext defaults to
  // the newest statement when /statements arrives, possibly after the user (or a
  // previous session) already holds All time or another month, so the default is
  // never read as a scope that contradicts the period: not for a render, a list
  // request or the select. The effects below then clear the stored selection.
  const scopedStatement = activeStatement
    && periodMonth !== ALL_TIME
    && (!month || activeStatement.statement_month === month)
    ? activeStatement
    : null
  const selectedStmt = scopedStatement?.id ?? ''
  const [sourceFilter, setSourceFilter] = useState(new Set())
  const [categoryFilter, setCategoryFilter] = useState(new Set())
  const [merchantFilter, setMerchantFilter] = useState('')
  // A Money Map row's payee: the grouping keys drive the query, name labels the chip.
  const [payeeFilter, setPayeeFilter] = useState(null)
  // Person filter carries {token, name}: token drives the query, name labels the chip.
  const [personFilter, setPersonFilter] = useState(null)
  const [minAmount, setMinAmount] = useState('')
  const [maxAmount, setMaxAmount] = useState('')
  const [sort, setSort] = useState('date')     // 'date' | 'amount'
  const [order, setOrder] = useState('desc')    // 'asc' | 'desc'
  const [selected, setSelected] = useState(new Set()) // selected txn ids for bulk actions
  const [search, setSearch] = useState('')
  // The query the server actually sees; trails `search` by the debounce delay.
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [facets, setFacets] = useState({ categories: [], sources: [] })
  const [categoryTree, setCategoryTree] = useState([])
  const [transactions, setTransactions] = useState([])
  // Count and debit/credit totals of every row the current filters match (all
  // pages), so a drill-through visibly adds up to the figure that was clicked.
  const [summary, setSummary] = useState(null)
  const [annotationMap, setAnnotationMap] = useState({})
  const [loading, setLoading] = useState(false)
  // Each fetch that can fail keeps its error, so the block says so (with Retry)
  // instead of reading as "no transactions".
  const [loadError, setLoadError] = useState(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [moreError, setMoreError] = useState(null)
  const [facetsError, setFacetsError] = useState(null)
  const [categoriesError, setCategoriesError] = useState(null)
  const [detailError, setDetailError] = useState(null)
  const [linkError, setLinkError] = useState(null) // {id, status} of a linked payment that could not open
  const [activeTxn, setActiveTxn] = useState(null)
  const [activeAnnotation, setActiveAnnotation] = useState(null)
  // Any tracked run holds the single job slot, so Sort again waits for it.
  const { job, startAnnotation } = useAnnotationJob()
  const [autoAnnotating, setAutoAnnotating] = useState(false)
  const loadRef = useRef(0)
  const facetsRef = useRef(0)

  // Stable serializations of the Set filters, for effect deps and API params.
  const sourceCsv = [...sourceFilter].sort().join(',')
  const categoryCsv = [...categoryFilter].sort().join(',')

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300)
    return () => clearTimeout(t)
  }, [search])

  // The filters alone: shared by the list and its one-line summary so the two
  // always describe the same rows.
  const buildScopeParams = useCallback(() => {
    const params = new URLSearchParams()
    if (selectedStmt) params.set('statement_id', selectedStmt)
    if (month) params.set('month', month)
    if (debouncedSearch) params.set('q', debouncedSearch)
    if (categoryCsv) params.set('category', categoryCsv)
    if (sourceCsv) params.set('source', sourceCsv)
    if (merchantFilter) params.set('merchant', merchantFilter)
    for (const key of payeeFilter?.keys ?? []) params.append('payee_key', key)
    if (personFilter?.token) params.set('person', personFilter.token)
    if (minAmount !== '' && !Number.isNaN(Number(minAmount))) params.set('min_amount', minAmount)
    if (maxAmount !== '' && !Number.isNaN(Number(maxAmount))) params.set('max_amount', maxAmount)
    return params
  }, [selectedStmt, month, debouncedSearch, categoryCsv, sourceCsv, merchantFilter, payeeFilter, personFilter, minAmount, maxAmount])

  const buildFilterParams = useCallback(() => {
    const params = buildScopeParams()
    params.set('include', 'annotation')
    params.set('limit', PAGE_SIZE)
    params.set('sort', sort)
    params.set('order', order)
    return params
  }, [buildScopeParams, sort, order])

  const loadTransactions = useCallback(async () => {
    const id = ++loadRef.current
    setLoading(true)
    setLoadError(null)
    try {
      const [txns, totals] = await Promise.all([
        api.get(`/transactions?${buildFilterParams()}`),
        api.get(`/transactions/summary?${buildScopeParams()}`).catch(() => null),
      ])
      if (id !== loadRef.current) return
      setSummary(totals)
      setTransactions(txns)
      setAnnotationMap(buildAnnotationMap(txns))
      setHasMore(txns.length === PAGE_SIZE)
      setSelected(new Set()) // a fresh result set invalidates the old selection
    } catch (e) {
      if (id === loadRef.current) setLoadError(e)
    } finally {
      if (id === loadRef.current) setLoading(false)
    }
  }, [buildFilterParams, buildScopeParams])

  async function loadMore() {
    const last = transactions.at(-1)
    if (!last) return
    setLoadingMore(true)
    setMoreError(null)
    try {
      const params = buildFilterParams()
      params.set('after', last.id)
      const more = await api.get(`/transactions?${params}`)
      setTransactions(prev => [...prev, ...more])
      setAnnotationMap(prev => ({ ...prev, ...buildAnnotationMap(more) }))
      setHasMore(more.length === PAGE_SIZE)
    } catch (e) {
      setMoreError(e)
    } finally {
      setLoadingMore(false)
    }
  }

  useEffect(() => { loadTransactions() }, [loadTransactions])

  function toggleSort(col) {
    if (sort === col) setOrder(o => (o === 'asc' ? 'desc' : 'asc'))
    // Both sortable columns (date, amount) open on newest / largest first.
    else { setSort(col); setOrder('desc') }
  }

  function toggleSelect(id) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  function toggleSelectAll() {
    setSelected(prev =>
      prev.size === transactions.length ? new Set() : new Set(transactions.map(t => t.id))
    )
  }

  const [bulkCategory, setBulkCategory] = useState('')
  const [bulkApplying, setBulkApplying] = useState(false)

  async function applyBulkCategory() {
    const [category, subcategory] = bulkCategory.includes(' › ')
      ? bulkCategory.split(' › ')
      : [bulkCategory, null]
    if (!category) return
    setBulkApplying(true)
    try {
      const { updated, skipped = [] } = await api.post('/annotations/bulk-category', {
        transaction_ids: [...selected], category, subcategory: subcategory || null,
      })
      const gone = skipped.length
        ? ` · ${skipped.length} skipped (no longer exist${skipped.length === 1 ? 's' : ''})`
        : ''
      toast(`Sorted ${count(updated, 'payment')}${gone}`, skipped.length ? 'info' : 'success')
      setSelected(new Set())
      setBulkCategory('')
      loadTransactions()
    } catch (e) {
      toast(couldnt('apply', e), 'error')
    } finally {
      setBulkApplying(false)
    }
  }

  // Filter dropdown options come from a scope-wide facets query, not from the
  // loaded page, so options that first appear beyond the current page (or that
  // an active filter would hide) are still offered.
  const loadFacets = useCallback(async () => {
    const id = ++facetsRef.current
    const params = new URLSearchParams()
    if (selectedStmt) params.set('statement_id', selectedStmt)
    if (month) params.set('month', month)
    try {
      const next = await api.get(`/transactions/facets?${params}`)
      if (id !== facetsRef.current) return // a newer scope was asked for meanwhile
      setFacets(next)
      setFacetsError(null)
    } catch (e) {
      // The dropdowns keep their previous options; the bar says so, with Retry.
      if (id === facetsRef.current) setFacetsError(e)
    }
  }, [selectedStmt, month])

  useEffect(() => { loadFacets() }, [loadFacets])

  // Full taxonomy for the bulk set-category picker (facets only list categories
  // already present in data; here the user can assign any of them).
  const loadCategories = useCallback(() => {
    setCategoriesError(null)
    api.get('/categories').then(setCategoryTree).catch(setCategoriesError)
  }, [])
  useEffect(() => { loadCategories() }, [loadCategories])

  const bulkOptions = (() => {
    const byId = Object.fromEntries(categoryTree.map(c => [c.id, c]))
    return categoryTree
      .filter(c => c.parent_id !== null)
      .map(c => `${byId[c.parent_id]?.name ?? '?'} › ${c.name}`)
      .sort()
  })()

  // Seed filters from the /transactions deep-link convention (category,
  // merchant, source, q, month). Runs on mount and whenever a new deep link
  // lands here; a month param is pushed into the shared period. Our own
  // writeback below also changes searchParams - the ref lets us tell the two
  // apart so a writeback never re-seeds (or clears the statement scope).
  const lastWrittenParams = useRef(null)
  const pendingPayment = useRef(null)
  const scrollTarget = useRef(null)
  useEffect(() => {
    if (searchParams.toString() === lastWrittenParams.current) return
    const category = searchParams.get('category')
    const source = searchParams.get('source')
    const merchant = searchParams.get('merchant')
    const payeeKeys = searchParams.getAll('payee_key')
    const person = searchParams.get('person')
    const q = searchParams.get('q')
    const m = searchParams.get('month')
    setCategoryFilter(category ? new Set(category.split(',')) : new Set())
    setSourceFilter(source ? new Set(source.split(',')) : new Set())
    setMerchantFilter(merchant ?? '')
    // The payee's name rides along in navigation state, like a person's; on a
    // cold link the chip reads what the server calls the payee once rows load.
    setPayeeFilter(payeeKeys.length ? { keys: payeeKeys, name: location.state?.payeeName ?? null } : null)
    // A person label may ride along in navigation state so the chip reads as a
    // name; on a cold link we only have the token, which is a fine chip label.
    setPersonFilter(person ? { token: person, name: location.state?.personName ?? person } : null)
    setSearch(q ?? '')
    setDebouncedSearch(q ?? '') // skip the debounce so the load isn't briefly unfiltered
    if (m && m !== periodMonth) setMonth(m)
    // A deep link means "this slice of everything": a lingering statement
    // selection would AND with the link's month and silently empty the list.
    // A payment link (the palette) names one payment: the effect below opens it once it can.
    const linked = searchParams.get('txn')
    if (linked) pendingPayment.current = linked
    if (category || source || merchant || payeeKeys.length || person || q || m || linked) clearStatementForDeepLink.current = true
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
    // On mount this still sees pre-seed (empty) state - the seed effect's
    // setState hasn't rendered yet - so writing here would erase a deep link.
    // Skip once; the seeded state re-runs this effect with real values.
    if (!writebackArmed.current) {
      writebackArmed.current = true
      return
    }
    const next = new URLSearchParams()
    if (categoryCsv) next.set('category', categoryCsv)
    if (sourceCsv) next.set('source', sourceCsv)
    if (merchantFilter) next.set('merchant', merchantFilter)
    for (const key of payeeFilter?.keys ?? []) next.append('payee_key', key)
    if (personFilter?.token) next.set('person', personFilter.token)
    if (search) next.set('q', search)
    const s = next.toString()
    if (s === searchParams.toString()) return
    lastWrittenParams.current = s
    setSearchParams(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryCsv, sourceCsv, merchantFilter, payeeFilter, personFilter, search])

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

  // "All time" means every transaction. A lingering statement selection would
  // keep the list on one month while the period says otherwise, so the two
  // controls are made to agree: All time clears the statement scope and the
  // select reads "All statements".
  useEffect(() => {
    if (periodMonth === ALL_TIME && activeStatement) setActiveStatement(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [periodMonth, activeStatement])

  // Escape closes the open payment. While the user is typing in it, the first
  // Escape only leaves the field, so a half-typed edit is not thrown away.
  const panelOpen = activeTxn != null
  useEffect(() => {
    function handler(e) {
      if (e.key !== LEDGER_KEYS.closePanel || e.defaultPrevented) return
      const t = e.target
      const typing = t?.tagName === 'INPUT' || t?.tagName === 'TEXTAREA' || t?.tagName === 'SELECT' || t?.isContentEditable
      if (typing && panelOpen) { t.blur?.(); return }
      setActiveTxn(null)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [panelOpen])

  // The payment's own record decides what the panel shows: its annotation, if any.
  function showRecord(full) {
    setActiveTxn(prev => (prev?.id === full.id ? { ...prev, ...nameFields(full) } : prev))
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
  }

  async function openAnnotationPanel(txn) {
    setActiveTxn(txn)
    setActiveAnnotation(undefined) // sentinel: loading
    setDetailError(null)
    setLinkError(null)
    // Fetch full transaction to get annotation fields. When that fails the panel
    // must not pose as a new annotation for a payment that may already have one.
    try {
      showRecord(await api.get(`/transactions/${txn.id}`))
    } catch (e) {
      setDetailError(e)
    }
  }

  // A link to one payment (the palette): show the month it is in, drop a statement
  // scope that would hide it, and open it. Waits for the statements so the month
  // can be matched against the ones the period picker knows.
  function openLinkedPayment(id) {
    setLinkError(null)
    return api.get(`/transactions/${id}`)
      .then(full => {
        const m = String(full.txn_date).slice(0, 7)
        setMonth(months.includes(m) ? m : ALL_TIME)
        setActiveStatement(null)
        scrollTarget.current = full.id
        setDetailError(null)
        setActiveTxn(full)
        showRecord(full)
      })
      .catch(e => setLinkError({ id, status: e?.status }))
      .finally(() => {
        setSearchParams(prev => {
          const next = new URLSearchParams(prev)
          next.delete('txn')
          return next
        }, { replace: true })
      })
  }

  useEffect(() => {
    const id = pendingPayment.current
    if (!id || statementsLoading) return
    pendingPayment.current = null
    openLinkedPayment(id)
    // showRecord and the setters are stable in effect: only a new link or the
    // statements finishing their load should run this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, statementsLoading])

  // Bring the linked payment's row into view once its month has loaded.
  useEffect(() => {
    if (!scrollTarget.current || loading) return
    const rows = [...document.querySelectorAll(`[data-txn-id="${scrollTarget.current}"]`)]
    const row = rows.find(el => el.offsetParent !== null) ?? rows[0]
    if (row) {
      row.scrollIntoView?.({ block: 'center' })
      scrollTarget.current = null
    }
  }, [transactions, loading])

  async function autoAnnotate() {
    if (!selectedStmt) {
      toast('Select a statement first', 'error')
      return
    }
    setAutoAnnotating(true)
    try {
      const result = await startAnnotation({ statement_id: selectedStmt })
      const summary = annotationSummary(result)
      toast(summary.message, summary.type, summaryDuration(summary))
      loadTransactions()
      loadFacets()
    } catch (e) {
      toast(couldnt('sort', e), 'error')
    } finally {
      setAutoAnnotating(false)
    }
  }


  // Patch a saved annotation into the loaded rows in place. A full reload
  // would reset to page one and lose the scroll position, which punishes the
  // "walk down the list fixing categories" workflow. The edited row stays
  // visible even if it no longer matches an active filter - it drops out on
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
    refreshName(txnId) // and a new merchant can change what the row is called
  }

  async function refreshName(txnId) {
    try {
      const fields = nameFields(await api.get(`/transactions/${txnId}`))
      setTransactions(prev => prev.map(t => (t.id === txnId ? { ...t, ...fields } : t)))
    } catch (_) {
      // The row keeps its old name until the next load.
    }
  }

  // A rename changes names and nothing else, so the rows are updated where
  // they stand: the page keeps its place, its selection and the open payment.
  // A payee name reaches every payment to the payee, so the loaded ones of that
  // payee are re-read together; a payment's own name changes only its row.
  async function applyRename(updated, scope) {
    const fields = nameFields(updated)
    setActiveTxn(prev => ({ ...prev, ...fields }))
    setTransactions(prev => prev.map(t => (t.id === updated.id ? { ...t, ...fields } : t)))
    if (scope !== 'payee' || !updated.counterparty_key) return
    try {
      const rows = await api.get(`/transactions?${new URLSearchParams({
        payee_key: updated.counterparty_key, include: 'annotation', limit: 1000,
      })}`)
      const byId = new Map(rows.map(r => [r.id, nameFields(r)]))
      setTransactions(prev => prev.map(t => (byId.has(t.id) ? { ...t, ...byId.get(t.id) } : t)))
    } catch (_) {
      // The rows keep their old names until the next load.
    }
  }

  const sourceOptions = sourceFilterOptions(facets.sources)
  const categoryOptions = facets.categories.map(c => ({ value: c, label: c }))

  const filtersActive = Boolean(
    debouncedSearch || sourceFilter.size || categoryFilter.size || merchantFilter || payeeFilter || personFilter
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
            <option key={s.id} value={s.id}>{s.bank_name} - {formatMonth(s.statement_month)}</option>
          ))}
        </select>

        <PeriodPicker />

        <input
          type="search"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search payee, note or amount…"
          className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1] w-64 placeholder:text-[#94a3b8]"
        />

        <div className="flex items-center gap-1">
          <input
            type="number"
            value={minAmount}
            onChange={e => setMinAmount(e.target.value)}
            placeholder="Min ₹"
            className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1] w-24 placeholder:text-[#94a3b8]"
          />
          <span className="text-[#94a3b8] text-xs">–</span>
          <input
            type="number"
            value={maxAmount}
            onChange={e => setMaxAmount(e.target.value)}
            placeholder="Max ₹"
            className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1] w-24 placeholder:text-[#94a3b8]"
          />
        </div>

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

        {facetsError && (
          <InlineError what="the filter options" error={facetsError} onRetry={loadFacets} compact />
        )}

        {merchantFilter && (
          <button
            onClick={() => setMerchantFilter('')}
            className="flex items-center gap-1.5 bg-[#13151f] border border-[#6366f1] text-[#a5b4fc] px-2.5 py-1.5 rounded-md text-sm transition-colors"
          >
            <span>{merchantFilter}</span>
            <X size={12} className="opacity-60 hover:opacity-100" />
          </button>
        )}

        {payeeFilter && (
          <button
            onClick={() => setPayeeFilter(null)}
            className="flex items-center gap-1.5 bg-[#13151f] border border-[#6366f1] text-[#a5b4fc] px-2.5 py-1.5 rounded-md text-sm transition-colors"
          >
            <span>Payee: {payeeFilter.name ?? transactions.find(t => t.payee_all)?.payee_all ?? payeeFilter.keys[0]}</span>
            <X size={12} className="opacity-60 hover:opacity-100" />
          </button>
        )}

        {personFilter && (
          <button
            onClick={() => setPersonFilter(null)}
            className="flex items-center gap-1.5 bg-[#13151f] border border-[#6366f1] text-[#a5b4fc] px-2.5 py-1.5 rounded-md text-sm transition-colors"
          >
            <span>Person: {personFilter.name}</span>
            <X size={12} className="opacity-60 hover:opacity-100" />
          </button>
        )}

        <span className="text-xs text-[#94a3b8] ml-auto">
          {loading ? 'Loading…' : loadError ? null : summary ? (
            <>
              {count(summary.count, 'payment')}
              {' · '}<Amount value={summary.money_out} decimals={0} /> out
              {' · '}<Amount value={summary.money_in} decimals={0} /> in
            </>
          ) : (
            `${transactions.length}${hasMore ? '+' : ''} ${transactions.length === 1 ? 'payment' : 'payments'}`
          )}
        </span>

        <button
          onClick={loadTransactions}
          disabled={loading}
          className="text-[#94a3b8] hover:text-[#e2e8f0] disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
        </button>

        <Tooltip content="Sort this statement again, for example after connecting a model">
          <button
            onClick={autoAnnotate}
            disabled={autoAnnotating || !selectedStmt || job != null}
            className="flex items-center gap-1.5 bg-[#4f46e5] text-white px-3 py-1.5 rounded-md text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            <Zap size={13} />
            {autoAnnotating ? 'Sorting…' : 'Sort again'}
          </button>
        </Tooltip>
      </div>

      {/* Bulk action bar (4.12) */}
      {selected.size > 0 && (
        <div className="sticky top-0 z-20 bg-[#1e1b4b] border-b border-[#3730a3] px-5 py-2.5 flex items-center gap-3 flex-wrap">
          <span className="text-sm text-[#c7d2fe] font-medium">
            {selected.size} selected
          </span>
          <button
            onClick={() => setSelected(new Set())}
            className="text-xs text-[#a5b4fc] hover:text-[#e0e7ff]"
          >
            Clear
          </button>
          <div className="flex items-center gap-2 ml-auto">
            {categoriesError && (
              <InlineError what="the categories" error={categoriesError} onRetry={loadCategories} compact className="text-[#fecaca]" />
            )}
            <select
              value={bulkCategory}
              onChange={e => setBulkCategory(e.target.value)}
              className="bg-[#13151f] border border-[#4338ca] text-[#e2e8f0] px-2.5 py-1.5 rounded-md text-sm focus:outline-none max-w-[220px]"
            >
              <option value="">Set category…</option>
              {bulkOptions.map(opt => <option key={opt} value={opt}>{opt}</option>)}
            </select>
            <button
              onClick={applyBulkCategory}
              disabled={!bulkCategory || bulkApplying}
              className="bg-[#4f46e5] text-white px-3 py-1.5 rounded-md text-xs font-medium hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              {bulkApplying ? 'Applying…' : 'Apply'}
            </button>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {linkError && (
          <p role="alert" className="m-4 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-[#7f1d1d] bg-[#2a1215] px-4 py-3 text-sm text-[#fecaca]">
            {linkError.status === 404 ? (
              <span>Couldn't find that payment. It may have been removed.</span>
            ) : (
              <>
                <span>Couldn't open this payment.</span>
                <button
                  type="button"
                  onClick={() => openLinkedPayment(linkError.id)}
                  className="shrink-0 rounded-md border border-[#7f1d1d] px-3 py-1.5 text-xs font-medium text-[#fecaca] transition-colors hover:bg-[#3b1a1e]"
                >
                  Retry
                </button>
              </>
            )}
          </p>
        )}
        {loadError ? (
          <InlineError what="your payments" error={loadError} onRetry={loadTransactions} retrying={loading} className="m-4" />
        ) : !loading && transactions.length === 0 ? (
          <EmptyState
            title="No payments"
            description={filtersActive ? 'No payments match the current filters.' : 'Add a statement to get started.'}
          />
        ) : (
          <>
            {/* Table on md+, card-per-row below md (4.1). */}
            <div className="hidden md:block">
              <TransactionTable
                transactions={transactions}
                annotationMap={annotationMap}
                activeId={activeTxn?.id}
                onSelect={openAnnotationPanel}
                selectable
                selected={selected}
                onToggle={toggleSelect}
                onToggleAll={toggleSelectAll}
                sort={sort}
                order={order}
                onSort={toggleSort}
              />
            </div>
            <div className="md:hidden">
              <TransactionCards
                transactions={transactions}
                annotationMap={annotationMap}
                activeId={activeTxn?.id}
                onSelect={openAnnotationPanel}
                selectable
                selected={selected}
                onToggle={toggleSelect}
              />
            </div>
            {moreError && (
              <InlineError what="more payments" error={moreError} onRetry={loadMore} retrying={loadingMore} className="mx-4 mt-3" />
            )}
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
          onClose={() => { setActiveTxn(null); setActiveAnnotation(null); setDetailError(null); scrollTarget.current = null }}
          onSaved={applySavedAnnotation}
          onRenamed={applyRename}
          loadError={detailError}
          onRetryLoad={() => openAnnotationPanel(activeTxn)}
        />
      )}
    </div>
  )
}
