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

// Expanding a card loads the group detail and the People list in parallel.
function mockDetail(detail, people = []) {
  mockApi.get.mockImplementation(url => Promise.resolve(url === '/people' ? people : detail))
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
    mockDetail({
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

  it('shows people by name and tags through the picker', async () => {
    mockApi.get.mockImplementation(url => Promise.resolve(url === '/people'
      ? [{ id: 'p1', name: 'Alice' }, { id: 'p2', name: 'Bob' }]
      : {
          ...GROUP,
          members: [
            { transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', people: ['p1'], txn_type: null },
          ],
          settle_up: [{ person: 'p1', name: 'Alice', net: 150 }],
        }))
    mockApi.patch.mockResolvedValue({})
    renderCard(GROUP)
    fireEvent.click(screen.getByText('Goa Trip'))
    await waitFor(() => expect(screen.getByText('DINNER')).toBeInTheDocument())
    // Name, never the id, in both the People cell and the settle-up chip.
    expect(screen.getAllByText('Alice')).toHaveLength(2)
    expect(screen.queryByText('p1')).not.toBeInTheDocument()

    fireEvent.focus(screen.getByLabelText('Search people'))
    fireEvent.click(screen.getByText('Bob'))
    await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith(
      '/groups/g1/members/t1', { people: ['p1', 'p2'] }))
  })

  it('says everyone is settled when tagged people net to zero', async () => {
    mockDetail({
      ...GROUP,
      members: [
        { transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', people: ['Alice'], txn_type: null },
        { transaction_id: 't2', txn_date: '2026-01-06', amount: 150, debit_credit: 'credit', raw_description: 'REPAY', people: ['Alice'], txn_type: null },
      ],
      settle_up: [],
    })
    renderCard(GROUP)
    fireEvent.click(screen.getByText('Goa Trip'))
    await waitFor(() => expect(screen.getByText("Everyone's settled up")).toBeInTheDocument())
  })

  it('shows no settle-up section when nobody is tagged', async () => {
    mockDetail({
      ...GROUP,
      members: [
        { transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', people: [], txn_type: null },
      ],
      settle_up: [],
    })
    renderCard(GROUP)
    fireEvent.click(screen.getByText('Goa Trip'))
    await waitFor(() => expect(screen.getByText('DINNER')).toBeInTheDocument())
    expect(screen.queryByText('Settle up')).not.toBeInTheDocument()
  })

  it('has no role column - settle-up is driven by type alone', async () => {
    mockDetail({
      ...GROUP,
      members: [
        { transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', people: ['Alice'], txn_type: 'split', role: 'paid' },
      ],
      settle_up: [{ person: 'Alice', net: 150 }],
    })
    renderCard(GROUP)
    fireEvent.click(screen.getByText('Goa Trip'))
    await waitFor(() => expect(screen.getByText('DINNER')).toBeInTheDocument())
    expect(screen.queryByText('Role')).not.toBeInTheDocument()
  })
})
