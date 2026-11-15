import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Chart as ChartJS, CategoryScale, LinearScale,
  PointElement, LineElement, Tooltip as ChartTooltip, Filler,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { usePeriod, ALL_TIME } from '../contexts/PeriodContext.jsx'
import { usePrivacy } from '../contexts/PrivacyContext.jsx'
import { useAnnotationJob } from '../contexts/AnnotationJobContext.jsx'
import { useDemoData } from '../contexts/DemoDataContext.jsx'
import PeriodPicker from '../components/PeriodPicker.jsx'
import Amount, { Percent, useMoney } from '../components/Amount.jsx'
import OtherInNote from '../components/OtherInNote.jsx'
import Tooltip from '../components/Tooltip.jsx'
import Delta, { tileDelta } from '../components/Delta.jsx'
import { coverageLabel, isInProgress, comparisonLabel } from '../lib/period.js'
import { txnFilterPath } from '../lib/txnLink.js'
import { annotationSummary, summaryDuration } from '../lib/annotationSummary.js'
import { count } from '../lib/plural.js'
import {
  FilePlus2, TrendingUp, HelpCircle,
  CheckCircle2, CalendarClock, ArrowRight, Sparkles,
} from 'lucide-react'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, ChartTooltip, Filler)

const card = 'bg-[#13151f] border border-[#2d3148] rounded-xl'
const cardTitle = 'text-xs font-semibold uppercase tracking-wider text-[#64748b]'

function ordinal(n) {
  const rest = n % 100
  if (rest >= 11 && rest <= 13) return `${n}th`
  return `${n}${['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`
}

// The one sentence the page exists for: how the month went, in plain words.
// Tone (color + verb) carries the judgment so no chart-reading is needed.
// "Kept" is the server's verdict.kept (In - Out - Invested), the same figure
// as the Kept tile and Money Map; what was invested is said next to it.
function VerdictHero({ verdict, period }) {
  const { kept, kept_rate: rate, invested } = verdict
  const monthName = dayjs(period.month).format('MMMM')
  const when = isInProgress(period) ? `${monthName} so far` : monthName
  const tone = rate == null
    ? { color: '#94a3b8' }
    : rate >= 0.2
      ? { color: '#34d399' }
      : rate >= 0
        ? { color: '#fbbf24' }
        : { color: '#f87171' }
  const investedNote = invested > 0 && (
    <>
      {', and invested '}
      <Amount value={invested} decimals={0} className="font-semibold tabular-nums" />
    </>
  )
  return (
    <div className={`${card} relative overflow-hidden p-6`}>
      <div
        className="absolute inset-x-0 top-0 h-1"
        style={{ background: `linear-gradient(90deg, #6366f1, ${tone.color})` }}
      />
      {verdict.money_in <= 0 ? (
        <p className="text-xl text-[#e2e8f0]">
          No money came in during {when} - here is where it went.
        </p>
      ) : kept >= 0 ? (
        <p className="text-xl leading-relaxed text-[#e2e8f0]">
          In {when}, you kept{' '}
          <Amount value={kept} decimals={0} className="font-semibold tabular-nums" />
          {rate != null && (
            <>
              {' '}-{' '}
              <Percent value={rate} className="font-semibold" />
              {' of what came in'}
            </>
          )}
          {investedNote}.
        </p>
      ) : (
        <p className="text-xl leading-relaxed text-[#e2e8f0]">
          In {when}, you spent{' '}
          <Amount value={Math.abs(kept)} decimals={0} className="font-semibold tabular-nums" />
          {' more than came in'}
          {investedNote}.
        </p>
      )}
    </div>
  )
}

// A freshly imported month has no annotations, so every insight the page
// could show would be empty or wrong. Until the pipeline runs, hold back
// everything except the honest cash totals - raw credits in, raw debits out -
// and the button that starts the sort. Same card shell as the verdict hero,
// so finishing feels like this surface waking up, not a page swap.
function SetupStage({ annotation, verdict, month, annotating, onSort }) {
  const many = annotation.total !== 1
  return (
    <>
      <div className={`${card} relative overflow-hidden p-6`}>
        <div
          className="absolute inset-x-0 top-0 h-1"
          style={{ background: 'linear-gradient(90deg, #6366f1, #a78bfa)' }}
        />
        <p className="text-xl leading-relaxed text-[#e2e8f0]">
          {annotation.total} transaction{many ? 's' : ''} from {dayjs(month).format('MMMM')} {many ? 'are' : 'is'} in.
        </p>
        <p className="mt-1 text-sm text-[#94a3b8]">
          Your copilot hasn't sorted them yet - run it to see the full picture.
        </p>
        <button
          onClick={onSort}
          disabled={annotating}
          className="mt-4 inline-flex items-center gap-2 rounded-lg bg-[#7c3aed] px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-[#6d28d9] disabled:cursor-default disabled:opacity-60"
        >
          <Sparkles size={15} /> {annotating ? 'Sorting…' : 'Sort my transactions'}
        </button>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {[['In', verdict.money_in], ['Out', verdict.money_out]].map(([label, value]) => (
          <div key={label} className={`${card} p-4`}>
            <p className={cardTitle}>{label}</p>
            <p className="mt-1 text-xl font-semibold tabular-nums text-[#e2e8f0]">
              <Amount value={value} decimals={0} />
            </p>
          </div>
        ))}
      </div>
    </>
  )
}

function StatTile({ label, value, delta, vs, goodWhenDown, sub, hint }) {
  const body = (
    <Link to="/insights" className={`${card} group block h-full p-4 transition-colors hover:border-[#6366f1]`}>
      <p className={cardTitle}>{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums text-[#e2e8f0]">
        <Amount value={value} decimals={0} />
      </p>
      {delta != null && (
        <p className="mt-1 text-xs">
          <Delta delta={delta} label={vs} goodWhenDown={goodWhenDown} />
        </p>
      )}
      {sub && <p className="mt-0.5 text-xs text-[#475569]">{sub}</p>}
    </Link>
  )
  return hint ? (
    <Tooltip content={hint} className="block h-full w-full">
      {body}
    </Tooltip>
  ) : (
    body
  )
}

// One ranked list of things worth doing, each a sentence and a doorway into
// the page where it gets done. Never more than four; an empty list is a
// reward ("all caught up"), not blank space.
function AttentionList({ items }) {
  return (
    <div className={`${card} overflow-hidden`}>
      <p className={`${cardTitle} px-4 py-3 border-b border-[#2d3148]`}>Needs your attention</p>
      {items.length === 0 ? (
        <div className="flex items-center gap-3 px-4 py-6">
          <CheckCircle2 size={20} className="shrink-0 text-emerald-400" />
          <p className="text-sm text-[#94a3b8]">All caught up - nothing needs you right now.</p>
        </div>
      ) : (
        <ul>
          {items.map(item => (
            <li key={item.key} className="border-b border-[#1a1d27] last:border-0">
              <Link
                to={item.to}
                className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-[#1a1d27]"
              >
                <span
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
                  style={{ backgroundColor: `${item.color}1f`, color: item.color }}
                >
                  <item.icon size={16} />
                </span>
                <span className="min-w-0 flex-1 text-sm text-[#e2e8f0]">{item.text}</span>
                <span className="hidden shrink-0 items-center gap-1 text-xs text-[#6366f1] group-hover:text-[#a5b4fc] sm:flex">
                  {item.cta} <ArrowRight size={13} />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// Balance over time, scoped to the selected month - the same chart Money Map
// shows across all months, narrowed here to how the balance moved through this
// one. The full multi-month view lives on Money Map.
function BalanceCard({ balance, month }) {
  const { hidden } = usePrivacy()
  const { money, tick } = useMoney()
  const start = dayjs(month).startOf('month').format('YYYY-MM-DD')
  const end = dayjs(month).endOf('month').format('YYYY-MM-DD')
  const points = balance.series.filter(p => p.date >= start && p.date <= end)
  const data = {
    labels: points.map(p => p.date),
    datasets: [{
      data: points.map(p => p.balance),
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
          label: item => money(item.raw, { decimals: 0 }),
        },
      },
    },
    scales: {
      x: {
        ticks: {
          color: '#64748b', font: { size: 11 }, maxTicksLimit: 6,
          callback(value) {
            return dayjs(this.getLabelForValue(value)).format('D MMM')
          },
        },
        grid: { display: false },
      },
      y: {
        ticks: {
          color: '#64748b', font: { size: 11 }, maxTicksLimit: 5,
          callback: tick,
        },
        grid: { color: '#1e2235' },
      },
    },
  }
  return (
    <Link to="/insights" className={`${card} group flex flex-col p-4 transition-colors hover:border-[#6366f1]`}>
      <p className={`${cardTitle} mb-3`}>Balance over time</p>
      <div className={`h-52 flex-1 ${hidden ? 'blur-[6px] select-none' : ''}`}>
        {points.length > 1 ? (
          <Line data={data} options={options} />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-[#475569]">
            Balance history appears once statements are imported
          </div>
        )}
      </div>
    </Link>
  )
}

// Committed money: the charges that will show up again next month whether or
// not the user does anything. The header is the server's recurring_summary
// (active monthly items) and the rows are those same items, so they always add
// up; irregular and stopped charges are on Money Map, not in this total.
function CommittedCard({ recurring, summary }) {
  // One row per counterparty: the four SIP legs of one platform read as a
  // single commitment to a person, not four look-alike rows.
  const byName = new Map()
  for (const r of recurring.filter(r => r.active && r.cadence === 'monthly')) {
    const g = byName.get(r.name) ?? { ...r, amount: 0, units: 0 }
    g.amount += r.amount
    g.units += 1
    if (r.last_date > g.last_date) g.last_date = r.last_date
    byName.set(r.name, g)
  }
  const committed = [...byName.values()].sort((a, b) => b.amount - a.amount)
  const shown = committed.slice(0, 5)
  const rest = committed.slice(5)
  return (
    <div className={`${card} overflow-hidden`}>
      <div className="flex items-baseline justify-between px-4 py-3 border-b border-[#2d3148]">
        <p className={cardTitle}>Spoken for each month</p>
        {committed.length > 0 && (
          <p className="text-sm font-semibold tabular-nums text-[#e2e8f0]">
            <Amount value={summary.monthly_total} decimals={0} />
          </p>
        )}
      </div>
      {committed.length === 0 ? (
        <p className="px-4 py-3 text-sm text-[#475569]">
          No monthly commitments spotted yet - they show up after a few months of statements.
        </p>
      ) : (
        <ul>
          {shown.map(r => (
            <li
              key={`${r.name}-${r.amount}`}
              className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm border-b border-[#1a1d27] last:border-0"
            >
              <div className="flex min-w-0 items-center gap-2.5">
                <CalendarClock size={14} className="shrink-0 text-[#64748b]" />
                <span className="truncate text-[#e2e8f0]">{r.name}</span>
                <span className="hidden text-xs text-[#64748b] sm:inline">
                  {r.units > 1
                    ? `${r.units} ${r.category === 'Investments' ? 'SIPs' : 'charges'}`
                    : `around the ${ordinal(dayjs(r.last_date).date())}`}
                </span>
              </div>
              <span className="tabular-nums text-[#94a3b8]">
                <Amount value={r.amount} decimals={0} />
              </span>
            </li>
          ))}
          {rest.length > 0 && (
            <li className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
              <span className="text-[#64748b]">and {rest.length} more</span>
              <span className="tabular-nums text-[#94a3b8]">
                <Amount value={rest.reduce((sum, r) => sum + r.amount, 0)} decimals={0} />
              </span>
            </li>
          )}
        </ul>
      )}
      <Link
        to="/insights"
        className="block border-t border-[#2d3148] px-4 py-2.5 text-xs text-[#6366f1] hover:text-[#a5b4fc]"
      >
        All subscriptions and SIPs on Money Map →
      </Link>
    </div>
  )
}

// One glance backward - the top three spending categories, net of refunds and
// paid-back shares - then a line that accounts for everything else on the way
// to Out (the rest of the categories plus the reconciling rows), then a
// hand-off to Money Map for the full picture.
function TopSpendingCard({ categories, breakdown, month }) {
  const top = categories.slice(0, 3)
  const rest = [...categories.slice(3).map(c => c.net), ...breakdown.extras.map(e => e.total)]
  const maxNet = Math.max(1, ...top.map(c => c.net))
  return (
    <div className={`${card} overflow-hidden`}>
      <p className={`${cardTitle} px-4 py-3 border-b border-[#2d3148]`}>
        Where {dayjs(month).format('MMMM')}'s money went
      </p>
      {top.length === 0 ? (
        <p className="px-4 py-3 text-sm text-[#475569]">No spending recorded this month.</p>
      ) : (
        <ul className="px-4 py-2">
          {top.map(c => (
            <li key={c.category}>
              <Link
                to={txnFilterPath({ category: c.category, month })}
                className="group -mx-1 grid grid-cols-[minmax(7rem,1fr)_2fr_5rem] items-center gap-3 rounded-md px-1 py-2 text-sm transition-colors hover:bg-[#1a1d27]"
              >
                <span className="truncate text-[#e2e8f0] group-hover:text-[#a5b4fc]">{c.category}</span>
                <span className="h-2 overflow-hidden rounded bg-[#1e2235]">
                  <span
                    className="block h-full rounded bg-[#6366f1]"
                    style={{ width: `${Math.min(100, (Math.max(c.net, 0) / maxNet) * 100)}%` }}
                  />
                </span>
                <span className="text-right tabular-nums text-[#e2e8f0]">
                  <Amount value={c.net} decimals={0} />
                </span>
              </Link>
            </li>
          ))}
          {rest.length > 0 && (
            <li className="-mx-1 grid grid-cols-[minmax(7rem,1fr)_2fr_5rem] items-center gap-3 px-1 py-2 text-sm">
              <span className="text-[#64748b]">and {rest.length} more</span>
              <span />
              <span className="text-right tabular-nums text-[#94a3b8]">
                <Amount value={rest.reduce((sum, v) => sum + v, 0)} decimals={0} />
              </span>
            </li>
          )}
        </ul>
      )}
      <Link
        to="/insights"
        className="block border-t border-[#2d3148] px-4 py-2.5 text-xs text-[#6366f1] hover:text-[#a5b4fc]"
      >
        See the full picture on Money Map →
      </Link>
    </div>
  )
}

// Where the copilot is shakiest: the categories your corrections most often
// changed, weakest first, each with the sample size behind it (4.19). Clicking
// through opens those transactions so you can teach it. Replaces the old filler
// "most of your money went to…" card with something actionable.
function AccuracyCard({ rows }) {
  const weak = rows.slice(0, 4)
  return (
    <div className={`${card} overflow-hidden`}>
      <p className={`${cardTitle} px-4 py-3 border-b border-[#2d3148]`}>
        Where I need your help most
      </p>
      {weak.length === 0 ? (
        <p className="px-4 py-4 text-sm text-[#475569] leading-relaxed">
          Not enough corrections yet to spot weak categories. Verify a few in Teach Me
          and the shakiest ones will surface here.
        </p>
      ) : (
        <ul className="px-4 py-2">
          {weak.map(r => {
            const pct = Math.round(r.accuracy * 100)
            const good = pct >= 80
            return (
              <li key={r.category}>
                <Link
                  to={txnFilterPath({ category: r.category })}
                  className="group -mx-1 grid grid-cols-[minmax(7rem,1fr)_2fr_4rem] items-center gap-3 rounded-md px-1 py-2 text-sm transition-colors hover:bg-[#1a1d27]"
                >
                  <span className="truncate text-[#e2e8f0] group-hover:text-[#a5b4fc]">{r.category}</span>
                  <span className="h-2 overflow-hidden rounded bg-[#1e2235]" title={`${r.confirmed} kept, ${r.changed} changed`}>
                    <span
                      className={`block h-full rounded ${good ? 'bg-emerald-500' : pct >= 50 ? 'bg-amber-500' : 'bg-red-500'}`}
                      style={{ width: `${pct}%` }}
                    />
                  </span>
                  <span className="text-right tabular-nums text-[#94a3b8]">
                    {pct}%
                    <span className="block text-[10px] text-[#475569]">n={r.reviewed}</span>
                  </span>
                </Link>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

export default function Dashboard() {
  const toast = useToast()
  const { refresh: refreshDemoData } = useDemoData()
  const { month: periodMonth } = usePeriod()
  const [data, setData] = useState(null)
  // Low-confidence guesses waiting in Teach Me (every month, not just this one).
  const [guessCount, setGuessCount] = useState(0)
  const [accuracy, setAccuracy] = useState([])
  const [loading, setLoading] = useState(true)
  // Any tracked run (this page, another page, or re-attached after a reload)
  // holds the single job slot, so the sort button waits for it.
  const { job, startAnnotation } = useAnnotationJob()
  const [annotating, setAnnotating] = useState(false)
  const [seedingDemo, setSeedingDemo] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  // Month-anchored like Money Map: "All time" falls back to the latest month
  // with data (the server's default), and the picker (allowAll={false}) shows
  // that month rather than a value the figures do not honour.
  const requestMonth = periodMonth && periodMonth !== ALL_TIME ? periodMonth : null

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      try {
        const [summary, counts, acc] = await Promise.all([
          api.get(`/insights${requestMonth ? `?month=${requestMonth}` : ''}`),
          // Counts only: the queue itself can be thousands of rows long.
          api.get('/annotations/review-queue/counts').catch(() => null),
          api.get('/annotations/accuracy').catch(() => []),
        ])
        if (cancelled) return
        setData(summary)
        setGuessCount(counts?.guesses ?? 0)
        setAccuracy(acc)
      } catch (e) {
        if (!cancelled) toast(`Couldn't load your data - ${e.message}`, 'error')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [requestMonth, toast, reloadKey])

  // Sweep every pending transaction, not just one statement: a multi-statement
  // upload should still resolve with one click. On success the refetch flips
  // annotation.annotated above zero and the full dashboard takes over.
  async function sortTransactions() {
    setAnnotating(true)
    try {
      const result = await startAnnotation({})
      const summary = annotationSummary(result)
      toast(summary.message, summary.type, summaryDuration(summary))
      setReloadKey(k => k + 1)
    } catch (e) {
      toast(`Sorting failed - ${e.message}`, 'error')
    } finally {
      setAnnotating(false)
    }
  }

  async function loadDemoData() {
    setSeedingDemo(true)
    try {
      const s = await api.post('/data/demo')
      toast(`Demo loaded: ${count(s.transactions, 'transaction')} across ${count(s.months, 'month')}`, 'success', 5000)
      refreshDemoData()
      setReloadKey(k => k + 1)
    } catch (e) {
      toast(`Couldn't load demo data - ${e.message}`, 'error')
    } finally {
      setSeedingDemo(false)
    }
  }

  const vs = comparisonLabel(data?.comparison)
  const coverage = coverageLabel(data?.period)
  const attention = []
  if (data?.month) {
    // One figure per fact, over one set: this month's unsorted payments and
    // their money. Guesses to check are a different fact (and span every
    // month, as Teach Me does), so they get their own row.
    const unsorted = data.unexplained.count
    if (unsorted > 0) {
      attention.push({
        key: 'unsorted', icon: HelpCircle, color: '#fbbf24', to: '/review',
        text: (
          <>
            {unsorted} {unsorted === 1 ? 'payment' : 'payments'} in {dayjs(data.month).format('MMMM')}{' '}
            {unsorted === 1 ? "isn't" : "aren't"} sorted yet (<Amount value={data.unexplained.total} decimals={0} />)
          </>
        ),
        cta: 'Sort them',
      })
    }
    if (guessCount > 0) {
      attention.push({
        key: 'guesses', icon: Sparkles, color: '#a78bfa', to: '/review',
        text: `${guessCount} ${guessCount === 1 ? 'guess' : 'guesses'} to check, across all months`,
        cta: 'Check them',
      })
    }
    // Statements land as whole months after the bank issues them, so "fresh"
    // means "has last month's statement", not "has today's transactions".
    const latestDataMonth = data.months.at(-1)
    const expected = dayjs().subtract(1, 'month').format('YYYY-MM')
    if (latestDataMonth < expected) {
      const missing = dayjs(expected).format('MMMM')
      attention.push({
        key: 'freshness', icon: FilePlus2, color: '#38bdf8', to: '/upload',
        text: `Your newest statement is ${dayjs(latestDataMonth).format('MMMM')} - add ${missing}'s to stay current`,
        cta: 'Add statement',
      })
    }
    // The server leaves Uncategorized out of what_changed: the unsorted item below covers it.
    const spike = data.what_changed.find(
      c => c.delta >= 2000 && c.current > 1.75 * Math.max(c.previous, 1)
    )
    if (spike) {
      attention.push({
        key: 'spike', icon: TrendingUp, color: '#fb7185',
        to: txnFilterPath({ category: spike.category, month: data.month }),
        text: (
          <>
            {spike.category} ran <Amount value={spike.delta} decimals={0} /> above{' '}
            {comparisonLabel(data.comparison)} - see what drove it
          </>
        ),
        cta: 'Look closer',
      })
    }
  }

  return (
    <div className="px-6 py-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-base font-semibold text-[#e2e8f0]">
          Your money at a glance
          {data?.month && (
            <span className="text-sm font-normal text-[#64748b]"> · {dayjs(data.month).format('MMMM YYYY')}</span>
          )}
          {coverage && (
            <span className="ml-2 rounded-full bg-[#2a2310] px-2 py-0.5 text-xs font-normal text-[#fbbf24]">
              {coverage}
            </span>
          )}
        </h1>
        <div className="flex items-center gap-3">
          <PeriodPicker allowAll={false} shown={data?.month} months={data?.months} />
          <Link
            to="/upload"
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#7c3aed] px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-[#6d28d9]"
          >
            <FilePlus2 size={15} /> Add statement
          </Link>
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-[#475569]">Pulling it together…</p>
      ) : !data?.month ? (
        <div className={`${card} p-8 text-center`}>
          <p className="text-lg text-[#e2e8f0]">Welcome! Let's get your money on the map.</p>
          <p className="mt-2 text-sm text-[#64748b]">
            Upload a bank statement and your copilot will sort every transaction for you.
          </p>
          <div className="mt-4 flex items-center justify-center gap-3">
            <Link
              to="/upload"
              className="inline-block rounded-lg bg-[#7c3aed] px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-[#6d28d9]"
            >
              Add your first statement
            </Link>
            <button
              onClick={loadDemoData}
              disabled={seedingDemo}
              className="inline-block rounded-lg border border-[#2d3148] px-4 py-2 text-sm font-semibold text-[#cbd5e1] transition-colors hover:border-[#6366f1] hover:text-[#e2e8f0] disabled:opacity-50"
            >
              {seedingDemo ? 'Loading…' : 'Load demo data'}
            </button>
          </div>
          <p className="mt-3 text-xs text-[#475569]">
            No statement handy? The demo fills four months of realistic sample data - remove it
            anytime from the banner or Settings › Data.
          </p>
        </div>
      ) : data.annotation?.total > 0 && data.annotation.annotated === 0 ? (
        <SetupStage
          annotation={data.annotation}
          verdict={data.verdict}
          month={data.month}
          annotating={annotating || job != null}
          onSort={sortTransactions}
        />
      ) : (
        <>
          <VerdictHero verdict={data.verdict} period={data.period} />

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile
              label="In" value={data.verdict.money_in}
              delta={tileDelta(data.verdict, 'money_in')} vs={vs}
              sub={data.verdict.other_in > 0 ? <OtherInNote amount={data.verdict.other_in} className="" /> : null}
            />
            <StatTile
              label="Out" value={data.verdict.money_out} goodWhenDown
              delta={tileDelta(data.verdict, 'money_out')} vs={vs}
            />
            <StatTile
              label="Invested" value={data.verdict.invested}
              delta={tileDelta(data.verdict, 'invested')} vs={vs}
            />
            <StatTile
              label="Kept" value={data.verdict.kept}
              delta={tileDelta(data.verdict, 'kept')} vs={vs}
              hint="In - Out - Invested"
            />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
            <div className="lg:col-span-3">
              <AttentionList items={attention.slice(0, 4)} />
            </div>
            <div className="lg:col-span-2">
              <BalanceCard balance={data.balance} month={data.month} />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <CommittedCard recurring={data.recurring} summary={data.recurring_summary} />
            <TopSpendingCard categories={data.categories} breakdown={data.breakdown} month={data.month} />
          </div>

          <AccuracyCard rows={accuracy} />
        </>
      )}
    </div>
  )
}
