import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, within, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({ mockApi: { get: vi.fn() } }))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class ApiError extends Error {} }))

import { LedgerProvider } from '../contexts/LedgerContext.jsx'
import { PeriodProvider } from '../contexts/PeriodContext.jsx'
import PeriodPicker from './PeriodPicker.jsx'

// Statements say Nov is the latest statement month, but the server's ledger
// has payments in Sep and Oct only (a statement whose month differs from its data).
const STATEMENTS = [
  { id: 's2', bank_name: 'kotak', statement_month: '2026-11', uploaded_at: '2026-11-20' },
  { id: 's1', bank_name: 'kotak', statement_month: '2026-09', uploaded_at: '2026-10-01' },
]

function renderPicker(props) {
  render(
    <LedgerProvider>
      <PeriodProvider>
        <PeriodPicker {...props} />
      </PeriodProvider>
    </LedgerProvider>
  )
}

beforeEach(() => {
  localStorage.clear()
  mockApi.get.mockImplementation(path => Promise.resolve(
    path === '/statements' ? STATEMENTS : path === '/transactions/months' ? { months: ['2026-09', '2026-10'] } : [],
  ))
})

describe('PeriodPicker on a month screen', () => {
  it('shows the month the page shows, from the months the server reports', async () => {
    localStorage.setItem('fc_period_month', 'all')
    renderPicker({ allowAll: false, shown: '2026-10', months: ['2026-08', '2026-09', '2026-10'] })
    const picker = await screen.findByRole('combobox', { name: 'Period' })
    await waitFor(() => expect(picker).toHaveValue('2026-10'))
    expect(within(picker).getAllByRole('option').map(o => o.value)).toEqual([
      '2026-10', '2026-09', '2026-08',
    ])
  })

  it('keeps the chosen month when the server honours it', async () => {
    localStorage.setItem('fc_period_month', '2026-09')
    renderPicker({ allowAll: false, shown: '2026-10', months: ['2026-08', '2026-09', '2026-10'] })
    const picker = await screen.findByRole('combobox', { name: 'Period' })
    await waitFor(() => expect(picker).toHaveValue('2026-09'))
  })

  it('falls back to the shown month when the chosen one is not in the data', async () => {
    localStorage.setItem('fc_period_month', '2026-11')
    renderPicker({ allowAll: false, shown: '2026-10', months: ['2026-09', '2026-10'] })
    const picker = await screen.findByRole('combobox', { name: 'Period' })
    await waitFor(() => expect(picker).toHaveValue('2026-10'))
  })
})

describe('PeriodPicker on a screen with no month of its own', () => {
  it('offers the months that have payments, newest first, not the statements months', async () => {
    renderPicker({})
    const picker = await screen.findByRole('combobox', { name: 'Period' })
    await waitFor(() => expect(within(picker).getAllByRole('option').map(o => o.value)).toEqual([
      'all', '2026-10', '2026-09',
    ]))
  })

  it('offers a month a statement runs into, though no statement starts in it', async () => {
    // One statement starting 5 Oct and ending 15 Nov: its own month says only October.
    mockApi.get.mockImplementation(path => Promise.resolve(
      path === '/statements'
        ? [{ id: 's1', bank_name: 'generic', statement_month: '2026-10', uploaded_at: '2026-11-16' }]
        : path === '/transactions/months' ? { months: ['2026-10', '2026-11'] } : [],
    ))
    renderPicker({})
    const picker = await screen.findByRole('combobox', { name: 'Period' })
    await waitFor(() => expect(within(picker).getAllByRole('option').map(o => o.value)).toContain('2026-11'))
  })
})
