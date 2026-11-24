import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({ mockApi: { get: vi.fn() } }))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class ApiError extends Error {} }))

import { LedgerProvider, useLedger } from './LedgerContext.jsx'

const KOTAK = { id: 'a1', account_label: 'Kotak savings' }
const SBI = { id: 'a2', account_label: 'SBI savings' }

function Probe() {
  const { accounts, months, accountId, multiple, loading, error, setAccountId } = useLedger()
  if (loading) return <p>loading</p>
  if (error) return <p>failed</p>
  return (
    <>
      <p>{`${accounts.length} accounts, months ${months.join(' ')}, account [${accountId}], multiple ${multiple}`}</p>
      <button onClick={() => setAccountId('a2')}>pick</button>
    </>
  )
}

function serve({ accounts, months = ['2026-10', '2026-11'] }) {
  mockApi.get.mockImplementation(path => (
    path === '/accounts' ? Promise.resolve(accounts) : Promise.resolve({ months })
  ))
}

beforeEach(() => {
  localStorage.clear()
  mockApi.get.mockReset()
})

describe('LedgerProvider', () => {
  it('reads the accounts with the months that have payments, so one statement can give two months', async () => {
    serve({ accounts: [KOTAK] })
    render(<LedgerProvider><Probe /></LedgerProvider>)
    expect(await screen.findByText('1 accounts, months 2026-10 2026-11, account [], multiple false')).toBeInTheDocument()
  })

  it('says the ledger could not load when the months cannot be read, instead of showing a ledger with none', async () => {
    mockApi.get.mockImplementation(path => (path === '/transactions/months' ? Promise.reject(new Error('down')) : Promise.resolve([])))
    render(<LedgerProvider><Probe /></LedgerProvider>)
    expect(await screen.findByText('failed')).toBeInTheDocument()
  })

  it('keeps the chosen account for the next visit, once there is a choice', async () => {
    serve({ accounts: [KOTAK, SBI] })
    render(<LedgerProvider><Probe /></LedgerProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'pick' }))
    await waitFor(() => expect(screen.getByText(/account \[a2\]/)).toBeInTheDocument())
    expect(localStorage.getItem('fc_account_id')).toBe('a2')
  })

  it('reads a remembered account that no longer exists as every account', async () => {
    serve({ accounts: [KOTAK, SBI] })
    localStorage.setItem('fc_account_id', 'gone')
    render(<LedgerProvider><Probe /></LedgerProvider>)
    expect(await screen.findByText(/account \[\], multiple true/)).toBeInTheDocument()
  })

  it('has no filter with one account, whatever was remembered', async () => {
    serve({ accounts: [KOTAK] })
    localStorage.setItem('fc_account_id', 'a1')
    render(<LedgerProvider><Probe /></LedgerProvider>)
    expect(await screen.findByText(/account \[\], multiple false/)).toBeInTheDocument()
  })

  it('removes the old "current statement" key from the browser', async () => {
    serve({ accounts: [KOTAK] })
    localStorage.setItem('fc_active_statement_id', 's1')
    render(<LedgerProvider><Probe /></LedgerProvider>)
    await screen.findByText(/1 accounts/)
    expect(localStorage.getItem('fc_active_statement_id')).toBeNull()
  })
})
