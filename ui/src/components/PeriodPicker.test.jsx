import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, within, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({ mockApi: { get: vi.fn() } }))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class ApiError extends Error {} }))

import { StatementProvider } from '../contexts/StatementContext.jsx'
import { PeriodProvider } from '../contexts/PeriodContext.jsx'
import PeriodPicker from './PeriodPicker.jsx'

// Statements say Nov is the latest statement month, but the server's ledger
// ends in Oct (a statement whose period_start month differs from its data).
const STATEMENTS = [
  { id: 's2', bank_name: 'kotak', statement_month: '2026-11', uploaded_at: '2026-11-20' },
  { id: 's1', bank_name: 'kotak', statement_month: '2026-09', uploaded_at: '2026-10-01' },
]

function renderPicker(props) {
  render(
    <StatementProvider>
      <PeriodProvider>
        <PeriodPicker {...props} />
      </PeriodProvider>
    </StatementProvider>
  )
}

beforeEach(() => {
  localStorage.clear()
  mockApi.get.mockImplementation(path => Promise.resolve(path === '/statements' ? STATEMENTS : []))
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
