import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// Hermetic mocks: no backend, no canvas (chart.js needs a real 2d context).
const { mockApi, mockRunAnnotationJob } = vi.hoisted(() => ({
  mockApi: { get: vi.fn() },
  mockRunAnnotationJob: vi.fn(),
}))
vi.mock('../lib/api.js', () => ({
  api: mockApi,
  runAnnotationJob: mockRunAnnotationJob,
  pollAnnotationJob: vi.fn(),
  getActiveAnnotationJob: vi.fn().mockResolvedValue(null),
  ApiError: class ApiError extends Error {},
}))
// The stub must be referentially stable: the page keeps toast in its fetch
// effect deps, so a new function per render would refetch forever.
vi.mock('../contexts/ToastContext.jsx', () => {
  const noop = () => {}
  return {
    ToastProvider: ({ children }) => children,
    useToast: () => noop,
  }
})
vi.mock('react-chartjs-2', () => ({
  Line: () => <div data-testid="balance-sparkline" />,
}))

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import { StatementProvider } from '../contexts/StatementContext.jsx'
import { PeriodProvider } from '../contexts/PeriodContext.jsx'
import { AnnotationJobProvider } from '../contexts/AnnotationJobContext.jsx'
import Dashboard from './Dashboard.jsx'

const SUMMARY = {
  month: '2026-05',
  prev_month: '2026-04',
  months: ['2026-04', '2026-05'],
  verdict: {
    money_in: 89403, money_out: 50738, invested: 30000, kept: 8665,
    kept_rate: 0.0969, earned: 89403, other_in: 0,
    prev: {
      money_in: 57583, money_out: 26006, invested: 30000, kept: 1577,
      kept_rate: 0.0274, earned: 57583, other_in: 0,
    },
  },
  what_changed: [
    { category: 'Travel', delta: 11249, current: 14207, previous: 2958 },
  ],
  categories: [
    { category: 'Food & Dining', gross: 20500, net: 20500, offsets: 0, count: 40, prev_net: 16000, delta: 4500, subcategories: [] },
    { category: 'Travel', gross: 14207, net: 14207, offsets: 0, count: 6, prev_net: 2958, delta: 11249, subcategories: [] },
  ],
  recurring: [
    { name: 'INDmoney', category: 'Investments', amount: 25000, months_seen: 6, months_span: 6, cadence: 'monthly', last_date: '2026-05-01', active: true },
    { name: 'Apple', category: 'Subscriptions', amount: 1999, months_seen: 4, months_span: 4, cadence: 'monthly', last_date: '2026-05-15', active: true },
    { name: 'LinkedIn', category: 'Subscriptions', amount: 499, months_seen: 3, months_span: 5, cadence: 'monthly', last_date: '2026-02-10', active: false },
  ],
  people: {
    items: [
      { id: 'p1', name: 'sanya', relationship: 'friend', sent: 19686, received: 23468, net: 3782, count: 20, last_date: '2026-05-20' },
    ],
    unmatched: { sent: 0, received: 0, count: 0 },
  },
  merchants: [],
  balance: {
    account: 'Kotak',
    series: [
      { date: '2026-04-30', balance: 71000 },
      { date: '2026-05-10', balance: 80000 },
      { date: '2026-05-20', balance: 85000 },
      { date: '2026-05-31', balance: 90770 },
    ],
  },
  unexplained: { count: 3, total: 620 },
  annotation: { total: 120, annotated: 117 },
}

// The same month before the pipeline has run: raw cash totals only.
const PENDING_SUMMARY = {
  ...SUMMARY,
  verdict: {
    ...SUMMARY.verdict,
    money_in: 89403, money_out: 80738, invested: 0, kept: 8665,
  },
  categories: [],
  recurring: [],
  what_changed: [],
  unexplained: { count: 120, total: 80738 },
  annotation: { total: 120, annotated: 0 },
}

function renderDashboard() {
  render(
    <MemoryRouter>
      <PrivacyProvider>
        <StatementProvider>
          <PeriodProvider>
            <AnnotationJobProvider>
              <Dashboard />
            </AnnotationJobProvider>
          </PeriodProvider>
        </StatementProvider>
      </PrivacyProvider>
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  mockApi.get.mockImplementation(path => {
    if (path.startsWith('/insights')) return Promise.resolve(SUMMARY)
    if (path === '/annotations/review-queue') return Promise.resolve(new Array(64))
    if (path === '/statements') return Promise.resolve([])
    return Promise.resolve([])
  })
})

describe('Dashboard', () => {
  it('renders the verdict sentence and server-computed tiles', async () => {
    renderDashboard()
    // The hero's "kept" is everything that stayed: invested 30,000 + kept 8,665.
    expect(
      await screen.findByText((_, el) => el.tagName === 'P' && /In May, you kept ₹38,665/.test(el.textContent))
    ).toBeInTheDocument()
    expect(screen.getByText('43%')).toBeInTheDocument()
    // Tiles show the insights verdict, not client-side sums.
    expect(screen.getAllByText('₹50,738').length).toBeGreaterThan(0)
    expect(screen.getAllByText('₹89,403').length).toBeGreaterThan(0)
  })

  it('shows invested and kept tiles without arithmetic explainers', async () => {
    renderDashboard()
    await screen.findByText('Invested')
    // The dashboard tiles stay clean: no "not part of Out" or other explainer.
    expect(screen.queryByText('not part of Out')).not.toBeInTheDocument()
    expect(screen.queryByText(/went into investments/)).not.toBeInTheDocument()
    expect(screen.queryByText(/earlier months/)).not.toBeInTheDocument()
  })

  it('shows a month-scoped balance-over-time chart with no headline number', async () => {
    renderDashboard()
    // The month-anchored chart renders (two+ points inside May), titled like
    // Money Map. The old month-end balance headline and delta are gone.
    expect(await screen.findByText('Balance over time')).toBeInTheDocument()
    expect(screen.getByTestId('balance-sparkline')).toBeInTheDocument()
    expect(screen.queryByText('₹90,770')).not.toBeInTheDocument()
    expect(screen.queryByText(/vs end of April/)).not.toBeInTheDocument()
  })

  it('ranks the attention items with the review queue first', async () => {
    renderDashboard()
    const review = await screen.findByRole('link', { name: /64 transactions need a quick look/ })
    expect(review).toHaveAttribute('href', '/review')
    // Data is from May but "today" is later, so the freshness nudge fires.
    expect(screen.getByRole('link', { name: /newest statement is May/ })).toHaveAttribute('href', '/upload')
    // Travel is 4.8x April: the spike item deep-links into filtered transactions.
    expect(screen.getByRole('link', { name: /Travel ran ₹11,249 above April/ }))
      .toHaveAttribute('href', '/transactions?category=Travel&month=2026-05')
    expect(screen.getByRole('link', { name: /has no category yet/ })).toHaveAttribute('href', '/review')
    // Capped at four: the people item (rank five) drops off.
    expect(screen.queryByText(/Between you and sanya/)).not.toBeInTheDocument()
  })

  it('shows all-caught-up when nothing needs attention', async () => {
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) {
        return Promise.resolve({
          ...SUMMARY,
          months: ['2026-04', dayjsNowMinusOne()],
          what_changed: [],
          unexplained: { count: 0, total: 0 },
          people: { items: [], unmatched: { sent: 0, received: 0, count: 0 } },
        })
      }
      if (path === '/annotations/review-queue') return Promise.resolve([])
      return Promise.resolve([])
    })
    renderDashboard()
    expect(await screen.findByText(/All caught up/)).toBeInTheDocument()
  })

  it('lists only active monthly commitments with their total', async () => {
    renderDashboard()
    expect(await screen.findByText('INDmoney')).toBeInTheDocument()
    expect(screen.getByText('Apple')).toBeInTheDocument()
    expect(screen.queryByText('LinkedIn')).not.toBeInTheDocument() // stopped
    expect(screen.getByText('₹26,999')).toBeInTheDocument() // total excludes the lapsed one
    expect(screen.getByText(/around the 15th/)).toBeInTheDocument()
  })

  it('hands off to Money Map from the spending glance', async () => {
    renderDashboard()
    const cat = await screen.findByRole('link', { name: /Food & Dining/ })
    expect(cat).toHaveAttribute('href', '/transactions?category=Food+%26+Dining&month=2026-05')
    expect(screen.getByRole('link', { name: /full picture on Money Map/ })).toHaveAttribute('href', '/insights')
  })

  it('shows the onboarding welcome when there is no data at all', async () => {
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) return Promise.resolve({ months: [], month: null })
      return Promise.resolve([])
    })
    renderDashboard()
    expect(await screen.findByRole('link', { name: /Add your first statement/ })).toHaveAttribute('href', '/upload')
  })

  it('holds back insights until the month has been annotated', async () => {
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) return Promise.resolve(PENDING_SUMMARY)
      return Promise.resolve([])
    })
    renderDashboard()
    expect(await screen.findByText(/120 transactions from May are in/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Sort my transactions/ })).toBeInTheDocument()
    // Only the honest cash totals show: raw In and Out, nothing derived.
    expect(screen.getByText('In')).toBeInTheDocument()
    expect(screen.getByText('₹80,738')).toBeInTheDocument()
    expect(screen.queryByText('Invested')).not.toBeInTheDocument()
    expect(screen.queryByText('Kept')).not.toBeInTheDocument()
    expect(screen.queryByText(/In May, you kept/)).not.toBeInTheDocument()
    expect(screen.queryByText('Needs your attention')).not.toBeInTheDocument()
    expect(screen.queryByText('Balance over time')).not.toBeInTheDocument()
    expect(screen.queryByText('Spoken for each month')).not.toBeInTheDocument()
    expect(screen.queryByText(/money went/)).not.toBeInTheDocument()
  })

  it('sorting flips the page from cash totals to the full dashboard', async () => {
    let insights = PENDING_SUMMARY
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) return Promise.resolve(insights)
      if (path === '/annotations/review-queue') return Promise.resolve([])
      return Promise.resolve([])
    })
    mockRunAnnotationJob.mockImplementation(async () => {
      insights = SUMMARY
      return { rule_matched: 40, rag_direct_annotated: 60, llm_annotated: 20 }
    })
    renderDashboard()
    fireEvent.click(await screen.findByRole('button', { name: /Sort my transactions/ }))
    // The job sweeps everything pending, not one statement.
    expect(mockRunAnnotationJob).toHaveBeenCalledWith({}, expect.any(Function))
    // Nothing-to-everything: the verdict hero appears after the refetch.
    expect(
      await screen.findByText((_, el) => el.tagName === 'P' && /In May, you kept/.test(el.textContent))
    ).toBeInTheDocument()
    expect(screen.getByText('Invested')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Sort my transactions/ })).not.toBeInTheDocument()
  })

  it('stays in the setup stage when sorting fails', async () => {
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) return Promise.resolve(PENDING_SUMMARY)
      return Promise.resolve([])
    })
    mockRunAnnotationJob.mockRejectedValue(new Error('LLM unreachable'))
    renderDashboard()
    fireEvent.click(await screen.findByRole('button', { name: /Sort my transactions/ }))
    expect(await screen.findByRole('button', { name: /Sort my transactions/ })).toBeEnabled()
  })

  it('requests the shared period month from the server', async () => {
    localStorage.setItem('fc_period_month', '2026-04')
    renderDashboard()
    await screen.findByText('Kept')
    expect(mockApi.get).toHaveBeenCalledWith('/insights?month=2026-04')
  })
})

// The freshness nudge compares against the previous calendar month, so the
// "quiet" fixture must claim data through then to keep the test time-stable.
function dayjsNowMinusOne() {
  const d = new Date()
  d.setMonth(d.getMonth() - 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}
