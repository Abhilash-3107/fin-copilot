import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { useStatement } from '../contexts/StatementContext.jsx'
import AnnotationPanel from '../components/AnnotationPanel.jsx'

const CAT_COLORS = [
  '#3b82f6', '#ef4444', '#10b981', '#a855f7', '#f59e0b',
  '#ec4899', '#14b8a6', '#6366f1', '#f97316',
]

function confColor(pct) {
  if (pct >= 75) return '#22c55e'
  if (pct >= 60) return '#f59e0b'
  return '#ef4444'
}

export default function Dashboard() {
  const toast = useToast()
  const { statements, activeStatement, setActiveStatement, loading: stmtLoading } = useStatement()

  const [transactions, setTransactions] = useState([])
  const [annMap, setAnnMap] = useState({})
  const [reviewCount, setReviewCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [activeTxn, setActiveTxn] = useState(null)
  const [activeAnnotation, setActiveAnnotation] = useState(null)
  const [autoAnnotating, setAutoAnnotating] = useState(false)
  const [amountsVisible, setAmountsVisible] = useState(false)

  // Reload transactions whenever the active statement changes
  useEffect(() => {
    if (stmtLoading) return
    async function load() {
      setLoading(true)
      try {
        const params = new URLSearchParams()
        if (activeStatement) params.set('statement_id', activeStatement.id)
        const qs = params.toString()

        const [txns, queue] = await Promise.all([
          api.get(`/transactions${qs ? '?' + qs : ''}`),
          api.get('/annotations/review-queue'),
        ])
        setTransactions(txns)
        setReviewCount(queue.length)

        const map = {}
        const sample = txns.slice(0, 200)
        const results = await Promise.allSettled(sample.map(t => api.get(`/transactions/${t.id}`)))
        results.forEach((r, i) => {
          if (r.status === 'fulfilled' && r.value?.annotation_id) {
            map[sample[i].id] = {
              id: r.value.annotation_id,
              category: r.value.category,
              subcategory: r.value.subcategory,
              source: r.value.source,
              confidence: r.value.confidence,
            }
          }
        })
        setAnnMap(map)
      } catch (e) {
        toast(`Failed to load dashboard: ${e.message}`, 'error')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [activeStatement, stmtLoading])

  async function runAutoAnnotate() {
    setAutoAnnotating(true)
    try {
      const body = activeStatement ? { statement_id: activeStatement.id } : {}
      const result = await api.post('/annotations/auto-annotate', body)
      toast(`Done — ${result.rule_matched ?? 0} rule, ${result.llm_annotated ?? 0} llm`, 'success', 4000)
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error')
    } finally {
      setAutoAnnotating(false)
    }
  }

  async function openPanel(txn) {
    setActiveTxn(txn)
    try {
      const full = await api.get(`/transactions/${txn.id}`)
      setActiveAnnotation(full.annotation_id ? {
        id: full.annotation_id, category: full.category, subcategory: full.subcategory,
        merchant: full.merchant, tags: full.tags, confidence: full.confidence, source: full.source,
      } : null)
    } catch (_) { setActiveAnnotation(null) }
  }

  // The selected month comes from the active statement
  const selectedMonth = activeStatement?.statement_month ?? null

  // Stats — scoped to the active statement's month
  const thisMonth = selectedMonth
    ? transactions.filter(t => t.txn_date.startsWith(selectedMonth))
    : transactions
  const totalSpend = thisMonth.filter(t => t.debit_credit === 'debit').reduce((s, t) => s + Number(t.amount), 0)
  const totalIncome = thisMonth.filter(t => t.debit_credit === 'credit').reduce((s, t) => s + Number(t.amount), 0)
  const annotatedCount = Object.keys(annMap).length
  const totalSampled = Math.min(transactions.length, 200)

  // Confidence by category (across all loaded transactions)
  const catConfMap = {}
  const catCountMap = {}
  for (const ann of Object.values(annMap)) {
    if (!ann.category || ann.confidence == null) continue
    catConfMap[ann.category] = (catConfMap[ann.category] ?? 0) + Number(ann.confidence)
    catCountMap[ann.category] = (catCountMap[ann.category] ?? 0) + 1
  }
  const confByCategory = Object.entries(catConfMap)
    .map(([cat, total]) => ({ cat, pct: Math.round((total / catCountMap[cat]) * 100) }))
    .sort((a, b) => b.pct - a.pct)
    .slice(0, 5)
  const lowestConf = confByCategory.at(-1) ?? null

  // Spend by category (this statement's month, debits)
  const spendByCat = {}
  for (const txn of thisMonth) {
    if (txn.debit_credit !== 'debit') continue
    const cat = annMap[txn.id]?.category ?? 'Uncategorized'
    spendByCat[cat] = (spendByCat[cat] ?? 0) + Number(txn.amount)
  }
  const totalMonthSpend = Object.values(spendByCat).reduce((s, v) => s + v, 0)
  const spendRanked = Object.entries(spendByCat).sort((a, b) => b[1] - a[1]).slice(0, 5)

  // Habits
  const uniqueCats = new Set(Object.values(annMap).map(a => a.category).filter(Boolean))
  const habitsCount = uniqueCats.size
  const prevMonthCats = new Set(
    transactions
      .filter(t => selectedMonth ? !t.txn_date.startsWith(selectedMonth) : false)
      .map(t => annMap[t.id]?.category).filter(Boolean)
  )
  const newThisMonth = [...uniqueCats].filter(c => !prevMonthCats.has(c)).length

  const fmtAmount = (v) => `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
  const hiddenDots = '• • • • • •'
  const monthLabel = selectedMonth ? dayjs(selectedMonth).format('MMMM YYYY') : 'All time'

  return (
    <div className="px-6 py-5 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-[#e2e8f0]">Dashboard</h1>
        <div className="flex items-center gap-3">
          {/* Statement picker */}
          {statements.length > 0 && (
            <select
              value={activeStatement?.id ?? ''}
              onChange={e => {
                const stmt = statements.find(s => s.id === e.target.value) ?? null
                setActiveStatement(stmt)
              }}
              className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] text-sm px-3 py-1.5 rounded-lg focus:outline-none focus:border-[#6366f1] cursor-pointer"
            >
              {statements.map(s => (
                <option key={s.id} value={s.id}>
                  {s.bank_name} — {dayjs(s.statement_month).format('MMM YYYY')}
                </option>
              ))}
            </select>
          )}
          <Link
            to="/upload"
            className="bg-transparent border border-[#4b5268] text-[#e2e8f0] px-4 py-1.5 rounded-lg text-sm hover:border-[#6b7280] transition-colors"
          >
            Upload statement
          </Link>
          <button
            onClick={runAutoAnnotate}
            disabled={autoAnnotating}
            className="bg-[#7c3aed] text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:bg-[#6d28d9] disabled:opacity-50 transition-colors"
          >
            {autoAnnotating
              ? 'Sorting…'
              : activeStatement
                ? `Sort ${activeStatement.bank_name} ${dayjs(activeStatement.statement_month).format('MMM YYYY')}`
                : 'Sort everything'
            }
          </button>
        </div>
      </div>

      {/* Month section */}
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b] mb-3">{monthLabel}</p>
        <div className="grid grid-cols-4 gap-4">
          {/* You Spent */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b] mb-3">You Spent</p>
            <div className="flex items-center gap-3">
              <span className="text-[#94a3b8] text-xl">₹</span>
              {amountsVisible ? (
                <span className="text-2xl font-bold text-[#e2e8f0] tabular-nums">
                  {totalSpend.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                </span>
              ) : (
                <span className="text-lg tracking-widest text-[#64748b]">{hiddenDots}</span>
              )}
              <button
                onClick={() => setAmountsVisible(v => !v)}
                className="ml-auto bg-[#e2e8f0] text-[#0f1117] text-sm font-semibold px-4 py-1.5 rounded-lg hover:bg-white transition-colors"
              >
                {amountsVisible ? 'hide' : 'show'}
              </button>
            </div>
          </div>

          {/* You Earned */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b] mb-3">You Earned</p>
            <div className="flex items-center gap-3">
              <span className="text-[#94a3b8] text-xl">₹</span>
              {amountsVisible ? (
                <span className="text-2xl font-bold text-[#e2e8f0] tabular-nums">
                  {totalIncome.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                </span>
              ) : (
                <span className="text-lg tracking-widest text-[#64748b]">{hiddenDots}</span>
              )}
              <button
                onClick={() => setAmountsVisible(v => !v)}
                className="ml-auto bg-[#e2e8f0] text-[#0f1117] text-sm font-semibold px-4 py-1.5 rounded-lg hover:bg-white transition-colors"
              >
                {amountsVisible ? 'hide' : 'show'}
              </button>
            </div>
          </div>

          {/* Sorted Automatically */}
          <div className="bg-[#13151f] border border-[#2d3748] rounded-xl p-4">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b] mb-3">Sorted Automatically</p>
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-bold text-[#22c55e] tabular-nums">{annotatedCount}</span>
              <span className="text-base text-[#94a3b8]">of {totalSampled}</span>
            </div>
            {reviewCount > 0 && (
              <p className="text-xs text-amber-400 mt-1">{reviewCount} need a quick look</p>
            )}
          </div>

          {/* Habits Picked Up */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b] mb-3">Habits Picked Up</p>
            <div className="flex items-center gap-2">
              <span className="text-3xl font-bold text-[#2dd4bf] tabular-nums">{habitsCount}</span>
              {newThisMonth > 0 && (
                <span className="bg-[#14532d] text-[#4ade80] text-[10px] font-semibold px-2 py-0.5 rounded-full">
                  {newThisMonth} new
                </span>
              )}
            </div>
            <p className="text-xs text-[#64748b] mt-1">across {habitsCount} categories</p>
          </div>
        </div>
      </div>

      {/* How well your copilot knows you */}
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b] mb-3">How Well Your Copilot Knows You</p>
        <div className="grid grid-cols-2 gap-4">
          {/* Confidence by category */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-5">
            <p className="text-sm text-[#e2e8f0] mb-4">Confidence by category</p>
            {loading ? (
              <p className="text-sm text-[#475569]">Loading…</p>
            ) : confByCategory.length === 0 ? (
              <p className="text-sm text-[#475569]">No annotated transactions yet</p>
            ) : (
              <div className="space-y-3">
                {confByCategory.map(({ cat, pct }) => (
                  <div key={cat} className="flex items-center gap-3">
                    <span className="text-sm text-[#cbd5e1] w-36 shrink-0">{cat}</span>
                    <div className="flex-1 h-2 bg-[#1e2235] rounded-full overflow-hidden">
                      <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: confColor(pct) }} />
                    </div>
                    <span className="text-sm font-semibold w-10 text-right shrink-0" style={{ color: confColor(pct) }}>{pct}%</span>
                  </div>
                ))}
              </div>
            )}
            {lowestConf && lowestConf.pct < 75 && (
              <p className="text-xs text-[#64748b] italic mt-4">
                {lowestConf.cat} is still learning — fix a few to help it along
              </p>
            )}
          </div>

          {/* Where your money went */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-5">
            <p className="text-sm text-[#e2e8f0] mb-4">Where your money went in {monthLabel}</p>
            {loading ? (
              <p className="text-sm text-[#475569]">Loading…</p>
            ) : spendRanked.length === 0 ? (
              <p className="text-sm text-[#475569]">No spend data for {monthLabel}</p>
            ) : (
              <div className="space-y-3">
                {spendRanked.map(([cat, amount], idx) => {
                  const pct = totalMonthSpend > 0 ? Math.round((amount / totalMonthSpend) * 100) : 0
                  const color = CAT_COLORS[idx % CAT_COLORS.length]
                  const barPct = totalMonthSpend > 0 ? (amount / totalMonthSpend) * 100 : 0
                  return (
                    <div key={cat} className="flex items-center gap-3">
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: color }} />
                      <span className="text-sm text-[#cbd5e1] w-28 shrink-0 truncate">{cat}</span>
                      <div className="flex-1 h-1.5 bg-[#1e2235] rounded-full overflow-hidden">
                        <div className="h-full rounded-full" style={{ width: `${barPct}%`, backgroundColor: color }} />
                      </div>
                      <span className="text-sm text-[#64748b] w-16 text-right shrink-0">
                        {amountsVisible ? fmtAmount(amount) : hiddenDots}
                      </span>
                      <span className="text-sm text-[#94a3b8] w-8 text-right shrink-0">{pct}%</span>
                    </div>
                  )
                })}
              </div>
            )}
            <div className="flex items-center justify-between mt-4 pt-3 border-t border-[#1e2235]">
              <p className="text-xs text-[#64748b] italic">
                {amountsVisible ? '' : 'Amounts hidden — tap show to reveal'}
              </p>
              <Link to="/insights" className="text-xs text-[#6366f1] hover:text-[#818cf8] transition-colors">
                See full breakdown →
              </Link>
            </div>
          </div>
        </div>
      </div>

      {/* Review queue CTA */}
      {reviewCount > 0 && (
        <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="w-2 h-2 rounded-full bg-amber-400 shrink-0" />
            <p className="text-sm text-[#e2e8f0]">
              <span className="font-semibold">{reviewCount} transaction{reviewCount !== 1 ? 's' : ''}</span>
              {' '}I'm not sure about — a quick look from you will make me much better
            </p>
          </div>
          <Link
            to="/review"
            className="bg-[#1e2235] border border-[#2d3148] text-[#e2e8f0] text-sm font-semibold px-4 py-2 rounded-lg hover:bg-[#252a3d] transition-colors whitespace-nowrap"
          >
            Help me get better →
          </Link>
        </div>
      )}

      {activeTxn && (
        <AnnotationPanel
          txn={activeTxn}
          annotation={activeAnnotation}
          onClose={() => { setActiveTxn(null); setActiveAnnotation(null) }}
          onSaved={() => {}}
        />
      )}
    </div>
  )
}
