import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {} }))

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import CommandPalette from './CommandPalette.jsx'

function renderPalette() {
  return render(
    <PrivacyProvider>
      <MemoryRouter>
        <CommandPalette />
      </MemoryRouter>
    </PrivacyProvider>
  )
}

function openPalette() {
  fireEvent.keyDown(window, { key: 'k', metaKey: true })
}

describe('CommandPalette', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/transactions')) return Promise.resolve([
        { id: 't1', txn_date: '2026-01-05', amount: 1200, debit_credit: 'debit', raw_description: 'SWIGGY', merchant: 'Swiggy' },
      ])
      if (path.startsWith('/people')) return Promise.resolve([{ id: 'p1', name: 'Karabi', upi: 'karabi' }])
      if (path.startsWith('/groups')) return Promise.resolve([{ id: 'g1', name: 'Goa Trip' }])
      return Promise.resolve([])
    })
  })

  it('is hidden until Cmd-K is pressed', () => {
    renderPalette()
    expect(screen.queryByPlaceholderText(/Search transactions/)).not.toBeInTheDocument()
    openPalette()
    expect(screen.getByPlaceholderText(/Search transactions/)).toBeInTheDocument()
  })

  it('shows jump-to-page results with no query', () => {
    renderPalette()
    openPalette()
    expect(screen.getByText('Go to')).toBeInTheDocument()
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
  })

  it('searches across transactions, people and groups', async () => {
    renderPalette()
    openPalette()
    fireEvent.change(screen.getByPlaceholderText(/Search transactions/), { target: { value: 'swiggy' } })
    await waitFor(() => expect(screen.getByText('Transactions')).toBeInTheDocument())
    expect(screen.getByText('Swiggy')).toBeInTheDocument()
    expect(screen.getByText('Karabi')).toBeInTheDocument()
    expect(screen.getByText('Goa Trip')).toBeInTheDocument()
  })

  it('closes on Escape', () => {
    renderPalette()
    openPalette()
    fireEvent.keyDown(screen.getByPlaceholderText(/Search transactions/), { key: 'Escape' })
    expect(screen.queryByPlaceholderText(/Search transactions/)).not.toBeInTheDocument()
  })
})
