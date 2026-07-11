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
