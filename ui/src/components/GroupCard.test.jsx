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

const NO_BALANCES = {
  owed_to_you: { count: 0, total: 0, name: null },
  you_owe: { count: 0, total: 0, name: null },
}

const GROUP = {
  id: 'g1',
  name: 'Goa Trip',
  labels: [],
  note: null,
  summary: {
    ...NO_BALANCES,
    member_count: 2,
    first_date: '2026-01-05',
    last_date: '2026-01-20',
    spend: 300,
    received: 100,
    your_share: 200,
    has_people: false,
    settle_up: [],
  },
}

// Expanding a card loads the group detail and the People list in parallel.
function mockDetail(detail, people = []) {
  mockApi.get.mockImplementation(url => Promise.resolve(url === '/people' ? people : detail))
}

describe('GroupCard', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the collapsed header: name, date range, payments, your share', () => {
    renderCard(GROUP)
    expect(screen.getByText('Goa Trip')).toBeInTheDocument()
    expect(screen.getByText('05 - 20 Jan 2026')).toBeInTheDocument()
    expect(screen.getByText('2 payments')).toBeInTheDocument()
    expect(screen.getByText('Your share')).toBeInTheDocument()
    expect(screen.getByText('₹200')).toBeInTheDocument()
    // The gross outflow is not a "net", and nobody is tagged so nothing is settled yet.
    expect(screen.queryByText(/net/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Settled')).not.toBeInTheDocument()
  })

  it('says who owes you in the header, from the same list as settle up', () => {
    renderCard({
      ...GROUP,
      summary: { ...GROUP.summary, your_share: 440, has_people: true, settle_up: [{ person: 'p1', name: 'Neha Gupta', net: 440 }], owed_to_you: { count: 1, total: 440, name: 'Neha Gupta' } },
    })
    expect(screen.getByText('Your share')).toBeInTheDocument()
    expect(screen.getAllByText('₹440')).toHaveLength(2)
    expect(screen.getByText(/Neha Gupta owes you/)).toBeInTheDocument()
    expect(screen.queryByText(/net/i)).not.toBeInTheDocument()
  })

  it('says you owe someone when the balance is negative', () => {
    renderCard({
      ...GROUP,
      summary: { ...GROUP.summary, has_people: true, settle_up: [{ person: 'p1', name: 'Neha Gupta', net: -150 }], you_owe: { count: 1, total: 150, name: 'Neha Gupta' } },
    })
    expect(screen.getByText(/You owe Neha Gupta/)).toBeInTheDocument()
  })

  it('totals several people who owe you into one line', () => {
    renderCard({
      ...GROUP,
      summary: {
        ...GROUP.summary,
        has_people: true,
        settle_up: [{ person: 'p1', name: 'A', net: 100 }, { person: 'p2', name: 'B', net: 50 }],
        owed_to_you: { count: 2, total: 150, name: null },
      },
    })
    expect(screen.getByText(/2 people owe you/)).toHaveTextContent('2 people owe you ₹150')
  })

  it('totals several people you owe into one line', () => {
    renderCard({
      ...GROUP,
      summary: { ...GROUP.summary, has_people: true, you_owe: { count: 3, total: 90.5, name: null } },
    })
    expect(screen.getByText(/You owe 3 people/)).toHaveTextContent('You owe 3 people ₹90.50')
  })

  it('never nets balances with opposite signs: both sides show with names', () => {
    renderCard({
      ...GROUP,
      summary: {
        ...GROUP.summary,
        has_people: true,
        owed_to_you: { count: 1, total: 4206, name: 'Rahul Mehta' },
        you_owe: { count: 1, total: 5600, name: 'Neha Gupta' },
      },
    })
    expect(screen.getByText(/Rahul Mehta owes you/)).toHaveTextContent('Rahul Mehta owes you ₹4,206')
    expect(screen.getByText(/you owe Neha Gupta/)).toHaveTextContent('you owe Neha Gupta ₹5,600')
    expect(screen.queryByText(/balances to settle/)).not.toBeInTheDocument()
    expect(screen.queryByText('Settled')).not.toBeInTheDocument()
  })

  it('shows counts when each side has several people, and a zero sum still shows both', () => {
    renderCard({
      ...GROUP,
      summary: {
        ...GROUP.summary,
        has_people: true,
        owed_to_you: { count: 2, total: 100, name: null },
        you_owe: { count: 1, total: 100, name: 'Neha Gupta' },
      },
    })
    expect(screen.getByText(/2 people owe you/)).toHaveTextContent('2 people owe you ₹100')
    expect(screen.getByText(/you owe Neha Gupta/)).toHaveTextContent('you owe Neha Gupta ₹100')
  })

  it('shows Settled when people are tagged and nothing is owed', () => {
    renderCard({ ...GROUP, summary: { ...GROUP.summary, has_people: true, settle_up: [] } })
    expect(screen.getByText('Settled')).toBeInTheDocument()
    expect(screen.queryByText(/net/i)).not.toBeInTheDocument()
  })

  it('uses the singular for one payment', () => {
    renderCard({ ...GROUP, summary: { ...GROUP.summary, member_count: 1 } })
    expect(screen.getByText('1 payment')).toBeInTheDocument()
  })

  it('refreshes the header from the server after adding a payment', async () => {
    const empty = { ...GROUP, summary: { ...NO_BALANCES, member_count: 0, first_date: null, last_date: null, spend: 0, received: 0, your_share: 0, has_people: false, settle_up: [] } }
    const afterAdd = {
      ...GROUP,
      members: [{ transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', payee: 'DINNER', people: [], txn_type: null }],
      summary: { member_count: 1, first_date: '2026-01-05', last_date: '2026-01-05', spend: 300, received: 0, your_share: 300, has_people: false, settle_up: [] },
    }
    let added = false
    mockApi.get.mockImplementation(url => {
      if (url === '/people') return Promise.resolve([])
      if (url.startsWith('/transactions')) return Promise.resolve([{ id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', payee: 'DINNER' }])
      return Promise.resolve(added ? afterAdd : { ...empty, members: [] })
    })
    mockApi.post.mockImplementation(() => { added = true; return Promise.resolve({}) })
    renderCard(empty)
    expect(screen.getByText('0 payments')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Goa Trip'))
    await waitFor(() => expect(screen.getByPlaceholderText(/Add a transaction/)).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText(/Add a transaction/), { target: { value: 'dinner' } })
    fireEvent.click(await screen.findByText('DINNER', {}, { timeout: 2000 }))
    await waitFor(() => expect(screen.getByText('1 payment')).toBeInTheDocument())
    expect(screen.getByText('Your share')).toBeInTheDocument()
    expect(screen.queryByText('0 payments')).not.toBeInTheDocument()
  })

  it('loads detail and shows settle-up when expanded', async () => {
    mockDetail({
      ...GROUP,
      members: [
        { transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', payee: 'DINNER', people: ['Alice'], txn_type: 'split', role: 'paid' },
      ],
      summary: { ...GROUP.summary, has_people: true, settle_up: [{ person: 'Alice', net: 150 }] },
    })
    renderCard(GROUP)
    fireEvent.click(screen.getByText('Goa Trip'))
    // jsdom renders both the desktop table and the phone cards.
    await waitFor(() => expect(screen.getAllByText('DINNER').length).toBeGreaterThan(0))
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
            { transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', payee: 'DINNER', people: ['p1'], txn_type: null },
          ],
          summary: { ...GROUP.summary, has_people: true, settle_up: [{ person: 'p1', name: 'Alice', net: 150 }] },
        }))
    mockApi.patch.mockResolvedValue({})
    renderCard(GROUP)
    fireEvent.click(screen.getByText('Goa Trip'))
    // jsdom renders both the desktop table and the phone cards.
    await waitFor(() => expect(screen.getAllByText('DINNER').length).toBeGreaterThan(0))
    // Name, never the id: People cell (table + phone card) and the settle-up chip.
    expect(screen.getAllByText('Alice')).toHaveLength(3)
    expect(screen.queryByText('p1')).not.toBeInTheDocument()

    fireEvent.focus(screen.getAllByLabelText('Search people')[0])
    fireEvent.click(screen.getByText('Bob'))
    await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith(
      '/groups/g1/members/t1', { people: ['p1', 'p2'] }))
  })

  it('says everyone is settled when tagged people net to zero', async () => {
    mockDetail({
      ...GROUP,
      members: [
        { transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', payee: 'DINNER', people: ['Alice'], txn_type: null },
        { transaction_id: 't2', txn_date: '2026-01-06', amount: 150, debit_credit: 'credit', raw_description: 'REPAY', payee: 'REPAY', people: ['Alice'], txn_type: null },
      ],
      summary: { ...GROUP.summary, has_people: true, settle_up: [] },
    })
    renderCard(GROUP)
    fireEvent.click(screen.getByText('Goa Trip'))
    await waitFor(() => expect(screen.getByText("Everyone's settled up")).toBeInTheDocument())
  })

  it('shows no settle-up section when nobody is tagged', async () => {
    mockDetail({
      ...GROUP,
      members: [
        { transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', payee: 'DINNER', people: [], txn_type: null },
      ],
    })
    renderCard(GROUP)
    fireEvent.click(screen.getByText('Goa Trip'))
    // jsdom renders both the desktop table and the phone cards.
    await waitFor(() => expect(screen.getAllByText('DINNER').length).toBeGreaterThan(0))
    expect(screen.queryByText('Settle up')).not.toBeInTheDocument()
  })

  it('has no role column - settle-up is driven by type alone', async () => {
    mockDetail({
      ...GROUP,
      members: [
        { transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', payee: 'DINNER', people: ['Alice'], txn_type: 'split', role: 'paid' },
      ],
      summary: { ...GROUP.summary, has_people: true, settle_up: [{ person: 'Alice', net: 150 }] },
    })
    renderCard(GROUP)
    fireEvent.click(screen.getByText('Goa Trip'))
    // jsdom renders both the desktop table and the phone cards.
    await waitFor(() => expect(screen.getAllByText('DINNER').length).toBeGreaterThan(0))
    expect(screen.queryByText('Role')).not.toBeInTheDocument()
  })

  it('reads the settle-up body from the same summary the header uses', async () => {
    const withBalance = {
      ...GROUP,
      members: [
        { transaction_id: 't1', txn_date: '2026-01-05', amount: 300, debit_credit: 'debit', raw_description: 'DINNER', payee: 'DINNER', people: ['p1'], txn_type: null },
      ],
      // A stray top-level field must not drive the body.
      settle_up: [{ person: 'p9', name: 'Stale', net: 1 }],
      summary: { ...GROUP.summary, has_people: true, settle_up: [{ person: 'p1', name: 'Alice', net: 150 }] },
    }
    mockDetail(withBalance)
    renderCard(GROUP)
    fireEvent.click(screen.getByText('Goa Trip'))
    await waitFor(() => expect(screen.getByText('Settle up')).toBeInTheDocument())
    expect(screen.queryByText('Stale')).not.toBeInTheDocument()
    expect(screen.getByText(/owes you/)).toHaveTextContent('owes you ₹150')
  })
})

describe('GroupCard failures', () => {
  beforeEach(() => vi.clearAllMocks())

  it('says the group could not load, inside the card, and recovers on Retry', async () => {
    mockApi.get.mockRejectedValue(new TypeError('Failed to fetch'))
    renderCard(GROUP)
    fireEvent.click(screen.getByRole('button', { name: 'Expand Goa Trip' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load this group.")
    expect(screen.queryByText(/No transactions yet/)).not.toBeInTheDocument()
    mockDetail({ ...GROUP, members: [] })
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/No transactions yet/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('GroupCard editing', () => {
  const onUpdate = vi.fn()
  const renderEditable = (group = GROUP) => render(
    <PrivacyProvider><GroupCard group={group} onDelete={() => {}} onUpdate={onUpdate} /></PrivacyProvider>
  )
  beforeEach(() => vi.clearAllMocks())

  it('renames, adds a note and labels, and hands the saved group up', async () => {
    mockApi.patch.mockResolvedValue({ ...GROUP, name: 'Goa 2026', note: 'Flights split', labels: ['travel'] })
    renderEditable()
    fireEvent.click(screen.getByRole('button', { name: 'Edit Goa Trip' }))
    const name = screen.getByLabelText('Group name')
    expect(name).toHaveValue('Goa Trip')
    expect(name).toHaveFocus()
    fireEvent.change(name, { target: { value: 'Goa 2026' } })
    fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'Flights split' } })
    const label = screen.getByPlaceholderText(/type and press Enter/)
    fireEvent.change(label, { target: { value: 'travel' } })
    fireEvent.keyDown(label, { key: 'Enter' })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith('/groups/g1', { name: 'Goa 2026', note: 'Flights split', labels: ['travel'] }))
    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ id: 'g1', name: 'Goa 2026' })))
    expect(screen.queryByLabelText('Group name')).not.toBeInTheDocument()
  })

  it('saves on Enter in the name and cancels on Escape without saving', async () => {
    mockApi.patch.mockResolvedValue({ ...GROUP, name: 'Goa 2026' })
    renderEditable()
    fireEvent.click(screen.getByRole('button', { name: 'Edit Goa Trip' }))
    fireEvent.change(screen.getByLabelText('Group name'), { target: { value: 'Goa 2026' } })
    fireEvent.keyDown(screen.getByLabelText('Group name'), { key: 'Escape' })
    expect(mockApi.patch).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Group name')).not.toBeInTheDocument()
    expect(screen.getByText('Goa Trip')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Edit Goa Trip' }))
    fireEvent.change(screen.getByLabelText('Group name'), { target: { value: 'Goa 2026' } })
    fireEvent.keyDown(screen.getByLabelText('Group name'), { key: 'Enter' })
    await waitFor(() => expect(mockApi.patch).toHaveBeenCalledTimes(1))
  })

  it('shows a refused name in the form and keeps it open', async () => {
    mockApi.patch.mockRejectedValue(Object.assign(new Error('You already have a group called "Flat".'), { status: 409 }))
    renderEditable()
    fireEvent.click(screen.getByRole('button', { name: 'Edit Goa Trip' }))
    fireEvent.change(screen.getByLabelText('Group name'), { target: { value: 'Flat' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('You already have a group called "Flat".')
    expect(screen.getByLabelText('Group name')).toBeInTheDocument()
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it('does not save a blank name', () => {
    renderEditable()
    fireEvent.click(screen.getByRole('button', { name: 'Edit Goa Trip' }))
    fireEvent.change(screen.getByLabelText('Group name'), { target: { value: '  ' } })
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })
})
