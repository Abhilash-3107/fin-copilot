import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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
vi.mock('react-chartjs-2', () => ({
  Line: () => <div data-testid="balance-chart" />,
}))

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import { StatementProvider } from '../contexts/StatementContext.jsx'
import { PeriodProvider } from '../contexts/PeriodContext.jsx'
import Insights from './Insights.jsx'

const SUMMARY = {
  month: '2026-02',
  prev_month: '2026-01',
  months: ['2026-01', '2026-02'],
  verdict: {
    money_in: 100646, money_out: 65877, invested: 30000, kept: 4769,
    kept_rate: 0.0474, earned: 57508, other_in: 43138,
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
  recurring: [
    { name: 'INDmoney', category: 'Investments', amount: 8000, months_seen: 6, months_span: 6, cadence: 'monthly', last_date: '2026-02-03', active: true },
  ],
  people: {
    items: [
      { id: 'p1', name: 'sanya', relationship: 'friend', sent: 19686, received: 23468, net: 3782, count: 20, last_date: '2026-02-20' },
    ],
    unmatched: { sent: 0, received: 0, count: 0 },
  },
  merchants: [
    { name: 'Zomato', total: 4470, count: 10, avg: 447 },
  ],
  balance: [
    { date: '2026-01-31', balance: 71000 },
    { date: '2026-02-28', balance: 50000 },
  ],
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
    expect(screen.getAllByText('₹65,877').length).toBeGreaterThan(0) // Out: gross, nothing netted
    expect(screen.getByText('5% of money in')).toBeInTheDocument()
    expect(screen.getByText(/incl ₹43,138 paid back or from people/)).toBeInTheDocument()
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
      screen.getByText((_, el) => el.tagName === 'P' && el.textContent.includes('sent ₹19,686 · received ₹23,468'))
    ).toBeInTheDocument()
    expect(screen.getByText('Zomato')).toBeInTheDocument()
    expect(screen.getByText('10')).toBeInTheDocument() // order count
    expect(screen.getByText('₹447')).toBeInTheDocument() // avg ticket
  })

  it('requests the period month from the server', async () => {
    localStorage.setItem('fc_period_month', '2026-01')
    renderInsights()
    await screen.findByText('Kept')
    expect(mockApi.get).toHaveBeenCalledWith('/insights?month=2026-01')
  })
})
