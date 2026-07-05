import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// Mock the API layer so the queue is hermetic (no backend, no fetch).
const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({
  api: mockApi,
  ApiError: class ApiError extends Error {},
}))

// Stub toasts to a noop: the real provider schedules a setTimeout to auto-dismiss,
// which fires after the test ends and trips React's act() warning. These specs
// assert behavior, not toast copy.
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => () => {},
}))

import { ToastProvider } from '../contexts/ToastContext.jsx'
import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import ReviewQueue from './ReviewQueue.jsx'

const CATEGORIES = [
  { id: 1, name: 'Food', parent_id: null },
  { id: 2, name: 'Transport', parent_id: null },
  { id: 3, name: 'Groceries', parent_id: 1 },
]

function makeCard(overrides = {}) {
  return {
    annotation_id: 10,
    transaction_id: 100,
    txn_date: '2026-01-01',
    amount: 250,
    debit_credit: 'debit',
    raw_description: 'SWIGGY ORDER 8842',
    category: 'Food',
    subcategory: '',
    merchant: 'Swiggy',
    tags: [],
    confidence: 0.5,
    source: 'llm',
    ...overrides,
  }
}

// Route api.get by path; the queue fetches review-queue, categories and config on mount.
function stubGet(queue) {
  mockApi.get.mockImplementation(path => {
    if (path === '/annotations/review-queue') return Promise.resolve(queue)
    if (path === '/categories') return Promise.resolve(CATEGORIES)
    if (path === '/config') return Promise.resolve({ dev_mode: false, confidence_threshold: 0.85 })
    if (path.endsWith('/similar')) return Promise.resolve([]) // no propagation candidates
    return Promise.resolve(null)
  })
}

// Render and wait until all three mount fetches (review-queue, categories, config)
// have settled, so no stray setState lands during teardown.
async function renderQueue() {
  render(
    <ToastProvider>
      <PrivacyProvider>
        <ReviewQueue />
      </PrivacyProvider>
    </ToastProvider>
  )
  await screen.findByText('SWIGGY ORDER 8842')
  await act(async () => {}) // flush the remaining categories/config promises
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.post.mockResolvedValue({})
  mockApi.patch.mockResolvedValue({})
})

describe('ReviewQueue keyboard shortcuts', () => {
  it('confirms a clean card on Enter (no edits) via the confirm endpoint', async () => {
    stubGet([makeCard()])
    await renderQueue()

    // A pristine card shows the Confirm affordance, not Save & Next.
    expect(screen.getByRole('button', { name: /Confirm/ })).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'Enter' })

    // Await the terminal UI first so every post-save state update flushes inside act.
    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/10/confirm', {})
    expect(mockApi.patch).not.toHaveBeenCalled()
  })

  it('confirms a clean card on the "c" key too', async () => {
    stubGet([makeCard()])
    await renderQueue()

    fireEvent.keyDown(window, { key: 'c' })

    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/10/confirm', {})
    expect(mockApi.patch).not.toHaveBeenCalled()
  })

  it('saves an edit (not a stale confirm) once the card is dirty', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    await renderQueue()

    // Change the category -> card becomes dirty -> the CTA flips to Save & Next.
    await user.click(screen.getByRole('button', { name: 'Transport' }))
    expect(screen.getByRole('button', { name: /Save & Next/ })).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'c' })

    await screen.findByText("You're all done!")
    expect(mockApi.patch).toHaveBeenCalledWith(
      '/annotations/10',
      expect.objectContaining({ category: 'Transport' })
    )
    // Crucially, a dirty card must NOT fire the confirm endpoint.
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/10/confirm', {})
  })

  it('skips without saving anything', async () => {
    stubGet([makeCard(), makeCard({ annotation_id: 11, raw_description: 'UBER TRIP' })])
    await renderQueue()

    fireEvent.keyDown(window, { key: 's' })

    // Advances to the next card, and neither save endpoint was touched.
    await screen.findByText('UBER TRIP')
    expect(mockApi.patch).not.toHaveBeenCalled()
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/10/confirm', {})
  })

  it('does not react to shortcuts while typing in an input', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    await renderQueue()

    // Reveal the merchant field and type into it; "c"/"s" here are text, not shortcuts.
    await user.click(screen.getByText(/Edit merchant & tags/))
    const merchant = screen.getByDisplayValue('Swiggy') // merchant input (label isn't htmlFor-linked)
    await user.click(merchant)
    await user.keyboard('cs')

    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/10/confirm', {})
    expect(merchant).toHaveValue('Swiggycs')
  })
})
