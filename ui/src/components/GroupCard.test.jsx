import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {} }))
const noopToast = () => {}
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => noopToast,
}))

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import GroupCard from './GroupCard.jsx'

function renderCard(group) {
  return render(
    <PrivacyProvider>
      <GroupCard group={group} onDelete={() => {}} />
    </PrivacyProvider>
  )
}

const GROUP = {
  id: 'g1',
  name: 'Goa Trip',
  labels: [],
  note: null,
  summary: {
    member_count: 2,
    first_date: '2026-01-05',
    last_date: '2026-01-20',
    spend: 300,
    received: 100,
    net: -200,
  },
}

describe('GroupCard', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the collapsed summary: name, date range, txn count, net', () => {
    renderCard(GROUP)
    expect(screen.getByText('Goa Trip')).toBeInTheDocument()
    expect(screen.getByText('05 - 20 Jan 2026')).toBeInTheDocument()
    expect(screen.getByText('2 txns')).toBeInTheDocument()
    // net is negative → shows −₹200 and a "net" label
    expect(screen.getByText('net')).toBeInTheDocument()
    expect(screen.getByText(/₹200/)).toBeInTheDocument()
  })

  it('loads detail and shows settle-up when expanded', async () => {
    mockApi.get.mockResolvedValue({
      ...GROUP,
      members: [
        { transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', people: ['Alice'], txn_type: 'split', role: 'paid' },
      ],
      settle_up: [{ person: 'Alice', net: 150 }],
    })
    renderCard(GROUP)
    fireEvent.click(screen.getByText('Goa Trip'))
    await waitFor(() => expect(screen.getByText('DINNER')).toBeInTheDocument())
    expect(screen.getByText('Settle up')).toBeInTheDocument()
    // Alice appears both in the members' People cell and in settle-up.
    expect(screen.getAllByText('Alice').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/owes you/)).toBeInTheDocument()
  })
})
