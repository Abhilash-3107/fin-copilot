import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// Hermetic mocks: no backend, no canvas (chart.js needs a real 2d context).
const { mockApi, mockRunAnnotationJob, refreshDemoData } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn() },
  mockRunAnnotationJob: vi.fn(),
  refreshDemoData: vi.fn(),
}))
vi.mock('../contexts/DemoDataContext.jsx', () => ({
  useDemoData: () => ({ sample: false, mixed: false, hosted: false, refresh: refreshDemoData }),
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
  period: { month: '2026-05', start: '2026-05-01', end: '2026-05-31', covered_through: '2026-05-31', partial: false },
  comparison: { month: '2026-04', start: '2026-04-01', end: '2026-04-30' },
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
  breakdown: {
    total: 50738,
    extras: [
      { key: 'transfers', label: 'Transfers and cash', total: 7000, count: 2 },
      { key: 'paid_back', label: 'Refunds and paybacks taken off the rows above', total: 1031, count: null },
    ],
  },
  recurring_summary: { monthly_total: 26999, count: 2 },
  recurring: [
    { name: 'INDmoney', category: 'Investments', amount: 25000, months_seen: 6, months_span: 6, cadence: 'monthly', last_date: '2026-05-01', active: true },
    { name: 'Apple', category: 'Subscriptions', amount: 1999, months_seen: 4, months_span: 4, cadence: 'monthly', last_date: '2026-05-15', active: true },
    { name: 'LinkedIn', category: 'Subscriptions', amount: 499, months_seen: 3, months_span: 5, cadence: 'monthly', last_date: '2026-02-10', active: false },
    { name: 'BESCOM', category: 'Utilities', amount: 2260, months_seen: 3, months_span: 4, cadence: 'irregular', last_date: '2026-05-04', active: true },
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

const COUNTS = { unsorted: 5, guesses: 64, set_aside: 0, set_aside_unsorted: 0 }

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
    if (path === '/annotations/review-queue/counts') return Promise.resolve(COUNTS)
    if (path === '/statements') return Promise.resolve([])
    return Promise.resolve([])
  })
})

describe('Dashboard', () => {
  it('renders the verdict sentence and server-computed tiles', async () => {
    renderDashboard()
    // The hero's "kept" is the Kept tile's figure (In - Out - Invested), with
    // what was invested said beside it.
    expect(
      await screen.findByText((_, el) => (
        el.tagName === 'P'
        && /In May, you kept ₹8,665 - 10% of what came in, and invested ₹30,000\./.test(el.textContent)
      ))
    ).toBeInTheDocument()
    expect(screen.getAllByText('₹8,665').length).toBeGreaterThan(1) // hero and tile agree
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

  it('ranks the attention items with the unsorted row first', async () => {
    renderDashboard()
    // One figure per fact: this month's unsorted count and money in one row,
    // the guesses to check (every month) in their own.
    const review = await screen.findByRole('link', { name: /3 payments in May aren't sorted yet \(\s*₹620\s*\)/ })
    expect(review).toHaveAttribute('href', '/review')
    expect(screen.getAllByRole('link', { name: /Sort them/ })).toHaveLength(1)
    expect(screen.getByRole('link', { name: /64 guesses to check, across all months/ })).toHaveAttribute('href', '/review')
    // Data is from May but "today" is later, so the freshness nudge fires.
    expect(screen.getByRole('link', { name: /newest statement is May/ })).toHaveAttribute('href', '/upload')
    // Travel is 4.8x April: the spike item deep-links into filtered transactions.
    expect(screen.getByRole('link', { name: /Travel ran ₹11,249 above April/ }))
      .toHaveAttribute('href', '/transactions?category=Travel&month=2026-05')
    // Per-person balances live on Money Map and People, not in the attention list.
    expect(screen.queryByText(/Between you and/)).not.toBeInTheDocument()
  })

  it('says one payment in the singular and never downloads the queue to count it', async () => {
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) return Promise.resolve({ ...SUMMARY, unexplained: { count: 1, total: 450 } })
      if (path === '/annotations/review-queue/counts') return Promise.resolve({ ...COUNTS, guesses: 1 })
      return Promise.resolve([])
    })
    renderDashboard()
    expect(await screen.findByRole('link', { name: /1 payment in May isn't sorted yet \(\s*₹450\s*\)/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /1 guess to check, across all months/ })).toBeInTheDocument()
    expect(mockApi.get.mock.calls.some(([p]) => p === '/annotations/review-queue')).toBe(false)
  })

  it('shows no guesses row when none are waiting', async () => {
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) return Promise.resolve(SUMMARY)
      if (path === '/annotations/review-queue/counts') return Promise.resolve({ ...COUNTS, guesses: 0 })
      return Promise.resolve([])
    })
    renderDashboard()
    await screen.findByRole('link', { name: /3 payments in May aren't sorted yet/ })
    expect(screen.queryByText(/to check/)).not.toBeInTheDocument()
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
      if (path === '/annotations/review-queue/counts') return Promise.resolve({ unsorted: 0, guesses: 0, set_aside: 0, set_aside_unsorted: 0 })
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

  it('tells the banner when it loads the demo, and says how to remove it', async () => {
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) return Promise.resolve({ months: [], month: null })
      return Promise.resolve([])
    })
    mockApi.post.mockResolvedValue({ transactions: 195, months: 4 })
    renderDashboard()
    expect(await screen.findByText(/remove it\s+anytime from the banner or Settings/)).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Load demo data' }))
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith('/data/demo'))
    await waitFor(() => expect(refreshDemoData).toHaveBeenCalled())
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
      if (path === '/annotations/review-queue/counts') return Promise.resolve({ unsorted: 0, guesses: 0, set_aside: 0, set_aside_unsorted: 0 })
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

  it('waits while another job holds the slot (e.g. re-attached after a reload)', async () => {
    const { getActiveAnnotationJob, pollAnnotationJob } = await import('../lib/api.js')
    getActiveAnnotationJob.mockResolvedValueOnce({ id: 're', status: 'running', kind: 'reembed' })
    pollAnnotationJob.mockReturnValueOnce(new Promise(() => {}))
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) return Promise.resolve(PENDING_SUMMARY)
      return Promise.resolve([])
    })
    renderDashboard()
    expect(await screen.findByRole('button', { name: /Sorting/ })).toBeDisabled()
    expect(mockRunAnnotationJob).not.toHaveBeenCalled()
  })

  describe('one definition of Kept', () => {
    const text = re => screen.findByText((_, el) => el.tagName === 'P' && re.test(el.textContent))

    it('omits the invested clause when nothing was invested', async () => {
      mockApi.get.mockImplementation(path => (
        path.startsWith('/insights')
          ? Promise.resolve({
            ...SUMMARY,
            verdict: { ...SUMMARY.verdict, invested: 0, kept: 38665, kept_rate: 0.4325 },
          })
          : Promise.resolve([])
      ))
      renderDashboard()
      expect(await text(/In May, you kept ₹38,665 - 43% of what came in\.$/)).toBeInTheDocument()
    })

    it('says "spent more than came in" when kept is negative', async () => {
      mockApi.get.mockImplementation(path => (
        path.startsWith('/insights')
          ? Promise.resolve({
            ...SUMMARY,
            verdict: { ...SUMMARY.verdict, money_in: 40000, money_out: 55000, invested: 0, kept: -15000, kept_rate: -0.375 },
          })
          : Promise.resolve([])
      ))
      renderDashboard()
      expect(await text(/In May, you spent ₹15,000 more than came in\.$/)).toBeInTheDocument()
      expect(screen.queryByText(/you kept/)).not.toBeInTheDocument()
    })
  })

  describe('a month in progress', () => {
    beforeEach(() => {
      vi.useFakeTimers({ toFake: ['Date'] })
      vi.setSystemTime(new Date('2026-11-15T12:00:00'))
    })
    afterEach(() => vi.useRealTimers())

    const PARTIAL = {
      ...SUMMARY,
      month: '2026-11',
      prev_month: '2026-10',
      period: { month: '2026-11', start: '2026-11-01', end: '2026-11-30', covered_through: '2026-11-15', partial: true },
      comparison: { month: '2026-10', start: '2026-10-01', end: '2026-10-15' },
    }

    it('labels the month as in progress and compares the same days', async () => {
      mockApi.get.mockImplementation(path => (
        path.startsWith('/insights') ? Promise.resolve(PARTIAL) : Promise.resolve([])
      ))
      renderDashboard()
      expect(await screen.findByText('1-15 Nov, in progress')).toBeInTheDocument()
      expect(screen.getByText((_, el) => el.tagName === 'P' && /^In November so far, you kept/.test(el.textContent)))
        .toBeInTheDocument()
      // Every tile names the comparison window (Invested is unchanged: "same as").
      expect(screen.getAllByText(/vs 1-15 Oct/).length).toBe(3)
      expect(screen.getByText('same as 1-15 Oct')).toBeInTheDocument()
      expect(screen.queryByText(/last month/)).not.toBeInTheDocument()
      expect(screen.getByRole('link', { name: /Travel ran ₹11,249 above 1-15 Oct/ })).toBeInTheDocument()
    })

    it('says no "in progress" once the month has ended, keeping the covered days', async () => {
      vi.setSystemTime(new Date('2026-12-02T12:00:00'))
      mockApi.get.mockImplementation(path => (
        path.startsWith('/insights')
          ? Promise.resolve({
            ...PARTIAL,
            period: { ...PARTIAL.period, covered_through: '2026-11-28' },
            comparison: { month: '2026-10', start: '2026-10-01', end: '2026-10-28' },
          })
          : Promise.resolve([])
      ))
      renderDashboard()
      expect(await screen.findByText('1-28 Nov')).toBeInTheDocument()
      expect(screen.getByText((_, el) => el.tagName === 'P' && /^In November, you kept/.test(el.textContent)))
        .toBeInTheDocument()
      expect(screen.getAllByText(/vs 1-28 Oct/).length).toBe(3)
      expect(screen.queryByText(/in progress|so far/)).not.toBeInTheDocument()
    })

    it('shows no delta at all when there is nothing to compare with', async () => {
      mockApi.get.mockImplementation(path => (
        path.startsWith('/insights')
          ? Promise.resolve({
            ...PARTIAL,
            comparison: null,
            verdict: { ...PARTIAL.verdict, prev: null },
            what_changed: [],
          })
          : Promise.resolve([])
      ))
      renderDashboard()
      await screen.findByText('Kept')
      expect(screen.queryByText(/ vs /)).not.toBeInTheDocument()
      expect(screen.queryByText(/same as/)).not.toBeInTheDocument()
      expect(screen.queryByText(/▲|▼/)).not.toBeInTheDocument()
    })

    it('names a complete month by its month', async () => {
      renderDashboard()
      await screen.findByText('Kept')
      expect(screen.getAllByText(/vs April/).length).toBe(3)
      expect(screen.getByText('same as April')).toBeInTheDocument()
      expect(screen.queryByText(/in progress/)).not.toBeInTheDocument()
    })
  })

  it('words the In tile note like Money Map and hides its amount in privacy mode', async () => {
    localStorage.setItem('fc_privacy_hidden', '1')
    mockApi.get.mockImplementation(path => (
      path.startsWith('/insights')
        ? Promise.resolve({ ...SUMMARY, verdict: { ...SUMMARY.verdict, other_in: 750 } })
        : Promise.resolve([])
    ))
    renderDashboard()
    const note = (await screen.findByText(/in refunds and paybacks/)).closest('p')
    expect(note).toHaveTextContent('incl ₹750 in refunds and paybacks')
    expect(note.querySelector('[aria-label="Amount hidden"]')).toHaveTextContent('₹750')
  })

  // Sum every rupee figure inside the given rows (the Indian grouping has commas only).
  const rupees = el => Number(el.textContent.match(/₹([\d,]+)/)[1].replace(/,/g, ''))

  it('spending glance closes on Out: top rows plus "and N more" add up', async () => {
    mockApi.get.mockImplementation(path => (
      path.startsWith('/insights')
        ? Promise.resolve({
          ...SUMMARY,
          verdict: { ...SUMMARY.verdict, money_out: 48031 },
          breakdown: { ...SUMMARY.breakdown, total: 48031 },
          categories: [
            { category: 'Food & Dining', gross: 20000, net: 20000, offsets: 0, count: 4, prev_net: 1, delta: 1, subcategories: [] },
            { category: 'Travel', gross: 15000, net: 15000, offsets: 0, count: 2, prev_net: 1, delta: 1, subcategories: [] },
            { category: 'Shopping', gross: 5000, net: 4000, offsets: 1000, count: 2, prev_net: 1, delta: 1, subcategories: [] },
            { category: 'Health', gross: 800, net: 800, offsets: 0, count: 1, prev_net: 1, delta: 1, subcategories: [] },
            { category: 'Uncategorized', gross: 200, net: 200, offsets: 0, count: 1, prev_net: 1, delta: 1, subcategories: [] },
          ],
        })
        : Promise.resolve([])
    ))
    renderDashboard()
    const card = (await screen.findByText(/money went/)).closest('div')
    const rows = within(card).getAllByRole('listitem')
    expect(rows).toHaveLength(4)
    expect(rows[3]).toHaveTextContent('and 4 more')
    expect(rows.map(rupees).reduce((a, b) => a + b, 0)).toBe(48031)
  })

  it('committed header equals the rows listed under it', async () => {
    mockApi.get.mockImplementation(path => (
      path.startsWith('/insights')
        ? Promise.resolve({
          ...SUMMARY,
          recurring_summary: { monthly_total: 40, count: 7 },
          recurring: Array.from({ length: 7 }, (_, i) => ({
            name: `Sub ${i}`, category: 'Subscriptions', amount: i === 6 ? 10 : 5, months_seen: 4, months_span: 4,
            cadence: 'monthly', last_date: '2026-05-05', active: true,
          })),
        })
        : Promise.resolve([])
    ))
    renderDashboard()
    const title = await screen.findByText('Spoken for each month')
    const card = title.closest('.overflow-hidden')
    const rows = within(card).getAllByRole('listitem')
    expect(rows).toHaveLength(6) // five rows plus "and 2 more"
    expect(rows[5]).toHaveTextContent('and 2 more')
    expect(rupees(title.parentElement)).toBe(40)
    expect(rows.map(rupees).reduce((a, b) => a + b, 0)).toBe(40)
  })

  it('lists only active monthly items in the committed card', async () => {
    renderDashboard()
    await screen.findByText('INDmoney')
    expect(screen.queryByText('BESCOM')).not.toBeInTheDocument() // irregular
    expect(screen.queryByText('LinkedIn')).not.toBeInTheDocument() // stopped
  })

  it('offers no All time on a month screen and shows the month it honours', async () => {
    localStorage.setItem('fc_period_month', 'all')
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) return Promise.resolve(SUMMARY)
      if (path === '/statements') {
        return Promise.resolve([
          { id: 's5', bank_name: 'kotak', statement_month: '2026-05', uploaded_at: '2026-06-01' },
          { id: 's4', bank_name: 'kotak', statement_month: '2026-04', uploaded_at: '2026-05-01' },
        ])
      }
      return Promise.resolve([])
    })
    renderDashboard()
    await screen.findByText('Kept')
    const picker = screen.getByRole('combobox', { name: 'Period' })
    expect(within(picker).queryByRole('option', { name: 'All time' })).not.toBeInTheDocument()
    await waitFor(() => expect(picker).toHaveValue('2026-05'))
    // The server picks its latest month; no unhonoured month is requested.
    expect(mockApi.get).toHaveBeenCalledWith('/insights')
  })

  it('shows the month the server returned even when statements name a later one', async () => {
    localStorage.setItem('fc_period_month', 'all')
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) return Promise.resolve(SUMMARY)
      if (path === '/statements') {
        return Promise.resolve([
          { id: 's6', bank_name: 'kotak', statement_month: '2026-06', uploaded_at: '2026-06-30' },
          { id: 's5', bank_name: 'kotak', statement_month: '2026-05', uploaded_at: '2026-06-01' },
        ])
      }
      return Promise.resolve([])
    })
    renderDashboard()
    await screen.findByText('Kept')
    const picker = screen.getByRole('combobox', { name: 'Period' })
    await waitFor(() => expect(picker).toHaveValue('2026-05'))
    expect(within(picker).queryByRole('option', { name: 'Jun 2026' })).not.toBeInTheDocument()
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
