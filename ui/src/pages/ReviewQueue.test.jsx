import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

// Mock the API layer so the queue is hermetic (no backend, no fetch).
const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({
  api: mockApi,
  ApiError: class ApiError extends Error {},
}))

// Stub toasts to a spy: the real provider schedules a setTimeout to auto-dismiss,
// which fires after the test ends and trips React's act() warning. Most specs
// assert behavior, not toast copy.
const { toastSpy } = vi.hoisted(() => ({ toastSpy: vi.fn() }))
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => toastSpy,
}))

// The page reads the single job slot for the "haven't been sorted yet" line.
const { jobCtx } = vi.hoisted(() => ({ jobCtx: { job: null, startAnnotation: null } }))
vi.mock('../contexts/AnnotationJobContext.jsx', () => ({
  useAnnotationJob: () => jobCtx,
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
    group_key: `P${overrides.transaction_id ?? 100}|debit`,
    group_size: 1,
    payee: 'SWIGGY',
    ...overrides,
  }
}

// Route api.get by path; the queue fetches review-queue, categories and config on mount.
// `queue` may be a function of the fetch number to script each response.
let snoozed = 0
let snoozedUnsorted = null // null: every set-aside payment has no real category
let waiting = null // null: derive from the served list
let aiActive = true
let anyTransactions = true
// By default the server serves `queue` once, then an empty list: whatever the
// test answers has left it by the time the page asks again.
function stubGet(queue) {
  let served = 0
  mockApi.get.mockImplementation(path => {
    if (path === '/annotations/review-queue') {
      if (typeof queue === 'function') return Promise.resolve(queue(served++))
      return Promise.resolve(served++ === 0 ? queue : [])
    }
    if (path === '/annotations/review-queue/counts') {
      return Promise.resolve({
        unsorted: waiting ?? 0, guesses: 0, set_aside: snoozed, set_aside_unsorted: snoozedUnsorted ?? snoozed,
      })
    }
    if (path === '/transactions?limit=1') return Promise.resolve(anyTransactions ? [{ id: 't' }] : [])
    if (path === '/categories') return Promise.resolve(CATEGORIES)
    if (path === '/config') return Promise.resolve({ dev_mode: false, confidence_threshold: 0.85, ai_active: aiActive })
    if (path.endsWith('/similar')) return Promise.resolve([]) // no propagation candidates
    if (path === '/policies') return Promise.resolve([])
    return Promise.resolve(null)
  })
}

// Render and wait until all three mount fetches (review-queue, categories, config)
// have settled, so no stray setState lands during teardown.
async function renderQueue() {
  renderPage()
  await screen.findByText('SWIGGY ORDER 8842')
  await act(async () => {}) // flush the remaining categories/config promises
}

function renderPage() {
  render(
    <MemoryRouter>
      <PrivacyProvider>
        <ToastProvider>
          <ReviewQueue />
        </ToastProvider>
      </PrivacyProvider>
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  snoozed = 0
  snoozedUnsorted = null
  waiting = null
  aiActive = true
  anyTransactions = true
  jobCtx.job = null
  jobCtx.startAnnotation = vi.fn()
  mockApi.post.mockResolvedValue({ reevaluated: [] })
  mockApi.patch.mockResolvedValue({ reevaluated: [] })
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
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
    expect(mockApi.patch).not.toHaveBeenCalled()
  })

  it('confirms a clean card on the "c" key too', async () => {
    stubGet([makeCard()])
    await renderQueue()

    fireEvent.keyDown(window, { key: 'c' })

    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
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
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
  })

  it('skips persistently via the snooze endpoint without saving anything', async () => {
    stubGet([makeCard(), makeCard({ annotation_id: 11, raw_description: 'UBER TRIP' })])
    await renderQueue()

    fireEvent.keyDown(window, { key: 's' })

    // Advances to the next card; the skip persists as a snooze (4.15), and
    // neither save endpoint is touched.
    await screen.findByText('UBER TRIP')
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/review-queue/skip', { transaction_id: 100 })
    expect(mockApi.patch).not.toHaveBeenCalled()
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
  })

  it('ignores repeat keys while a confirm is in flight', async () => {
    stubGet([makeCard(), makeCard({ annotation_id: 11, transaction_id: 101, raw_description: 'UBER TRIP' })])
    let finish
    mockApi.post.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    await renderQueue()

    // A quick double press, then skip and back, all before the save returns.
    fireEvent.keyDown(window, { key: 'c' })
    fireEvent.keyDown(window, { key: 'c' })
    fireEvent.keyDown(window, { key: 's' })
    fireEvent.keyDown(window, { key: 'b' })
    await act(async () => finish({ reevaluated: [] }))

    // One request, and the queue moved exactly one card: nothing skipped unseen.
    await screen.findByText('UBER TRIP')
    expect(mockApi.post.mock.calls).toEqual([['/annotations/10/confirm', { category: 'Food' }]])
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

    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
    expect(merchant).toHaveValue('Swiggycs')
  })
})

describe('ReviewQueue teaching propagation', () => {
  it('drops siblings the server resolved when a card is confirmed', async () => {
    stubGet([
      makeCard(),
      makeCard({ annotation_id: 11, transaction_id: 101, raw_description: 'SWIGGY ORDER 9911' }),
      makeCard({ annotation_id: 12, transaction_id: 102, raw_description: 'UBER TRIP 77' }),
    ])
    mockApi.post.mockResolvedValue({ reevaluated: [
      { transaction_id: 101, category: 'Food', subcategory: null, merchant: null, confidence: 0.95,
        source: 'learned_rule', resolved: true },
      { transaction_id: 102, category: 'Transport', subcategory: 'Cab & Auto', merchant: null, confidence: 0.6,
        source: 'rag_direct', resolved: false },
    ] })
    await renderQueue()

    fireEvent.keyDown(window, { key: 'Enter' })

    // The resolved sibling is skipped over; the changed one shows its new label.
    expect(await screen.findByText('UBER TRIP 77')).toBeInTheDocument()
    expect(screen.queryByText('SWIGGY ORDER 9911')).not.toBeInTheDocument()
    expect(screen.getByText('Cab & Auto', { selector: 'span' })).toBeInTheDocument()
  })

  it('reloads instead of confirming a label that changed under the card', async () => {
    const { ApiError } = await import('../lib/api.js')
    stubGet([makeCard()])
    mockApi.post.mockRejectedValue(Object.assign(new ApiError('now Transport'), { status: 409 }))
    await renderQueue()
    const fetches = mockApi.get.mock.calls.filter(([p]) => p === '/annotations/review-queue').length

    fireEvent.keyDown(window, { key: 'Enter' })
    await act(async () => {})

    expect(mockApi.get.mock.calls.filter(([p]) => p === '/annotations/review-queue').length).toBe(fetches + 1)
  })
})

describe('ReviewQueue payee groups', () => {
  const group = (id, desc, extra = {}) => makeCard({
    annotation_id: id, transaction_id: 100 + id, raw_description: desc,
    group_key: 'OBEROIFC TUCKSH|debit', group_size: 2, payee: 'OBEROIFC tucksh', ...extra,
  })

  it('confirms every queued payment to the payee in one decision', async () => {
    stubGet([
      group(10, 'SWIGGY ORDER 8842'),
      group(11, 'OBEROIFC SECOND 1'),
      makeCard({ annotation_id: 12, transaction_id: 112, raw_description: 'UBER TRIP 77', group_key: 'UBER|debit', group_size: 1 }),
    ])
    mockApi.post.mockResolvedValue({ updated: 2, reevaluated: [] })
    await renderQueue()
    expect(screen.getByText(/Apply my answer to all 2 payments to/)).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'Enter' })

    expect(await screen.findByText('UBER TRIP 77')).toBeInTheDocument()
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/bulk-label', {
      transaction_ids: [110, 111], label: { category: 'Food', subcategory: null },
    })
    expect(screen.queryByText('OBEROIFC SECOND 1')).not.toBeInTheDocument()
  })

  it('names the payee, not this payment\'s own name, when a group is confirmed', async () => {
    stubGet([
      group(10, 'SWIGGY ORDER 8842', { payee: 'March rent', payee_all: 'Rent' }),
      group(11, 'OBEROIFC SECOND 1', { payee: 'Rent', payee_all: 'Rent' }),
    ])
    mockApi.post.mockResolvedValue({ updated: 2, reevaluated: [] })
    await renderQueue()

    fireEvent.keyDown(window, { key: 'Enter' })

    await vi.waitFor(() => expect(toastSpy).toHaveBeenCalledWith(expect.stringContaining('all 2 from Rent confirmed'), 'success'))
  })

  it('"a" turns the group off so only this card is confirmed', async () => {
    stubGet([group(10, 'SWIGGY ORDER 8842'), group(11, 'OBEROIFC SECOND 1')])
    await renderQueue()

    fireEvent.keyDown(window, { key: 'a' })
    fireEvent.keyDown(window, { key: 'Enter' })

    expect(await screen.findByText('OBEROIFC SECOND 1')).toBeInTheDocument()
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
  })

  it('keeps the keyboard after the group checkbox is clicked', async () => {
    stubGet([group(10, 'SWIGGY ORDER 8842'), group(11, 'OBEROIFC SECOND 1')])
    await renderQueue()

    const box = screen.getByRole('checkbox')
    fireEvent.click(box)
    fireEvent.keyDown(box, { key: 'c' })

    expect(await screen.findByText('OBEROIFC SECOND 1')).toBeInTheDocument()
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
  })

  it('offers a policy after three small payments to people get the same label', async () => {
    await offerPolicy()
    fireEvent.click(await screen.findByRole('button', { name: 'Yes, always' }))
    await act(async () => {})
    expect(mockApi.post).toHaveBeenCalledWith('/policies', {
      max_amount: 500, category: 'Transport', subcategory: 'Cab & Auto',
    })
  })

  it('keeps the suggested limit readable while amounts are hidden', async () => {
    // A policy limit is a rule setting, not a money figure.
    localStorage.setItem('fc_privacy_hidden', '1')
    await offerPolicy()
    expect(await screen.findByText(/Sort small payments \(up to ₹500\)/)).toBeInTheDocument()
  })

  async function offerPolicy() {
    const person = (id, desc) => makeCard({
      annotation_id: id, transaction_id: 100 + id, raw_description: desc, amount: 40,
      category: 'Transport', subcategory: 'Cab & Auto', payee_kind: 'person',
      group_key: `P${id}|debit`, group_size: 1,
    })
    stubGet([person(10, 'SWIGGY ORDER 8842'), person(11, 'RIDE TWO'), person(12, 'RIDE THREE'), person(13, 'RIDE FOUR')])
    mockApi.post.mockImplementation(path => Promise.resolve(
      path === '/policies' ? { resolved: 1 } : path === '/policies/preview' ? { matches: 2 } : { reevaluated: [] }
    ))
    await renderQueue()

    for (const next of ['RIDE TWO', 'RIDE THREE', 'RIDE FOUR']) {
      fireEvent.keyDown(window, { key: 'Enter' })
      await screen.findByText(next)
    }
  }
})

describe('ReviewQueue custom categories', () => {
  it('creates a new category from the chips, selects it, and saves the card with it', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    mockApi.post.mockImplementation(path => {
      if (path === '/categories') return Promise.resolve({ id: 99, name: 'Pet Care', parent_id: null })
      return Promise.resolve({ reevaluated: [] })
    })
    await renderQueue()

    await user.click(screen.getByRole('button', { name: /New category/ }))
    await user.type(screen.getByLabelText('New category name'), 'Pet Care{Enter}')

    expect(mockApi.post).toHaveBeenCalledWith('/categories', { name: 'Pet Care' })
    // The new chip appears, is selected, and the card is now dirty.
    expect(await screen.findByRole('button', { name: 'Pet Care' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Save & Next/ })).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'c' })
    await screen.findByText("You're all done!")
    expect(mockApi.patch).toHaveBeenCalledWith(
      '/annotations/10',
      expect.objectContaining({ category: 'Pet Care' })
    )
  })

  it('creates a subcategory under the selected category', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    mockApi.post.mockImplementation((path, body) => {
      if (path === '/categories/subcategories') {
        return Promise.resolve({ id: 100, name: body.name, parent_id: body.parent_id })
      }
      return Promise.resolve({ reevaluated: [] })
    })
    await renderQueue()

    await user.click(screen.getByRole('button', { name: /New subcategory/ }))
    await user.type(screen.getByLabelText('New subcategory in Food'), 'Street Food{Enter}')

    expect(mockApi.post).toHaveBeenCalledWith('/categories/subcategories', { parent_id: 1, name: 'Street Food' })
    expect(await screen.findByRole('button', { name: 'Street Food' })).toBeInTheDocument()
  })

  it('keeps the field open when the server rejects the name', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    mockApi.post.mockRejectedValue(new Error("'Food' already exists"))
    await renderQueue()

    await user.click(screen.getByRole('button', { name: /New category/ }))
    await user.type(screen.getByLabelText('New category name'), 'Food{Enter}')

    expect(await screen.findByLabelText('New category name')).toHaveValue('Food')
  })
})

describe('ReviewQueue unsorted payments', () => {
  const bare = (id, desc, extra = {}) => makeCard({
    annotation_id: null, transaction_id: id, raw_description: desc, category: null, subcategory: null,
    merchant: null, confidence: 0, source: null, ...extra,
  })

  it('says "Nothing to sort yet" on a fresh install, never "all done"', async () => {
    anyTransactions = false
    stubGet([])
    renderPage()
    expect(await screen.findByText('Nothing to sort yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add a statement' })).toHaveAttribute('href', '/upload')
    expect(screen.queryByText(/all done/)).not.toBeInTheDocument()
  })

  it('says how many payments are set aside instead of "all done"', async () => {
    snoozed = 3
    stubGet([])
    renderPage()
    expect(await screen.findByText('3 payments are set aside')).toBeInTheDocument()
    expect(screen.queryByText(/all done/)).not.toBeInTheDocument()
    mockApi.post.mockResolvedValue({ restored: 3 })
    stubGet(() => [makeCard()])
    snoozed = 0
    fireEvent.click(screen.getByRole('button', { name: 'Review them again' }))
    expect(await screen.findByText('SWIGGY ORDER 8842')).toBeInTheDocument()
  })

  it('says the work is done only when the server agrees', async () => {
    // The last local card is answered, but the server has one more waiting.
    stubGet(n => (n === 0 ? [makeCard()] : n === 1 ? [makeCard({ annotation_id: 11, transaction_id: 101, raw_description: 'UBER TRIP' })] : []))
    await renderQueue()
    fireEvent.keyDown(window, { key: 'Enter' })
    expect(await screen.findByText('UBER TRIP')).toBeInTheDocument()
    expect(screen.queryByText("You're all done!")).not.toBeInTheDocument()
  })

  it('shows a line about payments that have not been through automatic sorting, and starts it', async () => {
    stubGet([bare(100, 'SWIGGY ORDER 8842'), bare(101, 'UBER TRIP')])
    jobCtx.startAnnotation.mockResolvedValue({ ai_active: false, rule_matched: 0, low_confidence: 2 })
    await renderQueue()
    expect(screen.getByText(
      '2 payments have no guess yet. Pick a category for each below, or run automatic sorting.'
    )).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Sort my transactions/ }))
    await act(async () => {})
    expect(jobCtx.startAnnotation).toHaveBeenCalledWith({})
  })

  it('says sorting is running while a job holds the slot', async () => {
    jobCtx.job = { status: 'running', kind: 'annotate' }
    stubGet([bare(100, 'SWIGGY ORDER 8842')])
    await renderQueue()
    expect(screen.getByText(/Automatic sorting is running/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Sort my transactions/ })).not.toBeInTheDocument()
  })

  it('shows no line when every queued payment has a guess', async () => {
    stubGet([makeCard()])
    await renderQueue()
    expect(screen.queryByText(/no guess yet/)).not.toBeInTheDocument()
  })

  it('uses the singular for one payment', async () => {
    stubGet([bare(100, 'SWIGGY ORDER 8842')])
    await renderQueue()
    expect(screen.getByText(
      '1 payment has no guess yet. Pick a category for it below, or run automatic sorting.'
    )).toBeInTheDocument()
  })

  it('offers no run when no model is configured', async () => {
    aiActive = false
    stubGet([bare(100, 'SWIGGY ORDER 8842'), bare(101, 'UBER TRIP')])
    await renderQueue()
    expect(screen.getByText('2 payments have no guess yet. Pick a category for each below.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Sort my transactions/ })).not.toBeInTheDocument()
  })

  it('stops offering the run once a run has left the payments bare', async () => {
    jobCtx.startAnnotation.mockResolvedValue({ ai_active: true, llm_failed: 2 })
    stubGet(() => [bare(100, 'SWIGGY ORDER 8842'), bare(101, 'UBER TRIP')])
    await renderQueue()
    fireEvent.click(screen.getByRole('button', { name: /Sort my transactions/ }))
    await act(async () => {})
    expect(await screen.findByText('2 payments have no guess yet. Pick a category for each below.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Sort my transactions/ })).not.toBeInTheDocument()
  })

  it('does not offer Confirm on a payment with no guess; Enter does nothing until a category is picked', async () => {
    const user = userEvent.setup()
    stubGet([bare(100, 'SWIGGY ORDER 8842')])
    await renderQueue()
    expect(screen.queryByRole('button', { name: /Confirm/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pick a category' })).toBeInTheDocument()
    expect(screen.getByText('No guess yet')).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'Enter' })
    fireEvent.keyDown(window, { key: 'c' })
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalled()
    expect(mockApi.patch).not.toHaveBeenCalled()

    // Picking a category answers it by hand, as a plain manual annotation.
    await user.click(screen.getByRole('button', { name: 'Transport' }))
    fireEvent.keyDown(window, { key: 'Enter' })
    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/bulk-label', {
      transaction_ids: [100],
      label: { category: 'Transport', subcategory: null, merchant: null, tags: [] },
    })
    // No similar lookup for a payment with no annotation.
    expect(mockApi.get.mock.calls.some(([p]) => p.endsWith('/similar'))).toBe(false)
  })

  it('does not offer Confirm on an Uncategorized guess either', async () => {
    stubGet([makeCard({ category: 'Uncategorized', confidence: 0.97 })])
    await renderQueue()
    expect(screen.queryByRole('button', { name: /Confirm/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pick a category' })).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Enter' })
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('answers a payee group that mixes guessed and unannotated payments by transaction', async () => {
    const user = userEvent.setup()
    const g = { group_key: 'OBEROI|debit', group_size: 2, payee: 'OBEROI' }
    stubGet([bare(100, 'SWIGGY ORDER 8842', g), makeCard({ annotation_id: 11, transaction_id: 101, raw_description: 'OBEROI TWO', category: 'Uncategorized', ...g })])
    await renderQueue()
    await user.click(screen.getByRole('button', { name: 'Transport' }))
    fireEvent.keyDown(window, { key: 'Enter' })
    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/bulk-label', {
      transaction_ids: [100, 101],
      label: { category: 'Transport', subcategory: null, merchant: null, tags: [] },
    })
  })

  it('skips an unannotated payment by transaction', async () => {
    stubGet([bare(100, 'SWIGGY ORDER 8842'), bare(101, 'UBER TRIP')])
    await renderQueue()
    fireEvent.keyDown(window, { key: 's' })
    await screen.findByText('UBER TRIP')
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/review-queue/skip', { transaction_id: 100 })
  })

  it('leaves no phantom set-aside item after skip, back, confirm', async () => {
    // AUDIT B2: the count comes from the server, which forgets the snooze once the card is answered.
    mockApi.post.mockImplementation(path => {
      if (path === '/annotations/review-queue/skip') snoozed = 1
      else snoozed = 0
      return Promise.resolve({ reevaluated: [] })
    })
    stubGet(n => (n === 0 ? [makeCard(), makeCard({ annotation_id: 11, transaction_id: 101, raw_description: 'UBER TRIP' })] : []))
    await renderQueue()

    fireEvent.keyDown(window, { key: 's' })
    await screen.findByText('UBER TRIP')
    fireEvent.keyDown(window, { key: 'b' })
    await screen.findByText('SWIGGY ORDER 8842')
    fireEvent.keyDown(window, { key: 'Enter' })
    await screen.findByText('UBER TRIP')
    fireEvent.keyDown(window, { key: 'Enter' })

    expect(await screen.findByText("You're all done!")).toBeInTheDocument()
    expect(screen.queryByText(/set aside/)).not.toBeInTheDocument()
    expect(screen.getByText('2 confirmed · 0 corrected · 0 skipped')).toBeInTheDocument()
  })
})

describe('ReviewQueue load failure', () => {
  it('shows an error with Retry, never "all done", when the queue cannot be loaded', async () => {
    let failing = true
    stubGet([makeCard()])
    const routed = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => (
      failing && path === '/annotations/review-queue' ? Promise.reject(new Error('Network down')) : routed(path)
    ))
    renderPage()

    expect(await screen.findByText("Couldn't load your review list")).toBeInTheDocument()
    expect(screen.getByText('Network down')).toBeInTheDocument()
    expect(screen.queryByText(/all done/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Everything is sorted/)).not.toBeInTheDocument()

    failing = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('SWIGGY ORDER 8842')).toBeInTheDocument()
  })

  it('does not keep asking the server while it is failing', async () => {
    stubGet([makeCard()])
    const routed = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => (
      path === '/annotations/review-queue' ? Promise.reject(new Error('Network down')) : routed(path)
    ))
    renderPage()
    await screen.findByText("Couldn't load your review list")
    await act(async () => {})
    expect(mockApi.get.mock.calls.filter(([p]) => p === '/annotations/review-queue')).toHaveLength(1)
  })
})

describe('ReviewQueue keyboard focus', () => {
  const bareCard = (id, desc) => makeCard({
    annotation_id: null, transaction_id: id, raw_description: desc, category: null, subcategory: null,
    merchant: null, confidence: 0, source: null,
  })

  it('Enter on the next card saves nothing after a chip was clicked and saved on the last one', async () => {
    const user = userEvent.setup()
    stubGet([bareCard(100, 'SWIGGY ORDER 8842'), bareCard(101, 'UBER TRIP')])
    await renderQueue()

    await user.click(screen.getByRole('button', { name: 'Transport' }))
    await user.keyboard('{Enter}')
    await screen.findByText('UBER TRIP')
    expect(mockApi.post).toHaveBeenCalledTimes(1) // the save of the first card
    mockApi.post.mockClear()

    // The next card has no pick. Enter must neither choose a category nor save.
    await user.keyboard('{Enter}')
    await act(async () => {})
    expect(screen.getByRole('button', { name: 'Pick a category' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Save & Next/ })).not.toBeInTheDocument()
    expect(mockApi.post).not.toHaveBeenCalled()
    expect(mockApi.patch).not.toHaveBeenCalled()
  })

  it('a keyboard pick keeps its chip focused, and the next card still takes the keyboard', async () => {
    const user = userEvent.setup()
    stubGet([bareCard(100, 'SWIGGY ORDER 8842'), bareCard(101, 'UBER TRIP')])
    await renderQueue()

    const chip = screen.getByRole('button', { name: 'Transport' })
    chip.focus()
    await user.keyboard('{Enter}') // the chip's own activation: picks it, saves nothing
    expect(mockApi.post).not.toHaveBeenCalled()
    expect(chip).toHaveFocus()
    await user.keyboard('c')
    await screen.findByText('UBER TRIP')
    mockApi.post.mockClear()

    await user.keyboard('{Enter}')
    await act(async () => {})
    expect(screen.getByRole('button', { name: 'Pick a category' })).toBeInTheDocument()
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('moves focus to the labelled card when the card changes', async () => {
    stubGet([makeCard(), makeCard({ annotation_id: 11, transaction_id: 101, raw_description: 'UBER TRIP', payee: 'Uber' })])
    await renderQueue()
    const first = screen.getByRole('group', { name: 'Payment to review: SWIGGY' })
    expect(first).toHaveFocus()

    fireEvent.keyDown(window, { key: 's' })
    const second = await screen.findByRole('group', { name: 'Payment to review: Uber' })
    expect(second).toHaveFocus()
  })

  it('does not act on Enter while a button has focus (the button does its own thing)', async () => {
    stubGet([makeCard()])
    await renderQueue()
    const confirmButton = screen.getByRole('button', { name: /Confirm/ })
    fireEvent.keyDown(confirmButton, { key: 'Enter' })
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalled()
  })
})

describe('ReviewQueue wording and counts', () => {
  const bare = (id, desc) => makeCard({
    annotation_id: null, transaction_id: id, raw_description: desc, category: null, subcategory: null,
    merchant: null, confidence: 0, source: null,
  })

  it('shows no source pill on a payment with no guess', async () => {
    stubGet([bare(100, 'SWIGGY ORDER 8842')])
    await renderQueue()
    expect(screen.queryByText('pending')).not.toBeInTheDocument()
    expect(screen.queryByText(/pending/i)).not.toBeInTheDocument()
  })

  it('offers "c confirm" in the legend only when confirming is possible', async () => {
    stubGet([bare(100, 'SWIGGY ORDER 8842')])
    await renderQueue()
    expect(screen.getByText(/Keyboard:/).textContent).not.toMatch(/confirm/)
  })

  it('lists c and Enter in the legend for a guess that can be confirmed', async () => {
    stubGet([makeCard()])
    await renderQueue()
    expect(screen.getByText(/Keyboard:/).textContent).toMatch(/c\s*confirm/)
  })

  it('counts a hand label on a payment with no guess as sorted by hand, not corrected', async () => {
    const user = userEvent.setup()
    stubGet([bare(100, 'SWIGGY ORDER 8842'), makeCard({ annotation_id: 11, transaction_id: 101, raw_description: 'UBER TRIP' })])
    await renderQueue()
    await user.click(screen.getByRole('button', { name: 'Transport' }))
    await user.keyboard('{Enter}')
    await screen.findByText('UBER TRIP')
    expect(screen.getByText(/0 confirmed · 0 corrected · 1 sorted by hand · 0 skipped/)).toBeInTheDocument()
  })

  it('says "still unchecked" for skipped guesses and "still not sorted" for skipped bare payments', async () => {
    snoozed = 2
    snoozedUnsorted = 0
    stubGet([])
    renderPage()
    expect(await screen.findByText('You skipped them, so they are still unchecked.')).toBeInTheDocument()
  })

  it('says "still not sorted" when the skipped payments have no real category', async () => {
    snoozed = 1
    stubGet([])
    renderPage()
    expect(await screen.findByText('You skipped it, so it is still not sorted.')).toBeInTheDocument()
  })

  it('resets the skipped tally when the set-aside payments are brought back', async () => {
    stubGet(n => (n === 0 ? [makeCard(), makeCard({ annotation_id: 11, transaction_id: 101, raw_description: 'UBER TRIP' })] : n === 1 ? [] : [makeCard()]))
    mockApi.post.mockImplementation(path => {
      if (path === '/annotations/review-queue/skip') snoozed = 2
      if (path === '/annotations/review-queue/unsnooze') { snoozed = 0; return Promise.resolve({ restored: 2 }) }
      return Promise.resolve({ reevaluated: [] })
    })
    await renderQueue()
    fireEvent.keyDown(window, { key: 's' })
    await screen.findByText('UBER TRIP')
    fireEvent.keyDown(window, { key: 's' })
    await screen.findByText('2 payments are set aside')
    expect(screen.getByText('0 confirmed · 0 corrected · 2 skipped')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Review them again' }))
    await screen.findByText('SWIGGY ORDER 8842')
    expect(screen.getByText(/0 confirmed · 0 corrected · 0 skipped/)).toBeInTheDocument()
  })

  it('says how much of a long queue is showing', async () => {
    waiting = 230
    stubGet([makeCard()])
    await renderQueue()
    expect(screen.getByText(/showing the first 1 of 230 waiting/)).toBeInTheDocument()
  })

  it('reloads the queue when a sorting run finishes, wherever it was started', async () => {
    jobCtx.job = { status: 'running', kind: 'annotate' }
    stubGet(n => (n === 0 ? [makeCard()] : [makeCard({ annotation_id: 11, transaction_id: 101, raw_description: 'UBER TRIP' })]))
    const { rerender } = render(
      <MemoryRouter><PrivacyProvider><ToastProvider><ReviewQueue /></ToastProvider></PrivacyProvider></MemoryRouter>
    )
    await screen.findByText('SWIGGY ORDER 8842')
    await act(async () => {})

    jobCtx.job = null
    rerender(
      <MemoryRouter><PrivacyProvider><ToastProvider><ReviewQueue /></ToastProvider></PrivacyProvider></MemoryRouter>
    )
    expect(await screen.findByText('UBER TRIP')).toBeInTheDocument()
  })
})
