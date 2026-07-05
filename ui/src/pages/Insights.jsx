import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Chart as ChartJS,
  ArcElement, CategoryScale, LinearScale, BarElement,
  PointElement, LineElement, Tooltip, Legend, Filler,
} from 'chart.js'
import { Bar, Line } from 'react-chartjs-2'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { isRealFlow, isShopping } from '../lib/categories.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { usePeriod, ALL_TIME } from '../contexts/PeriodContext.jsx'
import PeriodPicker from '../components/PeriodPicker.jsx'
import Amount from '../components/Amount.jsx'
import RecurringPanel from '../components/RecurringPanel.jsx'
import { txnFilterPath } from '../lib/txnLink.js'

ChartJS.register(ArcElement, CategoryScale, LinearScale, BarElement, PointElement, LineElement, Tooltip, Legend, Filler)

const CHART_COLORS = [
  '#6366f1','#8b5cf6','#ec4899','#f59e0b','#10b981','#3b82f6','#ef4444','#14b8a6',
  '#a3e635','#fb923c','#e879f9','#34d399',
]

const CHART_OPTS = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { labels: { color: '#94a3b8', font: { size: 11 }, boxWidth: 12 } },
    tooltip: {
      backgroundColor: '#1e2235',
      titleColor: '#e2e8f0',
      bodyColor: '#94a3b8',
      borderColor: '#2d3148',
      borderWidth: 1,
    },
  },
  scales: {
    x: { ticks: { color: '#64748b', font: { size: 11 } }, grid: { color: '#1e2235' } },
    y: { ticks: { color: '#64748b', font: { size: 11 } }, grid: { color: '#1e2235' } },
  },
}

function buildAnnotationMap(txns) {
  const map = {}
  for (const t of txns) {
    if (t.annotation_id) {
      map[t.id] = {
        category: t.category,
        subcategory: t.subcategory,
        merchant: t.merchant,
      }
    }
  }
  return map
}

export default function Insights() {
  const toast = useToast()
  const navigate = useNavigate()
  const { month } = usePeriod()
  const [transactions, setTransactions] = useState([])
  const [annMap, setAnnMap] = useState({})
  const [insights, setInsights] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        // The /insights payload carries server-computed recurrence, running
        // balance and category deltas that aren't worth recomputing client-side.
        const [txns, summary] = await Promise.all([
          api.get('/transactions?include=annotation'),
          api.get('/insights'),
        ])
        setTransactions(txns)
        setAnnMap(buildAnnotationMap(txns))
        setInsights(summary)
      } catch (e) {
        toast(`Couldn't load your data — ${e.message}`, 'error')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  // The shared period drives the slice. "All time" aggregates every month; a
  // specific month scopes the tables to it while the trend charts stay anchored
  // at that month.
  const isAll = !month || month === ALL_TIME
  const latestMonth = transactions.length
    ? transactions.map(t => t.txn_date.slice(0, 7)).sort().at(-1)
    : dayjs().format('YYYY-MM')
  const anchorMonth = isAll ? latestMonth : month
  const periodLabel = isAll ? 'All time' : dayjs(anchorMonth).format('MMMM YYYY')
  // Only carry a month into deep links when a specific month is selected.
  const monthParam = isAll ? undefined : anchorMonth

  // Filter to the selected month (or all)
  const monthTxns = isAll ? transactions : transactions.filter(t => t.txn_date.startsWith(anchorMonth))
  const monthDebits = monthTxns.filter(t => t.debit_credit === 'debit')

  // Category breakdown — exclude self-transfers (money that stays with the user)
  const catTotals = {}
  for (const txn of monthDebits) {
    const ann = annMap[txn.id]
    const cat = ann?.category ?? 'Uncategorized'
    if (!isRealFlow(cat)) continue
    catTotals[cat] = (catTotals[cat] ?? 0) + Number(txn.amount)
  }
  const catSorted = Object.entries(catTotals).sort((a, b) => b[1] - a[1])
  const totalSpend = catSorted.reduce((s, [, v]) => s + v, 0)

  // Monthly trend (6 months ending at the anchor month) — same self-transfer exclusion
  const months6 = Array.from({ length: 6 }, (_, i) => dayjs(anchorMonth).subtract(5 - i, 'month').format('YYYY-MM'))
  const monthlyDebits = months6.map(m =>
    transactions.filter(t => t.txn_date.startsWith(m) && t.debit_credit === 'debit' && isRealFlow(annMap[t.id]?.category)).reduce((s, t) => s + Number(t.amount), 0)
  )
  const monthlyCredits = months6.map(m =>
    transactions.filter(t => t.txn_date.startsWith(m) && t.debit_credit === 'credit').reduce((s, t) => s + Number(t.amount), 0)
  )

  // Top merchants — normalized merchant only, and exclude self-transfers +
  // investments so "where you shop" isn't dominated by money moved to yourself.
  const merchantTotals = {}
  for (const txn of monthDebits) {
    const ann = annMap[txn.id]
    if (!ann?.merchant || !isShopping(ann.category)) continue
    merchantTotals[ann.merchant] = (merchantTotals[ann.merchant] ?? 0) + Number(txn.amount)
  }
  const topMerchants = Object.entries(merchantTotals).sort((a, b) => b[1] - a[1]).slice(0, 10)

  const catChartLabels = catSorted.slice(0, 10).map(([k]) => k)
  const catChartData = {
    labels: catChartLabels,
    datasets: [{
      label: 'Amount',
      data: catSorted.slice(0, 10).map(([, v]) => v),
      backgroundColor: CHART_COLORS,
      borderWidth: 0,
    }],
  }

  // Running account balance over the full history (verified balances on every
  // parsed row, downsampled server-side).
  const balanceSeries = insights?.balance ?? []
  const balanceData = {
    labels: balanceSeries.map(p => dayjs(p.date).format('DD MMM YY')),
    datasets: [{
      label: 'Balance',
      data: balanceSeries.map(p => p.balance),
      borderColor: '#6366f1',
      backgroundColor: 'rgba(99,102,241,0.12)',
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
    }],
  }

  // Clicking a category bar deep-links to that category's transactions.
  const catChartOptions = {
    ...CHART_OPTS,
    indexAxis: 'y',
    onClick: (_evt, elements) => {
      if (!elements.length) return
      const cat = catChartLabels[elements[0].index]
      if (cat) navigate(txnFilterPath({ category: cat, month: monthParam }))
    },
  }

  const trendData = {
    labels: months6.map(m => dayjs(m).format('MMM YY')),
    datasets: [
      {
        label: 'Spent',
        data: monthlyDebits,
        borderColor: '#ef4444',
        backgroundColor: 'rgba(239,68,68,0.1)',
        fill: true,
        tension: 0.4,
        pointRadius: 3,
      },
      {
        label: 'Earned',
        data: monthlyCredits,
        borderColor: '#22c55e',
        backgroundColor: 'rgba(34,197,94,0.1)',
        fill: true,
        tension: 0.4,
        pointRadius: 3,
      },
    ],
  }

  const stackedData = {
    labels: months6.map(m => dayjs(m).format('MMM YY')),
    datasets: [
      { label: 'Spent', data: monthlyDebits, backgroundColor: 'rgba(239,68,68,0.7)', stack: 'a' },
      { label: 'Earned', data: monthlyCredits, backgroundColor: 'rgba(34,197,94,0.7)', stack: 'b' },
    ],
  }

  return (
    <div className="px-6 py-5 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-[#e2e8f0]">Money Map</h1>
        <PeriodPicker />
      </div>

      {loading ? (
        <p className="text-sm text-[#475569]">Crunching your numbers…</p>
      ) : (
        <>
          {/* Charts row */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] mb-3">
                Where it went — {periodLabel}
              </p>
              <div className="h-60">
                {catSorted.length > 0 ? (
                  <Bar data={catChartData} options={catChartOptions} />
                ) : (
                  <div className="flex items-center justify-center h-full text-sm text-[#475569]">Nothing here yet — categorize some transactions first</div>
                )}
              </div>
            </div>
            <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] mb-3">Your Money Flow</p>
              <div className="h-60">
                <Line data={trendData} options={CHART_OPTS} />
              </div>
            </div>
          </div>

          {/* Balance over time */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] mb-3">Account Balance Over Time</p>
            <div className="h-56">
              {balanceSeries.length > 1 ? (
                <Line data={balanceData} options={CHART_OPTS} />
              ) : (
                <div className="flex items-center justify-center h-full text-sm text-[#475569]">Balance history appears once statements are imported</div>
              )}
            </div>
          </div>

          {/* Stacked bar */}
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] mb-3">Earned vs Spent</p>
            <div className="h-48">
              <Bar data={stackedData} options={CHART_OPTS} />
            </div>
          </div>

          {/* Tables row */}
          <div className="grid grid-cols-2 gap-4">
            {/* Top merchants */}
            <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
              <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] px-4 py-3 border-b border-[#2d3148]">
                Where You Shop Most — {periodLabel}
              </p>
              {topMerchants.length === 0 ? (
                <p className="px-4 py-3 text-sm text-[#475569]">No merchant data yet — categorize some transactions to unlock this</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr>
                      <th className="px-4 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">Merchant</th>
                      <th className="px-4 py-2 text-right text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topMerchants.map(([merchant, total]) => (
                      <tr
                        key={merchant}
                        onClick={() => navigate(txnFilterPath({ merchant, month: monthParam }))}
                        className="border-b border-[#1a1d27] cursor-pointer hover:bg-[#1a1d27] transition-colors"
                      >
                        <td className="px-4 py-2 text-[#e2e8f0]">{merchant}</td>
                        <td className="px-4 py-2 text-right text-red-400 tabular-nums">
                          <Amount value={total} decimals={0} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* Category breakdown */}
            <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
              <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] px-4 py-3 border-b border-[#2d3148]">
                Spending by Category
              </p>
              {catSorted.length === 0 ? (
                <p className="px-4 py-3 text-sm text-[#475569]">No data for this month yet</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr>
                      <th className="px-4 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">Category</th>
                      <th className="px-4 py-2 text-right text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">Total</th>
                      <th className="px-4 py-2 text-right text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {catSorted.map(([cat, total]) => (
                      <tr
                        key={cat}
                        onClick={() => navigate(txnFilterPath({ category: cat, month: monthParam }))}
                        className="border-b border-[#1a1d27] cursor-pointer hover:bg-[#1a1d27] transition-colors"
                      >
                        <td className="px-4 py-2 text-[#e2e8f0]">{cat}</td>
                        <td className="px-4 py-2 text-right text-red-400 tabular-nums">
                          <Amount value={total} decimals={0} />
                        </td>
                        <td className="px-4 py-2 text-right text-[#94a3b8] tabular-nums">
                          {totalSpend > 0 ? Math.round((total / totalSpend) * 100) : 0}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* Recurring & subscriptions (full-history commitments) */}
          <RecurringPanel items={insights?.recurring ?? []} />
        </>
      )}
    </div>
  )
}
