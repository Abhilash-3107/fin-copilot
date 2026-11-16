import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// Hermetic mocks: no backend, no canvas (chart.js needs a real 2d context).
const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({
  api: mockApi,
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
// The mock keeps the options the page hands the chart, so a test can call the
// tooltip and axis callbacks that chart.js would run on a canvas.
const chart = vi.hoisted(() => ({ options: null }))
vi.mock('react-chartjs-2', () => ({
  Line: ({ options }) => {
    chart.options = options
    return <div data-testid="balance-chart" />
  },
}))

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import { StatementProvider } from '../contexts/StatementContext.jsx'
import { PeriodProvider } from '../contexts/PeriodContext.jsx'
import Insights from './Insights.jsx'

const SUMMARY = {
  month: '2026-02',
  prev_month: '2026-01',
  months: ['2026-01', '2026-02'],
  period: { month: '2026-02', start: '2026-02-01', end: '2026-02-28', covered_through: '2026-02-28', partial: false },
  comparison: { month: '2026-01', start: '2026-01-01', end: '2026-01-31' },
  verdict: {
    money_in: 100646, money_out: 77763, invested: 30000, kept: -7117,
    kept_rate: -0.0707, earned: 57508, other_in: 43138,
    prev: {
      money_in: 57583, money_out: 26006, invested: 30000, kept: 1577,
      kept_rate: 0.0274, earned: 57583, other_in: 0,
    },
  },
  what_changed: [
    { category: 'Entertainment', delta: 14541, current: 18842, previous: 4301 },
  ],
  categories: [
    {
      category: 'Entertainment', gross: 53652, net: 18842, offsets: 34810, count: 8,
      prev_net: 4301, delta: 14541,
      subcategories: [{ name: 'Events & Concerts', total: 46000, count: 2 }],
    },
    {
      category: 'Food & Dining', gross: 16411, net: 16411, offsets: 0, count: 40,
      prev_net: 13571, delta: 2840,
      subcategories: [{ name: 'Food Delivery', total: 5400, count: 12 }],
    },
  ],
  breakdown: {
    total: 77763,
    extras: [
      { key: 'transfers', label: 'Transfers and cash', total: 7000, count: 2 },
      { key: 'paid_back', label: 'Refunds and paybacks taken off the rows above', total: 34810, count: null },
      { key: 'other', label: 'Other money out', total: 700, count: 1 },
    ],
  },
  recurring_summary: { monthly_total: 8000, count: 1 },
  recurring: [
    { name: 'INDmoney', keys: ['INDMONEY'], category: 'Investments', amount: 8000, months_seen: 6, months_span: 6, cadence: 'monthly', last_date: '2026-02-03', active: true },
    { name: 'BESCOM', keys: ['BESCOM'], category: 'Utilities', amount: 2260, months_seen: 3, months_span: 4, cadence: 'irregular', last_date: '2026-02-04', active: true },
    { name: 'LinkedIn', keys: ['LINKEDIN'], category: 'Subscriptions', amount: 499, months_seen: 2, months_span: 2, cadence: 'monthly', last_date: '2025-11-20', active: false },
  ],
  people: {
    items: [
      { id: 'p1', name: 'sanya', relationship: 'friend', sent: 19686, received: 23468, net: 3782, count: 20, last_date: '2026-02-20' },
      { id: 'p2', name: 'neha', relationship: null, sent: 440, received: 0, net: -440, count: 1, last_date: '2026-02-13' },
    ],
    unmatched: { sent: 0, received: 0, count: 0 },
  },
  merchants: [
    { name: 'Zomato', keys: ['ZOMATO', 'ZOMATO LIMITED'], total: 4470, count: 10, avg: 447 },
  ],
  balance: {
    account: 'Kotak ····1234',
    series: [
      { date: '2026-01-31', balance: 71000 },
      { date: '2026-02-28', balance: 50000 },
    ],
  },
  unexplained: { count: 3, total: 620 },
}

function renderInsights() {
  render(
    <MemoryRouter>
      <PrivacyProvider>
        <StatementProvider>
          <PeriodProvider>
            <Insights />
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
    if (path === '/statements') return Promise.resolve([])
    return Promise.resolve([])
  })
})

describe('Insights (Money Map)', () => {
  it('renders the cash-view verdict strip with the kept rate', async () => {
    renderInsights()
    expect(await screen.findByText('Kept')).toBeInTheDocument()
    expect(screen.getAllByText('₹1,00,646').length).toBeGreaterThan(0) // In: all cash that arrived
    expect(screen.getAllByText('₹77,763').length).toBeGreaterThan(0) // Out: gross, nothing netted
    expect(screen.getByText('-7%').parentElement).toHaveTextContent('-7% of money in')
    // Every tile words its delta against the same window (Invested is unchanged).
    expect(screen.getByText('Out').closest('div')).toHaveTextContent(/vs January/)
    expect(screen.getByText('Kept').closest('div')).toHaveTextContent(/vs January/)
    expect(screen.getByText('same as January')).toBeInTheDocument()
    expect(screen.getByText(/in refunds and paybacks/)).toHaveTextContent('incl ₹43,138 in refunds and paybacks')
  })

  it('does not render the verdict arithmetic explainer', async () => {
    renderInsights()
    await screen.findByText('Kept')
    expect(screen.queryByText(/from earlier months/)).not.toBeInTheDocument()
    expect(screen.queryByText(/= saved/)).not.toBeInTheDocument()
  })

  it('renders what-changed sentences linking to filtered transactions', async () => {
    renderInsights()
    const link = await screen.findByRole('link', { name: /Entertainment up ₹14,541/ })
    expect(link).toHaveAttribute('href', '/transactions?category=Entertainment&month=2026-02')
  })

  it('shows net category amounts with the gross disclosure and drill-down', async () => {
    renderInsights()
    // Net amount appears in both the what-changed card and the category row.
    expect((await screen.findAllByText('₹18,842')).length).toBeGreaterThan(0)
    expect(screen.getByText(/gross/)).toBeInTheDocument()
    expect(screen.queryByText('Events & Concerts')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Entertainment/ }))
    expect(screen.getByText('Events & Concerts')).toBeInTheDocument()
  })

  it('surfaces unexplained spending as a review affordance', async () => {
    renderInsights()
    const link = (await screen.findByText(/is unexplained/)).closest('a')
    expect(link).toHaveAttribute('href', '/review')
  })

  it('renders recurring, people and merchant panels from the server payload', async () => {
    renderInsights()
    expect(await screen.findByText('INDmoney')).toBeInTheDocument()
    expect(screen.getByText('sanya')).toBeInTheDocument()
    // The sent/received line interleaves text nodes with Amount spans, so
    // match on the paragraph's full text content.
    expect(
      screen.getByText((_, el) => el.tagName === 'P' && el.textContent.includes('sent ₹19,686 · received ₹23,468 · 20 transfers'))
    ).toBeInTheDocument()
    // A single transfer is not "1 transfers".
    expect(
      screen.getByText((_, el) => el.tagName === 'P' && el.textContent.includes('sent ₹440 · received ₹0 · 1 transfer'))
    ).toBeInTheDocument()
    expect(screen.getByText('Sent and received in February')).toBeInTheDocument()
    // Same neutral wording as the People page: no sign, no red.
    const received = screen.getByText(/more received/)
    expect(received).toHaveTextContent('₹3,782 more received')
    const sent = screen.getByText(/more sent/)
    expect(sent).toHaveTextContent('₹440 more sent')
    expect(received.className).not.toMatch(/red|emerald|green/)
    expect(sent.className).not.toMatch(/red|emerald|green/)
    expect(screen.queryByText('−₹440')).not.toBeInTheDocument()
    expect(screen.getByText('Zomato')).toBeInTheDocument()
    expect(screen.getByText('10')).toBeInTheDocument() // order count
    expect(screen.getByText('₹447')).toBeInTheDocument() // avg ticket
  })

  it('drills a merchant row by its keys, not its name', async () => {
    renderInsights()
    const link = (await screen.findByText('Zomato')).closest('a')
    const params = new URL(link.getAttribute('href'), 'http://x').searchParams
    expect(params.getAll('payee_key')).toEqual(['ZOMATO', 'ZOMATO LIMITED'])
    expect(params.has('merchant')).toBe(false)
  })

  it('closes the breakdown on Out with the reconciling rows and a total', async () => {
    renderInsights()
    const card = (await screen.findByText(/Where it went/)).closest('.overflow-hidden')
    expect(within(card).getByText('Transfers and cash')).toBeInTheDocument()
    expect(within(card).getByText('Refunds and paybacks taken off the rows above')).toBeInTheDocument()
    expect(within(card).getByText('Other money out')).toBeInTheDocument()
    const total = within(card).getByText('Total out').closest('div')
    expect(total).toHaveTextContent('₹77,763')
    // Rows (net) plus the reconciling rows are exactly the total.
    const amounts = [...card.querySelectorAll('li > div > span.text-right.tabular-nums')]
      .map(el => Number(el.textContent.replace(/[^\d]/g, '')))
    const total_ = amounts.pop()
    expect(amounts.reduce((a, b) => a + b, 0)).toBe(total_)
  })

  it('lists active monthly items under the header total, then the rest apart', async () => {
    renderInsights()
    const title = await screen.findByText(/Recurring/)
    const card = title.closest('.overflow-hidden')
    expect(card.querySelector('thead')).toBeNull()
    expect(title.parentElement).toHaveTextContent('₹8,000/mo across 1')
    const rows = [...card.querySelectorAll('tbody tr')].map(tr => tr.textContent)
    expect(rows[0]).toMatch(/INDmoney.*₹8,000/)
    expect(rows[1]).toMatch(/not in the monthly total/)
    expect(rows[2]).toMatch(/BESCOM.*₹2,260/)
    expect(rows[3]).toMatch(/Stopped/)
    expect(rows[4]).toMatch(/LinkedIn/)
    // The header is the sum of the monthly rows listed first.
    expect(rows.filter(r => /Monthly/.test(r) && !/Stopped/.test(r)).length).toBe(2) // INDmoney + stopped LinkedIn
  })

  it('offers no All time and shows the latest month when the period is All time', async () => {
    localStorage.setItem('fc_period_month', 'all')
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/insights')) return Promise.resolve(SUMMARY)
      if (path === '/statements') {
        return Promise.resolve([
          { id: 's2', bank_name: 'kotak', statement_month: '2026-02', uploaded_at: '2026-03-01' },
          { id: 's1', bank_name: 'kotak', statement_month: '2026-01', uploaded_at: '2026-02-01' },
        ])
      }
      return Promise.resolve([])
    })
    renderInsights()
    await screen.findByText('Kept')
    const picker = screen.getByRole('combobox', { name: 'Period' })
    expect(within(picker).queryByRole('option', { name: 'All time' })).not.toBeInTheDocument()
    await waitFor(() => expect(picker).toHaveValue('2026-02'))
  })

  it('hides the In tile note amount in privacy mode', async () => {
    localStorage.setItem('fc_privacy_hidden', '1')
    renderInsights()
    const note = (await screen.findByText(/in refunds and paybacks/)).closest('span.text-xs')
    expect(note.querySelector('[data-privacy-mask]')).toHaveTextContent('₹43,138')
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
      period: { month: '2026-11', start: '2026-11-01', end: '2026-11-30', covered_through: '2026-11-15', partial: true },
      comparison: { month: '2026-10', start: '2026-10-01', end: '2026-10-15' },
    }

    it('labels the month and words every delta against the same days', async () => {
      mockApi.get.mockImplementation(path => (
        path.startsWith('/insights') ? Promise.resolve(PARTIAL) : Promise.resolve([])
      ))
      renderInsights()
      expect(await screen.findByText('1-15 Nov, in progress')).toBeInTheDocument()
      expect(screen.getByText('What changed vs 1-15 Oct')).toBeInTheDocument()
      expect(screen.getAllByText(/vs 1-15 Oct/).length).toBeGreaterThan(3)
      expect(screen.queryByText(/last month/)).not.toBeInTheDocument()
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
      renderInsights()
      expect(await screen.findByText('1-28 Nov')).toBeInTheDocument()
      expect(screen.getByText('What changed vs 1-28 Oct')).toBeInTheDocument()
      expect(screen.queryByText(/in progress/)).not.toBeInTheDocument()
    })

    it('shows no delta anywhere without a comparison', async () => {
      mockApi.get.mockImplementation(path => (
        path.startsWith('/insights')
          ? Promise.resolve({
            ...PARTIAL,
            comparison: null,
            verdict: { ...PARTIAL.verdict, prev: null },
            what_changed: [],
            categories: PARTIAL.categories.map(c => ({ ...c, prev_net: null, delta: null })),
          })
          : Promise.resolve([])
      ))
      renderInsights()
      await screen.findByText('Kept')
      expect(screen.queryByText(/ vs /)).not.toBeInTheDocument()
      expect(screen.queryByText(/same as/)).not.toBeInTheDocument()
      expect(screen.queryByText(/▲|▼/)).not.toBeInTheDocument()
      expect(screen.getByText('What changed')).toBeInTheDocument()
    })
  })

  it('requests the period month from the server', async () => {
    localStorage.setItem('fc_period_month', '2026-01')
    renderInsights()
    await screen.findByText('Kept')
    expect(mockApi.get).toHaveBeenCalledWith('/insights?month=2026-01')
  })
})

describe('privacy mode', () => {
  it('blurs the money percentage with the amounts', async () => {
    localStorage.setItem('fc_privacy_hidden', '1')
    renderInsights()
    await screen.findByText('Kept')
    const rate = screen.getAllByText('-7%')[0]
    expect(rate).toHaveClass('blur-[5px]')
    expect(rate).toHaveAttribute('aria-hidden', 'true')
  })

  it('keeps the balance chart tooltip and ticks free of amounts', async () => {
    localStorage.setItem('fc_privacy_hidden', '1')
    renderInsights()
    await screen.findByTestId('balance-chart')
    const { tooltip, } = chart.options.plugins
    expect(tooltip.callbacks.label({ raw: 71000 })).not.toMatch(/\d/)
    expect(chart.options.scales.y.ticks.callback(71000)).not.toMatch(/\d/)
  })

  it('shows both when amounts are shown', async () => {
    renderInsights()
    await screen.findByTestId('balance-chart')
    expect(chart.options.plugins.tooltip.callbacks.label({ raw: 71000 })).toBe('₹71,000')
    expect(chart.options.scales.y.ticks.callback(71000)).toBe('₹71k')
  })
})

describe('Insights when the API fails', () => {
  it('says the Money Map could not load, never "Nothing here yet", and recovers on Retry', async () => {
    const base = mockApi.get.getMockImplementation()
    let failing = true
    mockApi.get.mockImplementation(path => (
      failing && path.startsWith('/insights') ? Promise.reject(new TypeError('Failed to fetch')) : base(path)
    ))
    renderInsights()
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load your Money Map.")
    expect(screen.queryByText(/Nothing here yet/)).not.toBeInTheDocument()
    failing = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/What changed/i)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
