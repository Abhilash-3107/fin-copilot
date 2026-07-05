import { useEffect, useState } from 'react'
import {
  Chart as ChartJS,
  ArcElement, CategoryScale, LinearScale, BarElement,
  PointElement, LineElement, Tooltip, Legend, Filler,
} from 'chart.js'
import { Bar, Doughnut, Line } from 'react-chartjs-2'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { isRealFlow, isShopping } from '../lib/categories.js'
import { useToast } from '../contexts/ToastContext.jsx'

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
  const [selectedMonth, setSelectedMonth] = useState(dayjs().format('YYYY-MM'))
  const [transactions, setTransactions] = useState([])
  const [annMap, setAnnMap] = useState({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        const txns = await api.get('/transactions?include=annotation')
        setTransactions(txns)
        setAnnMap(buildAnnotationMap(txns))
        if (txns.length > 0) {
          const latestMonth = txns
            .map(t => t.txn_date.slice(0, 7))
            .sort()
            .at(-1)
          setSelectedMonth(latestMonth)
        }
      } catch (e) {
        toast(`Couldn't load your data — ${e.message}`, 'error')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  // Filter to selected month
  const monthTxns = transactions.filter(t => t.txn_date.startsWith(selectedMonth))
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

  // Monthly trend (6 months ending at selectedMonth) — same self-transfer exclusion
  const months6 = Array.from({ length: 6 }, (_, i) => dayjs(selectedMonth).subtract(5 - i, 'month').format('YYYY-MM'))
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

  const catChartData = {
    labels: catSorted.slice(0, 10).map(([k]) => k),
    datasets: [{
      label: 'Amount',
      data: catSorted.slice(0, 10).map(([, v]) => v),
      backgroundColor: CHART_COLORS,
      borderWidth: 0,
    }],
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
        <input
          type="month"
          value={selectedMonth}
          onChange={e => setSelectedMonth(e.target.value)}
          className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1]"
        />
      </div>

      {loading ? (
        <p className="text-sm text-[#475569]">Crunching your numbers…</p>
      ) : (
        <>
          {/* Charts row */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] mb-3">
                Where it went — {dayjs(selectedMonth).format('MMMM YYYY')}
              </p>
              <div className="h-60">
                {catSorted.length > 0 ? (
                  <Bar data={catChartData} options={{ ...CHART_OPTS, indexAxis: 'y' }} />
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
                Where You Shop Most — {dayjs(selectedMonth).format('MMMM YYYY')}
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
                      <tr key={merchant} className="border-b border-[#1a1d27]">
                        <td className="px-4 py-2 text-[#e2e8f0]">{merchant}</td>
                        <td className="px-4 py-2 text-right text-red-400 tabular-nums">
                          ₹{total.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
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
                      <tr key={cat} className="border-b border-[#1a1d27]">
                        <td className="px-4 py-2 text-[#e2e8f0]">{cat}</td>
                        <td className="px-4 py-2 text-right text-red-400 tabular-nums">
                          ₹{total.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
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
        </>
      )}
    </div>
  )
}
