import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import dayjs from 'dayjs'
import { api, runAnnotationJob } from '../lib/api.js'
import { isRealFlow } from '../lib/categories.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { useStatement } from '../contexts/StatementContext.jsx'
import AnnotationProgress from '../components/AnnotationProgress.jsx'
import Tooltip from '../components/Tooltip.jsx'
import { HelpCircle } from 'lucide-react'

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
  const [autoAnnotating, setAutoAnnotating] = useState(false)
  const [annotateProgress, setAnnotateProgress] = useState(null)
  const [amountsVisible, setAmountsVisible] = useState(false)

  // Reload transactions whenever the active statement changes
  useEffect(() => {
    if (stmtLoading) return
    async function load() {
      setLoading(true)
      try {
        const params = new URLSearchParams()
        params.set('include', 'annotation')
        if (activeStatement) params.set('statement_id', activeStatement.id)

        const [txns, queue] = await Promise.all([
          api.get(`/transactions?${params}`),
          api.get('/annotations/review-queue'),
        ])
        setTransactions(txns)
        setReviewCount(queue.length)

        const map = {}
        for (const t of txns) {
          if (t.annotation_id) {
            map[t.id] = {
              id: t.annotation_id,
              category: t.category,
              subcategory: t.subcategory,
              source: t.source,
              confidence: t.confidence,
            }
          }
        }
        setAnnMap(map)
      } catch (e) {
        toast(`Couldn't load your data — ${e.message}`, 'error')
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
      const result = await runAnnotationJob(body, job => setAnnotateProgress(job))
      toast(`All done! ${result.rule_matched ?? 0} matched by rules, ${result.llm_annotated ?? 0} figured out by AI`, 'success', 4000)
    } catch (e) {
      toast(`Something went wrong — ${e.message}`, 'error')
    } finally {
      setAutoAnnotating(false)
      setAnnotateProgress(null)
    }
  }

  // The selected month comes from the active statement
  const selectedMonth = activeStatement?.statement_month ?? null

  // Stats — scoped to the active statement's month
  const thisMonth = selectedMonth
    ? transactions.filter(t => t.txn_date.startsWith(selectedMonth))
    : transactions
  // Self-transfers move money between the user's own accounts — exclude them
  // from spend and income so they don't inflate either side (or savings rate).
  const totalSpend = thisMonth.filter(t => t.debit_credit === 'debit' && isRealFlow(annMap[t.id]?.category)).reduce((s, t) => s + Number(t.amount), 0)
  const totalIncome = thisMonth.filter(t => t.debit_credit === 'credit' && isRealFlow(annMap[t.id]?.category)).reduce((s, t) => s + Number(t.amount), 0)
  // Spend by category (this statement's month, debits)
  const spendByCat = {}
  for (const txn of thisMonth) {
    if (txn.debit_credit !== 'debit') continue
    const cat = annMap[txn.id]?.category ?? 'Uncategorized'
    if (!isRealFlow(cat)) continue
    spendByCat[cat] = (spendByCat[cat] ?? 0) + Number(txn.amount)
  }
  const totalMonthSpend = Object.values(spendByCat).reduce((s, v) => s + v, 0)
  const spendRanked = Object.entries(spendByCat).sort((a, b) => b[1] - a[1]).slice(0, 5)

  // Confidence by category — scoped to the active statement's transactions
  const catConfMap = {}
  const catCountMap = {}
  const scopedTxnIds = new Set(thisMonth.map(t => t.id))
  for (const [txnId, ann] of Object.entries(annMap)) {
    if (!scopedTxnIds.has(txnId)) continue
    if (!ann.category || ann.confidence == null) continue
    catConfMap[ann.category] = (catConfMap[ann.category] ?? 0) + Number(ann.confidence)
    catCountMap[ann.category] = (catCountMap[ann.category] ?? 0) + 1
  }
  const confByCategory = Object.entries(catConfMap)
    .map(([cat, total]) => ({ cat, pct: Math.round((total / catCountMap[cat]) * 100) }))
    .sort((a, b) => b.pct - a.pct)
    .slice(0, 5)
  const lowestConf = confByCategory.at(-1) ?? null

  // Savings rate
  const savingsRate = totalIncome > 0 ? Math.round(((totalIncome - totalSpend) / totalIncome) * 100) : null

  // Biggest spike: compare this month's per-category spend vs average across all other months
  const allMonths = [...new Set(transactions.map(t => t.txn_date.slice(0, 7)))].sort()
  const otherMonths = selectedMonth ? allMonths.filter(m => m !== selectedMonth) : []

  let spikeResult = null
  if (otherMonths.length >= 1) {
    // Build per-category averages across other months
    const otherCatTotals = {}
    const otherCatMonthCount = {}
    for (const txn of transactions) {
      if (txn.debit_credit !== 'debit') continue
      if (!otherMonths.includes(txn.txn_date.slice(0, 7))) continue
      const cat = annMap[txn.id]?.category
      if (!cat || cat === 'Uncategorized') continue
      const m = txn.txn_date.slice(0, 7)
      if (!otherCatMonthCount[cat]) otherCatMonthCount[cat] = new Set()
      otherCatMonthCount[cat].add(m)
      otherCatTotals[cat] = (otherCatTotals[cat] ?? 0) + Number(txn.amount)
    }
    const otherCatAvg = {}
    for (const [cat, total] of Object.entries(otherCatTotals)) {
      otherCatAvg[cat] = total / otherCatMonthCount[cat].size
    }

    // Find the category with the biggest deviation from its average (up or down)
    let bestCat = null, bestDeviation = 0
    for (const [cat, thisAmt] of Object.entries(spendByCat)) {
      if (cat === 'Uncategorized') continue
      const avg = otherCatAvg[cat]
      if (!avg || avg < 500) continue // skip tiny/new categories
      const deviation = Math.abs(thisAmt / avg - 1) // e.g. 2x = 1.0 deviation, 0.3x = 0.7 deviation
      if (deviation > bestDeviation) { bestDeviation = deviation; bestCat = cat }
    }

    if (bestCat) {
      const multiple = spendByCat[bestCat] / otherCatAvg[bestCat]
      if (multiple > 1.2) {
        spikeResult = { cat: bestCat, multiple, thisAmt: spendByCat[bestCat], type: 'spike' }
      } else if (multiple < 0.8) {
        spikeResult = { cat: bestCat, multiple, thisAmt: spendByCat[bestCat], type: 'drop' }
      }
    }
  }

  // Fallback: top category this month (when only 1 month of data)
  const topCatFallback = spendRanked[0]
    ? { cat: spendRanked[0][0], pct: totalMonthSpend > 0 ? Math.round((spendRanked[0][1] / totalMonthSpend) * 100) : 0 }
    : null

  const fmtAmount = (v) => `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
  const hiddenDots = '• • • • • •'
  const monthLabel = selectedMonth ? dayjs(selectedMonth).format('MMMM YYYY') : 'All time'

  return (
    <div className="px-6 py-5 space-y-6">
      <AnnotationProgress job={autoAnnotating ? annotateProgress : null} />
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-[#e2e8f0]">Your Money at a Glance</h1>
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
            Add statement
          </Link>
          <Tooltip content="Use AI to automatically categorize transactions in this statement">
            {/* Primary only when nothing is pending review; when the queue is
                non-empty, Teach Me is the primary action and this steps back. */}
            <button
              onClick={runAutoAnnotate}
              disabled={autoAnnotating}
              className={reviewCount > 0
                ? 'bg-[#13151f] border border-[#2d3148] text-[#94a3b8] px-4 py-1.5 rounded-lg text-sm font-medium hover:text-[#e2e8f0] disabled:opacity-50 transition-colors'
                : 'bg-[#7c3aed] text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:bg-[#6d28d9] disabled:opacity-50 transition-colors'}
            >
              {autoAnnotating
                ? 'Categorizing…'
                : activeStatement
                  ? 'Auto-categorize'
                  : 'Auto-categorize all'
              }
            </button>
          </Tooltip>
        </div>
      </div>

      {/* Month section */}
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b] mb-3">{monthLabel}</p>
        <div className="grid grid-cols-4 gap-4">
          {/* You Spent */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b] mb-3">Money spent</p>
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
                {amountsVisible ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          {/* You Earned */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b] mb-3">Money earned</p>
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
                {amountsVisible ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          {/* Savings Rate */}
          <div className="bg-[#13151f] border border-[#2d3748] rounded-xl p-4">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b] mb-3">You Saved</p>
            {savingsRate === null ? (
              <p className="text-sm text-[#475569] mt-1">No income yet to compare</p>
            ) : (
              <>
                <div className="flex items-baseline gap-1">
                  <span
                    className="text-3xl font-bold tabular-nums"
                    style={{ color: savingsRate >= 20 ? '#22c55e' : savingsRate >= 0 ? '#f59e0b' : '#ef4444' }}
                  >
                    {savingsRate}
                  </span>
                  <span className="text-lg text-[#94a3b8]">%</span>
                </div>
                <p className="text-xs text-[#94a3b8] mt-1">
                  {amountsVisible
                    ? `You ${totalIncome - totalSpend >= 0 ? 'kept' : 'overspent'} ${fmtAmount(Math.abs(totalIncome - totalSpend))} this month`
                    : 'Net cash flow hidden'}
                </p>
                <p className="text-xs text-[#64748b] mt-0.5">
                  {savingsRate >= 20 ? 'Nice! You\'re ahead of the curve' : savingsRate >= 0 ? 'Room to grow — small wins add up' : 'Heads up — you spent more than you earned'}
                </p>
              </>
            )}
          </div>

          {/* Biggest Spike / Top Category */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4 flex flex-col justify-between">
            {spikeResult ? (
              <>
                <p className="text-xs text-[#94a3b8] leading-relaxed">
                  {spikeResult.type === 'spike'
                    ? `You spent more on ${spikeResult.cat} than usual`
                    : `${spikeResult.cat} spending dropped — nice!`
                  }
                </p>
                <div className="flex items-baseline gap-1 my-2">
                  <span className={`text-3xl font-bold tabular-nums ${spikeResult.type === 'spike' ? 'text-red-400' : 'text-emerald-400'}`}>
                    {spikeResult.type === 'spike' ? '+' : '-'}{Math.abs(Math.round((spikeResult.multiple - 1) * 100))}%
                  </span>
                </div>
              </>
            ) : topCatFallback ? (
              <>
                <p className="text-xs text-[#94a3b8]">Most of your money went to {topCatFallback.cat}</p>
                <div className="flex items-baseline gap-1 my-2">
                  <span className="text-3xl font-bold tabular-nums text-[#2dd4bf]">{topCatFallback.pct}%</span>
                </div>
              </>
            ) : (
              <p className="text-sm text-[#475569]">Categorize your transactions to unlock insights</p>
            )}
            <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b]">Worth Noticing</p>
          </div>
        </div>
      </div>

      {/* How well your copilot knows you */}
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-widest text-[#64748b] mb-3">Your Copilot is Learning</p>
        <div className="grid grid-cols-2 gap-4">
          {/* Confidence by category */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-5">
            <p className="text-sm text-[#e2e8f0] mb-4 flex items-center gap-1.5">
              Accuracy
              <Tooltip content="How often the AI's auto-categorizations are correct">
                <HelpCircle size={13} className="text-[#475569] hover:text-[#94a3b8] transition-colors cursor-help" />
              </Tooltip>
            </p>
            {loading ? (
              <p className="text-sm text-[#475569]">Pulling it together…</p>
            ) : confByCategory.length === 0 ? (
              <p className="text-sm text-[#475569]">I haven't categorized anything yet — hit Categorize to get started</p>
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
                I'm still learning about {lowestConf.cat} — a few corrections would help a lot
              </p>
            )}
          </div>

          {/* Where your money went */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-5">
            <p className="text-sm text-[#e2e8f0] mb-4">Your top spendings in {monthLabel}</p>
            {loading ? (
              <p className="text-sm text-[#475569]">Pulling it together…</p>
            ) : spendRanked.length === 0 ? (
              <p className="text-sm text-[#475569]">No spending data for {monthLabel} yet</p>
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
                {amountsVisible ? '' : 'Tap show to reveal amounts'}
              </p>
              <Link to="/insights" className="text-xs text-[#6366f1] hover:text-[#818cf8] transition-colors">
                Money Map →
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
              {' '}need your eye — a quick review makes me smarter every time
            </p>
          </div>
          <Link
            to="/review"
            className="bg-[#7c3aed] text-white text-sm font-semibold px-4 py-2 rounded-lg hover:bg-[#6d28d9] transition-colors whitespace-nowrap"
          >
            Teach Me →
          </Link>
        </div>
      )}

    </div>
  )
}
