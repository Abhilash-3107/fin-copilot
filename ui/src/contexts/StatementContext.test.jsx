import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({ mockApi: { get: vi.fn() } }))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class ApiError extends Error {} }))

import { StatementProvider, useStatement } from './StatementContext.jsx'

function Probe() {
  const { statements, months, loading, error } = useStatement()
  if (loading) return <p>loading</p>
  if (error) return <p>failed</p>
  return <p>{`${statements.length} statements, months ${months.join(' ')}`}</p>
}

beforeEach(() => {
  localStorage.clear()
  mockApi.get.mockReset()
})

describe('StatementProvider', () => {
  it('reads the months that have payments with the statements, so one statement can give two months', async () => {
    mockApi.get.mockImplementation(path => Promise.resolve(
      path === '/statements'
        ? [{ id: 's1', statement_month: '2026-10', uploaded_at: '2026-11-16' }]
        : { months: ['2026-10', '2026-11'] },
    ))
    render(<StatementProvider><Probe /></StatementProvider>)
    expect(await screen.findByText('1 statements, months 2026-10 2026-11')).toBeInTheDocument()
  })

  it('says the list could not load when the months cannot be read, instead of showing a ledger with none', async () => {
    mockApi.get.mockImplementation(path => (
      path === '/transactions/months' ? Promise.reject(new Error('down')) : Promise.resolve([])
    ))
    render(<StatementProvider><Probe /></StatementProvider>)
    expect(await screen.findByText('failed')).toBeInTheDocument()
  })
})
