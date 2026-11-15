import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {} }))

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import { ToastProvider } from '../contexts/ToastContext.jsx'
import ReviewPolicies from './ReviewPolicies.jsx'

const POLICY = { id: 'p1', max_amount: 500, category: 'Transport', subcategory: null, enabled: true }

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
  mockApi.get.mockImplementation(path => Promise.resolve(path === '/policies' ? [POLICY] : []))
  mockApi.patch.mockResolvedValue({ ...POLICY, max_amount: 700, resolved: 0 })
})

describe('policy limits and privacy mode', () => {
  // A limit is a rule setting, not a money figure: it stays readable with
  // amounts hidden, in the field and in the toast that confirms it.
  it('keeps the limit visible in the field and in its toast while amounts are hidden', async () => {
    localStorage.setItem('fc_privacy_hidden', '1')
    render(<PrivacyProvider><ToastProvider><ReviewPolicies /></ToastProvider></PrivacyProvider>)

    const limit = await screen.findByLabelText('Amount limit')
    expect(limit).toHaveValue(500)
    fireEvent.change(limit, { target: { value: '700' } })
    fireEvent.blur(limit)

    expect(await screen.findByText('Limit set to ₹700.')).toBeInTheDocument()
  })
})
