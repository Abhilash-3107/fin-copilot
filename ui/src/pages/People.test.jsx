import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

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
import People from './People.jsx'

function renderPeople() {
  return render(
    <PrivacyProvider>
      <MemoryRouter initialEntries={['/people']}>
        <Routes>
          <Route path="/people" element={<People />} />
          <Route path="/transactions" element={<div>Transactions Page</div>} />
        </Routes>
      </MemoryRouter>
    </PrivacyProvider>
  )
}

const PEOPLE = [
  { id: 'p1', name: 'Karabi', upi: 'karabi', relationship: 'sister', stats: { matched: 3, sent: 500, received: 200, net: -300 } },
  { id: 'p2', name: 'NoMatch', upi: null, relationship: null, stats: { matched: 0, sent: 0, received: 0, net: 0 } },
]

describe('People', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockResolvedValue(PEOPLE)
  })

  it('shows matched count and net columns', async () => {
    renderPeople()
    await waitFor(() => expect(screen.getByText('Karabi')).toBeInTheDocument())
    expect(screen.getByText('3')).toBeInTheDocument()
    // net -300 renders with the rupee sign
    expect(screen.getByText(/₹300/)).toBeInTheDocument()
  })

  it('offers Landlord and shows a relationship the list does not know', async () => {
    mockApi.get.mockResolvedValue([
      { id: 'p5', name: 'Amit', upi: 'amit', relationship: 'landlord', stats: { matched: 1, sent: 100, received: 0, net: -100 } },
      { id: 'p6', name: 'Zoya', upi: 'zoya', relationship: 'cousin', stats: { matched: 1, sent: 100, received: 0, net: -100 } },
    ])
    renderPeople()
    await waitFor(() => expect(screen.getByText('Amit')).toBeInTheDocument())
    const [amit, zoya] = screen.getAllByRole('combobox').filter(el => el.closest('tr'))
    expect(amit).toHaveValue('landlord')
    expect(zoya).toHaveValue('cousin')
  })

  it('states what the figure counts and over what period', async () => {
    renderPeople()
    await waitFor(() => expect(screen.getByText('Karabi')).toBeInTheDocument())
    expect(screen.getByText(/Sent and received/)).toBeInTheDocument()
    expect(screen.getByText(/all time/i)).toBeInTheDocument()
    expect(screen.queryByText('Net')).not.toBeInTheDocument()
  })

  it('reads the sign in words and does not colour sending money red', async () => {
    mockApi.get.mockResolvedValue([
      PEOPLE[0],
      { id: 'p3', name: 'Neha', upi: 'neha', relationship: null, stats: { matched: 2, sent: 100, received: 4800, net: 4700 } },
    ])
    renderPeople()
    await waitFor(() => expect(screen.getByText('Karabi')).toBeInTheDocument())
    const sent = screen.getByText(/more sent/)
    expect(sent).toHaveTextContent('₹300 more sent')
    expect(screen.getByText(/more received/)).toHaveTextContent('₹4,700 more received')
    expect(sent.className).not.toMatch(/red|green/)
    expect(screen.getByText(/more received/).className).not.toMatch(/red|green/)
  })

  it('says Even for a net that rounds to zero rupees, never "₹0 more sent"', async () => {
    mockApi.get.mockResolvedValue([
      { id: 'p4', name: 'Tiny', upi: 'tiny', relationship: null, stats: { matched: 2, sent: 100, received: 99.7, net: -0.3 } },
      { id: 'p5', name: 'Flat', upi: 'flat', relationship: null, stats: { matched: 2, sent: 100, received: 100, net: 0 } },
    ])
    renderPeople()
    await waitFor(() => expect(screen.getByText('Tiny')).toBeInTheDocument())
    expect(screen.getAllByText('Even')).toHaveLength(2)
    expect(screen.queryByText(/more sent/)).not.toBeInTheDocument()
  })

  it('uses plain hyphens, not em dashes, for empty cells', async () => {
    renderPeople()
    await waitFor(() => expect(screen.getByText('NoMatch')).toBeInTheDocument())
    expect(document.body.textContent).not.toContain('\u2014')
  })

  it('counts contacts with the right plural', async () => {
    mockApi.get.mockResolvedValue([PEOPLE[0]])
    renderPeople()
    expect(await screen.findByText('1 contact')).toBeInTheDocument()
  })

  it('clicks the matched count through to filtered transactions', async () => {
    renderPeople()
    await waitFor(() => expect(screen.getByText('Karabi')).toBeInTheDocument())
    fireEvent.click(screen.getByText('3'))
    await waitFor(() => expect(screen.getByText('Transactions Page')).toBeInTheDocument())
  })

  it('opens the add-person modal instead of an inline form', async () => {
    renderPeople()
    await waitFor(() => expect(screen.getByText('Karabi')).toBeInTheDocument())
    // No modal heading until the trigger is clicked.
    expect(screen.queryByText('Add a person')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Add Person/i }))
    expect(screen.getByText('Add a person')).toBeInTheDocument()
  })
})
