import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readStored, writeStored } from './storage.js'
import { PrivacyProvider, usePrivacy } from '../contexts/PrivacyContext.jsx'
import { LedgerProvider } from '../contexts/LedgerContext.jsx'
import { PeriodProvider, usePeriod } from '../contexts/PeriodContext.jsx'

vi.mock('./api.js', () => ({ api: { get: vi.fn(() => Promise.resolve([])) } }))

afterEach(() => vi.unstubAllGlobals())

function blockStorage() {
  const boom = () => { throw new DOMException('blocked', 'SecurityError') }
  vi.stubGlobal('localStorage', { getItem: boom, setItem: boom, removeItem: boom })
}

describe('storage helpers', () => {
  it('read and write through to localStorage', () => {
    writeStored('k', 'v')
    expect(readStored('k')).toBe('v')
    writeStored('k', null)
    expect(readStored('k')).toBeNull()
  })
})

describe('providers with blocked storage', () => {
  it('mount and work for the privacy, statement and period providers', async () => {
    blockStorage()
    function Probe() {
      const { hidden } = usePrivacy()
      const { month } = usePeriod()
      return <p>{String(hidden)} {String(month)}</p>
    }
    render(
      <PrivacyProvider><LedgerProvider><PeriodProvider><Probe /></PeriodProvider></LedgerProvider></PrivacyProvider>,
    )
    expect(await screen.findByText('false null')).toBeInTheDocument()
  })
})
