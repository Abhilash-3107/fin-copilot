import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Chart as ChartJS, CategoryScale, LinearScale,
  PointElement, LineElement, Tooltip, Filler,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { usePeriod, ALL_TIME } from '../contexts/PeriodContext.jsx'
import { usePrivacy } from '../contexts/PrivacyContext.jsx'
import PeriodPicker from '../components/PeriodPicker.jsx'
import Amount, { formatRupees } from '../components/Amount.jsx'
import RecurringPanel from '../components/RecurringPanel.jsx'
import { txnFilterPath } from '../lib/txnLink.js'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler)

const card = 'bg-[#13151f] border border-[#2d3148] rounded-xl'
const cardTitle = 'text-xs font-semibold uppercase tracking-wider text-[#64748b]'

function DeltaBadge({ delta, goodWhenDown = false, suffix = ' vs last month' }) {
  if (!delta) return <span className="text-xs text-[#475569]">—{suffix}</span>
  const up = delta > 0
  const good = goodWhenDown ? !up : up
  return (
    <span className={`text-xs tabular-nums ${good ? 'text-emerald-400' : 'text-red-400'}`}>
      {up ? '▲' : '▼'} <Amount value={Math.abs(delta)} decimals={0} />{suffix}
    </span>
  )
}

function StatTile({ label, value, delta, goodWhenDown, sub }) {
  return (
    <div className={`${card} p-4`}>
      <p className={cardTitle}>{label}</p>
      <p className="text-xl font-semibold text-[#e2e8f0] mt-1 tabular-nums">
        <Amount value={value} decimals={0} />
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-x-2">
        <DeltaBadge delta={delta} goodWhenDown={goodWhenDown} />
        {sub && <span className="text-xs text-[#64748b]">{sub}</span>}
      </div>
    </div>
  )
}

// Row 1: the page's answer to "am I OK this month?". A cash view, so the
// four tiles always reconcile: In = Out + Invested + Kept by construction.
// A friend's payback the pipeline never matched simply lands inside In
// (disclosed via other_in) instead of silently distorting a tile.
function VerdictStrip({ verdict }) {
  const { money_in, money_out, invested, kept, kept_rate, other_in, prev } = verdict
  return (
    <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
      <StatTile
        label="In" value={money_in} delta={money_in - prev.money_in}
        sub={other_in > 0 ? `incl ${formatRupees(other_in, { decimals: 0 })} paid back or from people` : null}
      />
      <StatTile label="Out" value={money_out} delta={money_out - prev.money_out} goodWhenDown />
      <StatTile label="Invested" value={invested} delta={invested - prev.invested} sub="not part of Out" />
      <StatTile
        label="Kept" value={kept} delta={kept - prev.kept}
        sub={kept_rate != null ? `${Math.round(kept_rate * 100)}% of money in` : null}
      />
    </div>
  )
}

function BalanceChart({ balance }) {
  const { hidden } = usePrivacy()
  const series = balance.series
  const account = balance.account
  const data = {
    labels: series.map(p => p.date),
    datasets: [{
      data: series.map(p => p.balance),
      borderColor: '#6366f1',
      backgroundColor: 'rgba(99,102,241,0.12)',
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      pointHitRadius: 12,
      borderWidth: 2,
    }],
  }
  const options = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      tooltip: {
        backgroundColor: '#1e2235', titleColor: '#e2e8f0', bodyColor: '#94a3b8',
        borderColor: '#2d3148', borderWidth: 1, displayColors: false,
        callbacks: {
          title: items => dayjs(items[0].label).format('D MMM YYYY'),
          label: item => formatRupees(item.raw, { decimals: 0 }),
        },
      },
    },
    scales: {
      x: {
        ticks: {
          color: '#64748b', font: { size: 11 }, autoSkip: false,
          // One label per month (on its first data point), so a daily series
          // never renders duplicate month labels.
          callback(value, index) {
            const cur = dayjs(this.getLabelForValue(value)).format('MMM YY')
            const prev = index > 0 ? dayjs(this.getLabelForValue(index - 1)).format('MMM YY') : null
            return cur === prev ? undefined : cur
          },
        },
        grid: { display: false },
      },
      y: {
        ticks: {
          color: '#64748b', font: { size: 11 }, maxTicksLimit: 5,
          callback: v => hidden ? '••' : `₹${Math.round(v / 1000)}k`,
        },
        grid: { color: '#1e2235' },
      },
    },
  }
  return (
    <div className={`${card} p-4`}>
      <div className="flex items-baseline justify-between mb-3">
        <p className={cardTitle}>Balance over time</p>
        {account && <span className="text-xs text-[#64748b]">{account}</span>}
      </div>
      <div className={`h-52 ${hidden ? 'blur-[6px] select-none' : ''}`}>
        {series.length > 1 ? (
          <Line data={data} options={options} />
        ) : (
          <div className="flex items-center justify-center h-full text-sm text-[#475569]">
            Balance history appears once statements are imported
          </div>
        )}
      </div>
    </div>
  )
}

// Row 2b: the top category shifts vs the prior month, as sentences with
// click-through — the "what changed" a person actually opens the page for.
function WhatChanged({ changes, month, prevMonth }) {
  const prevLabel = dayjs(prevMonth).format('MMM')
  return (
    <div className={`${card} p-4`}>
      <p className={`${cardTitle} mb-3`}>What changed vs {prevLabel}</p>
      {changes.length === 0 ? (
        <p className="text-sm text-[#475569]">No meaningful shifts from last month.</p>
      ) : (
        <ul className="space-y-3">
          {changes.map(c => (
            <li key={c.category}>
              <Link
                to={txnFilterPath({ category: c.category, month })}
                className="group flex items-baseline justify-between gap-3 text-sm"
              >
                <span className="text-[#e2e8f0] group-hover:text-[#a5b4fc]">
                  {c.category} {c.delta > 0 ? 'up' : 'down'}{' '}
                  <Amount value={Math.abs(c.delta)} decimals={0} className="tabular-nums" />
                </span>
                <span className="text-xs text-[#64748b] tabular-nums whitespace-nowrap">
                  <Amount value={c.previous} decimals={0} /> → <Amount value={c.current} decimals={0} />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function DeltaShort({ delta }) {
  if (!delta) return <span className="text-xs text-[#475569]">—</span>
  const up = delta > 0
  return (
    <span className={`text-xs tabular-nums ${up ? 'text-red-400' : 'text-emerald-400'}`}>
      {up ? '▲' : '▼'} <Amount value={Math.abs(delta)} decimals={0} />
    </span>
  )
}

// Row 3: one component instead of the old chart + table pair. Each category
// row carries the bar, the net amount, the month-over-month delta, and an
// expandable subcategory drill-down. Amounts are net of refunds and shares
// friends paid back; where that netting applied, the gross is disclosed.
function CategoryBreakdown({ categories, unexplained, month }) {
  const [expanded, setExpanded] = useState(null)
  const maxNet = Math.max(1, ...categories.map(c => Math.abs(c.net)))
  return (
    <div className={`${card} overflow-hidden`}>
      <p className={`${cardTitle} px-4 py-3 border-b border-[#2d3148]`}>
        Where it went — {dayjs(month).format('MMMM YYYY')}
      </p>
      {categories.length === 0 ? (
        <p className="px-4 py-3 text-sm text-[#475569]">No spending recorded this month.</p>
      ) : (
        <ul>
          {categories.map(c => (
            <li key={c.category} className="border-b border-[#1a1d27] last:border-0">
              <div className="px-4 py-2.5 grid grid-cols-[minmax(10rem,1fr)_2fr_7rem_7rem] items-center gap-3 text-sm">
                <button
                  onClick={() => setExpanded(expanded === c.category ? null : c.category)}
                  className="text-left text-[#e2e8f0] hover:text-[#a5b4fc] truncate cursor-pointer"
                  title="Show subcategories"
                >
                  <span className="text-[#475569] mr-1.5">{expanded === c.category ? '▾' : '▸'}</span>
                  {c.category}
                </button>
                <div className="h-2 rounded bg-[#1e2235] overflow-hidden">
                  <div
                    className="h-full rounded bg-[#6366f1]"
                    style={{ width: `${Math.min(100, (Math.max(c.net, 0) / maxNet) * 100)}%` }}
                  />
                </div>
                <span className="text-right tabular-nums text-[#e2e8f0]">
                  <Amount value={c.net} decimals={0} />
                </span>
                <span className="text-right">
                  <DeltaShort delta={c.delta} />
                </span>
              </div>
              {c.offsets > 0 && (
                <p className="px-4 pb-2 -mt-1 text-xs text-[#64748b]">
                  gross <Amount value={c.gross} decimals={0} />, of which{' '}
                  <Amount value={c.offsets} decimals={0} /> was refunded or paid back
                </p>
              )}
              {expanded === c.category && (
                <ul className="pb-2">
                  {c.subcategories.map(s => (
                    <li key={s.name} className="px-4 py-1 pl-10 flex items-baseline justify-between text-xs">
                      <span className="text-[#94a3b8]">
                        {s.name} <span className="text-[#475569]">× {s.count}</span>
                      </span>
                      <span className="tabular-nums text-[#94a3b8]"><Amount value={s.total} decimals={0} /></span>
                    </li>
                  ))}
                  <li className="px-4 pt-1 pl-10">
                    <Link
                      to={txnFilterPath({ category: c.category, month })}
                      className="text-xs text-[#6366f1] hover:text-[#a5b4fc]"
                    >
                      View transactions →
                    </Link>
                  </li>
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
      {unexplained.count > 0 && (
        <Link
          to="/review"
          className="block px-4 py-2.5 border-t border-[#2d3148] text-xs text-[#f59e0b] hover:text-[#fbbf24]"
        >
          <Amount value={unexplained.total} decimals={0} /> across {unexplained.count} transaction
          {unexplained.count === 1 ? '' : 's'} is unexplained — review them →
        </Link>
      )}
    </div>
  )
}

// Row 5: net position per named person. Intent is unknowable without links,
// so the framing stays "between you and X", never "X owes you". Shares paid
// back inside expense groups are already netted out server-side.
function PeoplePanel({ people }) {
  return (
    <div className={`${card} overflow-hidden`}>
      <p className={`${cardTitle} px-4 py-3 border-b border-[#2d3148]`}>Between you and your people</p>
      {people.items.length === 0 ? (
        <p className="px-4 py-3 text-sm text-[#475569]">
          No transfers matched to people yet —{' '}
          <Link to="/people" className="text-[#6366f1] hover:text-[#a5b4fc]">add people</Link>{' '}
          and their UPI handles to unlock this.
        </p>
      ) : (
        <ul>
          {people.items.map(p => (
            <li
              key={p.id}
              className="px-4 py-2.5 border-b border-[#1a1d27] last:border-0 flex items-center justify-between gap-3 text-sm"
            >
              <div className="min-w-0">
                <p className="text-[#e2e8f0] truncate">
                  {p.name}
                  {p.relationship && <span className="ml-2 text-xs text-[#64748b]">{p.relationship}</span>}
                </p>
                <p className="text-xs text-[#64748b] tabular-nums">
                  sent <Amount value={p.sent} decimals={0} /> · received <Amount value={p.received} decimals={0} /> · {p.count} transfers
                </p>
              </div>
              <span
                className={`tabular-nums whitespace-nowrap ${p.net >= 0 ? 'text-emerald-400' : 'text-red-400'}`}
                title={p.net >= 0 ? 'You received more than you sent' : 'You sent more than you received'}
              >
                {p.net >= 0 ? '+' : '−'}<Amount value={Math.abs(p.net)} decimals={0} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// Row 6: merchants with order counts and average ticket, on canonical
// counterparty keys so ZOMATO and ZOMATO LIMITED are one merchant.
function MerchantsPanel({ merchants, month }) {
  return (
    <div className={`${card} overflow-hidden`}>
      <p className={`${cardTitle} px-4 py-3 border-b border-[#2d3148]`}>
        Top merchants — {dayjs(month).format('MMMM YYYY')}
      </p>
      {merchants.length === 0 ? (
        <p className="px-4 py-3 text-sm text-[#475569]">No merchant spending this month.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr>
              {['Merchant', 'Total', 'Orders', 'Avg order'].map((h, i) => (
                <th
                  key={h}
                  className={`px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235] ${i === 0 ? 'text-left' : 'text-right'}`}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {merchants.map(m => (
              <tr key={m.name} className="border-b border-[#1a1d27] last:border-0">
                <td className="px-4 py-2">
                  <Link
                    to={txnFilterPath({ merchant: m.name, month })}
                    className="text-[#e2e8f0] hover:text-[#a5b4fc]"
                  >
                    {m.name}
                  </Link>
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-[#e2e8f0]"><Amount value={m.total} decimals={0} /></td>
                <td className="px-4 py-2 text-right tabular-nums text-[#94a3b8]">{m.count}</td>
                <td className="px-4 py-2 text-right tabular-nums text-[#94a3b8]"><Amount value={m.avg} decimals={0} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export default function Insights() {
  const toast = useToast()
  const { month: periodMonth } = usePeriod()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  // The page is month-anchored: every number answers "how was this month".
  // "All time" falls back to the latest month with data (the server's default).
  const requestMonth = periodMonth && periodMonth !== ALL_TIME ? periodMonth : null

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      try {
        const summary = await api.get(`/insights${requestMonth ? `?month=${requestMonth}` : ''}`)
        if (!cancelled) setData(summary)
      } catch (e) {
        if (!cancelled) toast(`Couldn't load your insights — ${e.message}`, 'error')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [requestMonth, toast])

  return (
    <div className="px-6 py-5 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-[#e2e8f0]">
          Money Map
          {data?.month && (
            <span className="text-sm font-normal text-[#64748b]"> — {dayjs(data.month).format('MMMM YYYY')}</span>
          )}
        </h1>
        <PeriodPicker />
      </div>

      {loading ? (
        <p className="text-sm text-[#475569]">Crunching your numbers…</p>
      ) : !data?.month ? (
        <p className="text-sm text-[#475569]">Nothing here yet — upload a statement to get started.</p>
      ) : (
        <>
          <VerdictStrip verdict={data.verdict} />

          <div className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-4">
            <BalanceChart balance={data.balance} />
            <WhatChanged changes={data.what_changed} month={data.month} prevMonth={data.prev_month} />
          </div>

          <CategoryBreakdown categories={data.categories} unexplained={data.unexplained} month={data.month} />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 items-start">
            <RecurringPanel items={data.recurring} />
            <PeoplePanel people={data.people} />
          </div>

          <MerchantsPanel merchants={data.merchants} month={data.month} />
        </>
      )}
    </div>
  )
}
