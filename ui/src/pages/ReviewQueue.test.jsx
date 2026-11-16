import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, act, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from '../test/router.jsx'

// Mock the API layer so the queue is hermetic (no backend, no fetch).
const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({
  api: mockApi,
  ApiError: class ApiError extends Error {},
}))

// The real provider schedules a setTimeout to auto-dismiss, which fires after
// the test ends and trips React's act() warning, so toasts are a spy here. The
// provider itself (one slot, an action) is tested in ToastContext.test.jsx.
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
import ShortcutSheet from '../components/ShortcutSheet.jsx'
import { TEACH_ME_KEYS } from '../lib/shortcuts.js'

const CATEGORIES = [
  { id: 1, name: 'Food', parent_id: null },
  { id: 2, name: 'Transport', parent_id: null },
  { id: 3, name: 'Groceries', parent_id: 1 },
  { id: 4, name: 'Uncategorized', parent_id: null },
]

const UNDO = '/annotations/review-queue/undo'

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
    payee_kind: 'business',
    note: null,
    suggestions: [{ category: 'Food', subcategory: null, reason: 'AI guess, 50% sure' }],
    ...overrides,
  }
}

const uber = (extra = {}) => makeCard({
  annotation_id: 11, transaction_id: 101, raw_description: 'UBER TRIP', payee: 'UBER', ...extra,
})

const bare = (id, payee, extra = {}) => makeCard({
  annotation_id: null, transaction_id: id, raw_description: `${payee} TRIP`, payee, category: null, subcategory: null,
  merchant: null, confidence: 0, source: null, suggestions: [], ...extra,
})

// Route api.get by path; the queue fetches review-queue, categories and config on mount.
// `queue` may be a function of the fetch number to script each response.
let snoozed = 0
let snoozedUnsorted = null // null: every set-aside payment has no real category
let waiting = null // null: derive from the served list
let aiActive = true
let anyTransactions = true
let learnedRules = () => []
// By default the server serves `queue` once, then an empty list: whatever the
// test answers has left it by the time the page asks again.
function stubGet(queue) {
  let served = 0
  let ruleFetches = 0
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
    if (path === '/annotations/learned-rules') return Promise.resolve(learnedRules(ruleFetches++))
    return Promise.resolve(null)
  })
}

// Render and wait until the mount fetches have settled, so no stray setState
// lands during teardown.
async function renderQueue(first = 'SWIGGY') {
  renderPage()
  await screen.findByRole('heading', { name: first })
  await act(async () => {}) // flush the remaining categories/config promises
}

function renderPage() {
  render(
    <MemoryRouter>
      <PrivacyProvider>
        <ToastProvider>
          <ReviewQueue />
          <ShortcutSheet />
        </ToastProvider>
      </PrivacyProvider>
    </MemoryRouter>
  )
}

const heading = name => screen.findByRole('heading', { name })
const key = (k, options = {}) => fireEvent.keyDown(window, { key: k, ...options })
const lastToast = () => toastSpy.mock.calls.at(-1)

beforeEach(() => {
  vi.clearAllMocks()
  snoozed = 0
  snoozedUnsorted = null
  waiting = null
  aiActive = true
  anyTransactions = true
  learnedRules = () => []
  jobCtx.job = null
  jobCtx.startAnnotation = vi.fn()
  mockApi.post.mockResolvedValue({ reevaluated: [] })
  mockApi.patch.mockResolvedValue({ reevaluated: [] })
})

describe('the sort card', () => {
  it('leads with the payee and the note the user typed, and hides the printed line', async () => {
    stubGet([makeCard({ note: 'Dinner with Asha' })])
    await renderQueue()
    expect(screen.getByRole('heading', { name: 'SWIGGY' })).toBeInTheDocument()
    expect(screen.getByText('Dinner with Asha')).toBeInTheDocument()
    expect(screen.getByText(/Your note:/)).toBeInTheDocument()
    expect(screen.queryByText('SWIGGY ORDER 8842')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /As printed on the statement/ }))
    expect(screen.getByText('SWIGGY ORDER 8842')).toBeInTheDocument()
  })

  it('leads with the printed line when no payee could be read from it', async () => {
    stubGet([makeCard({ payee: null, raw_description: 'NEFT-IN/PAYROLL/ACME', note: null })])
    await renderQueue('NEFT-IN/PAYROLL/ACME')
    expect(screen.queryByRole('button', { name: /As printed on the statement/ })).not.toBeInTheDocument()
  })

  it('shows no note line when the payment has none', async () => {
    stubGet([makeCard()])
    await renderQueue()
    expect(screen.queryByText(/Your note:/)).not.toBeInTheDocument()
  })

  it('lists each suggestion with its number and reason, the guess already chosen', async () => {
    stubGet([makeCard({ suggestions: [
      { category: 'Transport', subcategory: null, reason: 'You chose this 8 times for SWIGGY' },
      { category: 'Food', subcategory: null, reason: 'AI guess, 50% sure' },
    ] })])
    await renderQueue()
    expect(screen.getByText('Which is it?')).toBeInTheDocument()
    const first = screen.getByRole('button', { name: /You chose this 8 times for SWIGGY/ })
    expect(first).toHaveTextContent('1')
    expect(first).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: /AI guess, 50% sure/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('has no confidence bar, no category chips and no "Uncategorized" choice', async () => {
    stubGet([makeCard()])
    await renderQueue()
    expect(screen.queryByText('Confidence')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Transport' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Uncategorized' })).not.toBeInTheDocument()
  })

  it('says there is no suggestion when the backend has none', async () => {
    stubGet([bare(100, 'CHAI WALA')])
    await renderQueue('CHAI WALA')
    expect(screen.getByText('No suggestion for this one. Pick a category.')).toBeInTheDocument()
    expect(screen.queryByText('pending')).not.toBeInTheDocument()
  })
})

describe('ReviewQueue keyboard shortcuts', () => {
  it('confirms a clean card on Enter (no edits) via the confirm endpoint', async () => {
    stubGet([makeCard()])
    await renderQueue()

    expect(screen.getByRole('button', { name: /Confirm/ })).toBeInTheDocument()
    key('Enter')

    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
    expect(mockApi.patch).not.toHaveBeenCalled()
  })

  it('no longer confirms on "c", and a letter does not open the category filter', async () => {
    stubGet([makeCard()])
    await renderQueue()
    key('c')
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalled()
    expect(screen.getByRole('combobox', { name: 'Another category' })).not.toHaveFocus()
  })

  it('1, 2 and 3 choose a suggestion, and Enter then saves that one', async () => {
    stubGet([makeCard({ suggestions: [
      { category: 'Food', subcategory: null, reason: 'AI guess, 50% sure' },
      { category: 'Transport', subcategory: null, reason: 'You chose this once for SWIGGY' },
      { category: 'Food', subcategory: 'Groceries', reason: 'Similar to BIGBASKET, which you sorted this way' },
    ] })])
    await renderQueue()

    key('2')
    expect(screen.getByRole('button', { name: /You chose this once/ })).toHaveAttribute('aria-pressed', 'true')
    expect(mockApi.post).not.toHaveBeenCalled() // choosing is not answering

    key('Enter')
    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/bulk-label', {
      transaction_ids: [100],
      label: { category: 'Transport', subcategory: null, tags: [] },
    })
    // A correction is not a confirm: the stale confirm endpoint is never used.
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/10/confirm', expect.anything())
  })

  it('choosing the guess again and pressing Enter is still a confirm', async () => {
    stubGet([makeCard({ suggestions: [
      { category: 'Transport', subcategory: null, reason: 'You chose this once for SWIGGY' },
      { category: 'Food', subcategory: null, reason: 'AI guess, 50% sure' },
    ] })])
    await renderQueue()
    key('1')
    key('2')
    key('Enter')
    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
  })

  it('ignores a number with no suggestion behind it', async () => {
    stubGet([makeCard()])
    await renderQueue()
    key('3')
    expect(screen.getByRole('button', { name: /AI guess, 50% sure/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('"/" moves the keyboard to the category filter without typing a slash', async () => {
    stubGet([makeCard()])
    await renderQueue()
    key('/')
    const box = screen.getByRole('combobox', { name: 'Another category' })
    expect(box).toHaveFocus()
    expect(box).toHaveValue('')
  })

  it('after "/" every letter is text: a name starting with S, Z or A skips, undoes and unticks nothing', async () => {
    const user = userEvent.setup()
    const member = (id, extra = {}) => makeCard({
      annotation_id: id, transaction_id: 100 + id, group_key: 'OBEROI|debit', group_size: 2, payee: 'OBEROI', ...extra,
    })
    stubGet([member(10), member(11)])
    await renderQueue('OBEROI')
    await user.keyboard('/saz')
    expect(screen.getByRole('combobox', { name: 'Another category' })).toHaveValue('saz')
    expect(screen.getByRole('checkbox')).toBeChecked() // "a" did not untick the group
    expect(mockApi.post).not.toHaveBeenCalled() // "s" did not skip, "z" did not undo
    expect(screen.getByRole('heading', { name: 'OBEROI' })).toBeInTheDocument()
  })

  it('shortcuts do not fire with Shift held', async () => {
    const member = (id, extra = {}) => makeCard({
      annotation_id: id, transaction_id: 100 + id, group_key: 'OBEROI|debit', group_size: 2, payee: 'OBEROI', ...extra,
    })
    mockApi.post.mockResolvedValue({ reevaluated: [], undo_token: 'tok-1' })
    stubGet([member(10), member(11), uber()])
    await renderQueue('OBEROI')
    key('S', { shiftKey: true })
    key('Z', { shiftKey: true })
    key('A', { shiftKey: true })
    key('1', { shiftKey: true })
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalled()
    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('"/" still opens the filter on a layout where it needs Shift', async () => {
    stubGet([makeCard()])
    await renderQueue()
    key('/', { shiftKey: true })
    expect(screen.getByRole('combobox', { name: 'Another category' })).toHaveFocus()
  })

  it('finds any category by typing, picks it with arrows and Enter, then Enter answers', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    await renderQueue()

    await user.keyboard('/gro')
    expect(screen.getByRole('option', { name: 'Food > Groceries' })).toBeInTheDocument()
    await user.keyboard('{Enter}')
    // The pick is chosen, not yet answered, and the card has the keyboard again.
    expect(mockApi.post).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Your pick')).toHaveTextContent('Food > Groceries')
    expect(screen.getByRole('group', { name: 'Payment to SWIGGY' })).toHaveFocus()

    await user.keyboard('{Enter}')
    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/bulk-label', {
      transaction_ids: [100],
      label: { category: 'Food', subcategory: 'Groceries', tags: [] },
    })
  })

  it('Escape closes the filter and gives the keyboard back to the card', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    await renderQueue()
    await user.keyboard('/gro{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Payment to SWIGGY' })).toHaveFocus()
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('skips persistently via the snooze endpoint without saving anything', async () => {
    stubGet([makeCard(), uber()])
    await renderQueue()

    key('s')

    await heading('UBER')
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/review-queue/skip', { transaction_id: 100 })
    expect(mockApi.patch).not.toHaveBeenCalled()
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
  })

  it('ignores a held key, so one press cannot skip a whole queue', async () => {
    stubGet([makeCard(), uber()])
    await renderQueue()
    key('s', { repeat: true })
    await act(async () => {})
    expect(screen.getByRole('heading', { name: 'SWIGGY' })).toBeInTheDocument()
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('ignores repeat keys while a confirm is in flight', async () => {
    stubGet([makeCard(), uber()])
    let finish
    mockApi.post.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    await renderQueue()

    // A quick double press, then skip and undo, all before the save returns.
    key('Enter')
    key('Enter')
    key('s')
    key('z')
    await act(async () => {}) // the answer is sent once the skip writes before it have settled
    await act(async () => finish({ reevaluated: [] }))

    // One request, and the queue moved exactly one card: nothing skipped unseen.
    await heading('UBER')
    expect(mockApi.post.mock.calls).toEqual([['/annotations/10/confirm', { category: 'Food' }]])
  })

  it('does not react to shortcuts while typing in an input', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    await renderQueue()

    // Open the tags field and type into it; "s" and "z" here are text, not shortcuts.
    await user.click(screen.getByText(/Edit tags/))
    const tags = screen.getByPlaceholderText(/type and press enter/i)
    await user.click(tags)
    await user.keyboard('sz1')

    expect(mockApi.post).not.toHaveBeenCalled()
    expect(tags).toHaveValue('sz1')
  })

  it('has no merchant field: naming a payee is Rename, in the Ledger', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    await renderQueue()
    expect(screen.queryByText(/merchant/i)).not.toBeInTheDocument()
    await user.click(screen.getByText(/Edit tags/))
    expect(screen.queryByLabelText('Merchant')).not.toBeInTheDocument()
  })

  it('a tags edit alone makes the card a save, not a confirm, and sends no merchant', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    await renderQueue()
    await user.click(screen.getByText(/Edit tags/))
    await user.type(screen.getByPlaceholderText(/type and press enter/i), 'lunch{Enter}')
    await user.click(screen.getByRole('group', { name: 'Payment to SWIGGY' }))
    key('Enter')
    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/bulk-label', {
      transaction_ids: [100],
      label: { category: 'Food', subcategory: null, tags: ['lunch'] },
    })
  })
})

describe('answering and undoing', () => {
  it('says what was sorted, in plain words, with an Undo that takes it back', async () => {
    mockApi.post.mockResolvedValue({ reevaluated: [], undo_token: 'tok-1' })
    stubGet(n => (n === 0 ? [makeCard()] : n === 1 ? [] : [makeCard()]))
    await renderQueue()

    key('Enter')
    await screen.findByText("You're all done!")
    const [message, type, options] = lastToast()
    expect(message).toBe('Sorted as Food')
    expect(type).toBe('success')
    expect(options.action.label).toBe('Undo')

    mockApi.post.mockResolvedValue({ restored: [100] })
    await act(async () => options.action.onClick())
    expect(mockApi.post).toHaveBeenLastCalledWith(UNDO, { token: 'tok-1' })
    expect(await heading('SWIGGY')).toBeInTheDocument()
    expect(screen.getByText('0 / 1')).toBeInTheDocument()
  })

  it('Z undoes the last answer and the card comes back', async () => {
    mockApi.post.mockResolvedValue({ reevaluated: [], undo_token: 'tok-1' })
    // The first load, then the reload after the undo.
    stubGet(() => [makeCard(), uber()])
    await renderQueue()

    key('Enter')
    await heading('UBER')
    mockApi.post.mockResolvedValue({ restored: [100] })
    key('z')

    expect(await heading('SWIGGY')).toBeInTheDocument()
    expect(mockApi.post).toHaveBeenLastCalledWith(UNDO, { token: 'tok-1' })
    // The progress forgets the undone answer.
    expect(screen.getByText('0 / 2')).toBeInTheDocument()
    expect(screen.getByText(/0 confirmed/)).toBeInTheDocument()
  })

  it('undoes a whole payee group in one go', async () => {
    const member = (id, extra = {}) => makeCard({
      annotation_id: id, transaction_id: 100 + id, group_key: 'OBEROI|debit', group_size: 2, payee: 'OBEROI', ...extra,
    })
    mockApi.post.mockResolvedValue({ updated: 2, reevaluated: [], undo_token: 'tok-g' })
    stubGet(n => (n === 0 ? [member(10), member(11)] : n === 1 ? [] : [member(10), member(11)]))
    await renderQueue('OBEROI')

    key('Enter')
    await screen.findByText("You're all done!")
    expect(lastToast()[0]).toBe('Sorted as Food, and 1 more from OBEROI')
    mockApi.post.mockResolvedValue({ restored: [110, 111] })
    key('z')
    expect(await heading('OBEROI')).toBeInTheDocument()
    expect(screen.getByText('0 / 2')).toBeInTheDocument()
  })

  it('undoes a skip, and the card returns to the front', async () => {
    mockApi.post.mockResolvedValue({ snoozed: true, undo_token: 'tok-s' })
    stubGet(n => (n === 0 ? [makeCard(), uber()] : [makeCard(), uber()]))
    await renderQueue()

    key('s')
    await heading('UBER')
    await act(async () => {})
    expect(lastToast()[0]).toBe('Set aside for later')
    expect(lastToast()[2].action.label).toBe('Undo')

    mockApi.post.mockResolvedValue({ restored: [100] })
    key('z')
    expect(await heading('SWIGGY')).toBeInTheDocument()
    expect(mockApi.post).toHaveBeenLastCalledWith(UNDO, { token: 'tok-s' })
  })

  it('Z straight after a skip waits for the skip to be written, then undoes it', async () => {
    let finishSkip
    mockApi.post.mockImplementation(path => (
      path === '/annotations/review-queue/skip'
        ? new Promise(resolve => { finishSkip = () => resolve({ snoozed: true, undo_token: 'tok-s' }) })
        : Promise.resolve({ restored: [100] })
    ))
    stubGet(() => [makeCard(), uber()])
    await renderQueue()

    key('s')
    await heading('UBER')
    key('z')
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalledWith(UNDO, expect.anything()) // the skip is not written yet
    await act(async () => finishSkip())
    expect(mockApi.post).toHaveBeenCalledWith(UNDO, { token: 'tok-s' })
  })

  it('says so and does not bring the card back when the server refuses the undo', async () => {
    const { ApiError } = await import('../lib/api.js')
    mockApi.post.mockResolvedValue({ reevaluated: [], undo_token: 'tok-1' })
    stubGet(n => (n === 0 ? [makeCard(), uber()] : [uber()]))
    await renderQueue()
    key('Enter')
    await heading('UBER')

    mockApi.post.mockRejectedValue(Object.assign(new ApiError('That answer can no longer be undone.'), { status: 409 }))
    key('z')
    await act(async () => {})
    expect(lastToast().slice(0, 2)).toEqual(['That answer can no longer be undone.', 'error'])
    expect(screen.getByRole('heading', { name: 'UBER' })).toBeInTheDocument()

    // The answer is final now: a second Z has nothing to undo.
    mockApi.post.mockClear()
    key('z')
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('an answer straight after a skip waits for the skip to be written', async () => {
    let finishSkip
    mockApi.post.mockImplementation(path => (
      path === '/annotations/review-queue/skip'
        ? new Promise(resolve => { finishSkip = () => resolve({ snoozed: true, undo_token: 'tok-s' }) })
        : Promise.resolve({ reevaluated: [], undo_token: 'tok-a' })
    ))
    stubGet(() => [makeCard(), uber(), makeCard({ transaction_id: 102, annotation_id: 12, payee: 'LAST' })])
    await renderQueue()

    key('s')
    await heading('UBER')
    key('Enter')
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/11/confirm', expect.anything()) // the skip is not written yet
    await act(async () => finishSkip())
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith('/annotations/11/confirm', { category: 'Food' }))
  })

  it('does not say Undone when the list could not be reloaded afterwards', async () => {
    mockApi.post.mockResolvedValue({ reevaluated: [], undo_token: 'tok-1' })
    let loads = 0
    stubGet(() => { loads += 1; return [makeCard(), uber()] })
    await renderQueue()
    key('Enter')
    await heading('UBER')

    mockApi.post.mockResolvedValue({ restored: [100] })
    const base = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => (
      path === '/annotations/review-queue' ? Promise.reject(new Error('offline')) : base(path)
    ))
    key('z')
    await waitFor(() => expect(mockApi.post).toHaveBeenLastCalledWith(UNDO, { token: 'tok-1' }))
    await act(async () => {})
    expect(loads).toBeGreaterThan(0)
    expect(toastSpy).not.toHaveBeenCalledWith('Undone', expect.anything())
    expect(lastToast()[0]).toBe("Undone. Couldn't reload the list. Finance Copilot isn't answering.")
    expect(lastToast()[1]).toBe('error')
  })

  it('Z with nothing to undo does nothing', async () => {
    stubGet([makeCard()])
    await renderQueue()
    key('z')
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('offers Undo on the finish screen too', async () => {
    mockApi.post.mockResolvedValue({ reevaluated: [], undo_token: 'tok-1' })
    stubGet(n => (n === 0 ? [makeCard()] : n === 1 ? [] : [makeCard()]))
    await renderQueue()
    key('Enter')
    await screen.findByText("You're all done!")

    mockApi.post.mockResolvedValue({ restored: [100] })
    fireEvent.click(screen.getByRole('button', { name: /Undo/ }))
    expect(await heading('SWIGGY')).toBeInTheDocument()
  })

  it('adds how many other payments the server sorted as a result', async () => {
    mockApi.post.mockResolvedValue({ undo_token: 'tok-1', reevaluated: [
      { transaction_id: 101, category: 'Food', subcategory: null, merchant: null, confidence: 0.95, source: 'learned_rule', resolved: true },
      { transaction_id: 102, category: 'Food', subcategory: null, merchant: null, confidence: 0.95, source: 'learned_rule', resolved: true },
    ] })
    stubGet([makeCard(), uber(), uber({ annotation_id: 12, transaction_id: 102, payee: 'ZOMATO', raw_description: 'ZOMATO' }), makeCard({ transaction_id: 103, annotation_id: 13, payee: 'LAST' })])
    await renderQueue()
    key('Enter')
    await heading('LAST')
    expect(lastToast()[0]).toBe('Sorted as Food. 2 similar payments were sorted too.')
  })
})

describe('undoing a correction and the similar payments it fixed', () => {
  async function correctAndApply() {
    const user = userEvent.setup()
    stubGet(() => [makeCard(), uber()])
    const base = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => (
      path === '/annotations/10/similar'
        ? Promise.resolve([{
          transaction_id: 200, payee: 'SWIGGY BLR', txn_date: '2026-01-02', amount: 90, debit_credit: 'debit',
          category: 'Food', differs: true, similarity: 0.9,
        }])
        : base(path)
    ))
    mockApi.post.mockImplementation(path => Promise.resolve(
      path.endsWith('/apply-to-similar') ? { applied: 1, skipped: 0, undo_token: 'tok-sim' }
        : path === '/annotations/bulk-label' ? { updated: 1, reevaluated: [], undo_token: 'tok-ans' }
          : { restored: [] }
    ))
    await renderQueue()
    key('1')
    await user.keyboard('/trans')
    await user.keyboard('{Enter}')
    key('Enter')
    fireEvent.click(await screen.findByRole('button', { name: /Apply to 1 payment/ }))
    await heading('UBER')
    await act(async () => {})
  }

  it('Z takes back the similar payments first, then the answer, as one step', async () => {
    await correctAndApply()
    expect(lastToast()[2].action.label).toBe('Undo')
    mockApi.post.mockClear()
    key('z')
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledTimes(2))
    expect(mockApi.post.mock.calls).toEqual([[UNDO, { token: 'tok-sim' }], [UNDO, { token: 'tok-ans' }]])
    await waitFor(() => expect(lastToast().slice(0, 2)).toEqual(['Undone', 'info']))
    // One step: a second Z has nothing left.
    mockApi.post.mockClear()
    key('z')
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('says plainly when only part of it could be undone', async () => {
    const { ApiError } = await import('../lib/api.js')
    await correctAndApply()
    mockApi.post.mockClear()
    mockApi.post.mockImplementation((path, body) => (
      body?.token === 'tok-ans'
        ? Promise.reject(Object.assign(new ApiError("Those payments have changed since, so this answer can't be undone."), { status: 409 }))
        : Promise.resolve({ restored: [100] })
    ))
    key('z')
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledTimes(2))
    await act(async () => {})
    const [message, type] = lastToast()
    expect(type).toBe('error')
    expect(message).toMatch(/Only part of that was undone/)
    expect(message).toMatch(/changed since/)
    expect(toastSpy).not.toHaveBeenCalledWith('Undone', expect.anything())
  })
})

describe('ReviewQueue teaching propagation', () => {
  it('drops siblings the server resolved when a card is confirmed', async () => {
    stubGet([
      makeCard(),
      makeCard({ annotation_id: 11, transaction_id: 101, raw_description: 'SWIGGY ORDER 9911', payee: 'SIBLING' }),
      makeCard({ annotation_id: 12, transaction_id: 102, raw_description: 'UBER TRIP 77', payee: 'UBER' }),
    ])
    mockApi.post.mockResolvedValue({ reevaluated: [
      { transaction_id: 101, category: 'Food', subcategory: null, merchant: null, confidence: 0.95,
        source: 'learned_rule', resolved: true },
      { transaction_id: 102, category: 'Transport', subcategory: 'Cab & Auto', merchant: null, confidence: 0.6,
        source: 'rag_direct', resolved: false },
    ] })
    await renderQueue()

    key('Enter')

    // The resolved sibling is skipped over; the changed one shows its new label.
    expect(await heading('UBER')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'SIBLING' })).not.toBeInTheDocument()
    // Enter now vouches for the label the server stored.
    mockApi.post.mockClear()
    key('Enter')
    await act(async () => {})
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/12/confirm', { category: 'Transport' })
  })

  it('reloads instead of confirming a label that changed under the card', async () => {
    const { ApiError } = await import('../lib/api.js')
    stubGet([makeCard()])
    mockApi.post.mockRejectedValue(Object.assign(new ApiError('now Transport'), { status: 409 }))
    await renderQueue()
    const fetches = mockApi.get.mock.calls.filter(([p]) => p === '/annotations/review-queue').length

    key('Enter')
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
      makeCard({ annotation_id: 12, transaction_id: 112, raw_description: 'UBER TRIP 77', payee: 'UBER', group_key: 'UBER|debit', group_size: 1 }),
    ])
    mockApi.post.mockResolvedValue({ updated: 2, reevaluated: [] })
    await renderQueue('OBEROIFC tucksh')
    expect(screen.getByLabelText(/Do the same for all 2 payments to/)).toBeChecked()

    key('Enter')

    expect(await heading('UBER')).toBeInTheDocument()
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/bulk-label', {
      transaction_ids: [110, 111], label: { category: 'Food', subcategory: null },
    })
  })

  it('is ticked for a business and unticked for a person', async () => {
    stubGet([group(10, 'A', { payee_kind: 'person' }), group(11, 'B', { payee_kind: 'person' })])
    await renderQueue('OBEROIFC tucksh')
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('a payee that could be either is ticked, as it was before', async () => {
    stubGet([group(10, 'A', { payee_kind: 'unknown' }), group(11, 'B', { payee_kind: 'unknown' })])
    await renderQueue('OBEROIFC tucksh')
    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('answers one payment to a person by itself unless the box is ticked', async () => {
    stubGet([group(10, 'A', { payee_kind: 'person' }), group(11, 'B', { payee_kind: 'person' })])
    await renderQueue('OBEROIFC tucksh')
    key('Enter')
    await act(async () => {})
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
  })

  it('names the payee, not this payment\'s own name, when a group is confirmed', async () => {
    stubGet([
      group(10, 'SWIGGY ORDER 8842', { payee: 'March rent', payee_all: 'Rent' }),
      group(11, 'OBEROIFC SECOND 1', { payee: 'Rent', payee_all: 'Rent' }),
    ])
    mockApi.post.mockResolvedValue({ updated: 2, reevaluated: [] })
    await renderQueue('March rent')

    key('Enter')

    await waitFor(() => expect(toastSpy).toHaveBeenCalledWith(expect.stringContaining('and 1 more from Rent'), 'success', undefined))
  })

  it('"a" turns the group off so only this card is confirmed', async () => {
    stubGet([group(10, 'SWIGGY ORDER 8842'), group(11, 'OBEROIFC SECOND 1')])
    await renderQueue('OBEROIFC tucksh')

    key('a')
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    key('Enter')

    await act(async () => {})
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
  })

  it('keeps the keyboard after the group checkbox is clicked', async () => {
    stubGet([group(10, 'SWIGGY ORDER 8842'), group(11, 'OBEROIFC SECOND 1')])
    await renderQueue('OBEROIFC tucksh')

    const box = screen.getByRole('checkbox')
    fireEvent.click(box)
    fireEvent.keyDown(box, { key: 'Enter' })

    await act(async () => {})
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

  it('does not claim the policy sorted anything when the list could not be reloaded', async () => {
    await offerPolicy()
    const base = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => (
      path === '/annotations/review-queue' ? Promise.reject(new Error('offline')) : base(path)
    ))
    fireEvent.click(await screen.findByRole('button', { name: 'Yes, always' }))
    await act(async () => {})
    await act(async () => {})
    expect(lastToast()[1]).toBe('error')
    expect(lastToast()[0]).toBe("Policy saved. Couldn't reload the list. Finance Copilot isn't answering.")
  })

  it('keeps the suggested limit readable while amounts are hidden', async () => {
    // A policy limit is a rule setting, not a money figure.
    localStorage.setItem('fc_privacy_hidden', '1')
    await offerPolicy()
    expect(await screen.findByText(/Sort small payments \(up to ₹500\)/)).toBeInTheDocument()
  })

  async function offerPolicy() {
    const person = (id, payee) => makeCard({
      annotation_id: id, transaction_id: 100 + id, raw_description: payee, payee, amount: 40,
      category: 'Transport', subcategory: 'Cab & Auto', payee_kind: 'person',
      group_key: `P${id}|debit`, group_size: 1,
    })
    stubGet([person(10, 'ONE'), person(11, 'TWO'), person(12, 'THREE'), person(13, 'FOUR')])
    mockApi.post.mockImplementation(path => Promise.resolve(
      path === '/policies' ? { resolved: 1 } : path === '/policies/preview' ? { matches: 2 } : { reevaluated: [] }
    ))
    await renderQueue('ONE')

    for (const next of ['TWO', 'THREE', 'FOUR']) {
      key('Enter')
      await heading(next)
    }
  }
})

describe('ReviewQueue custom categories', () => {
  it('creates a new category from the filter, chooses it, and answers the card with it', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    mockApi.post.mockImplementation(path => {
      if (path === '/categories') return Promise.resolve({ id: 99, name: 'Pet Care', parent_id: null })
      return Promise.resolve({ reevaluated: [] })
    })
    await renderQueue()

    await user.keyboard('/Pet Care')
    await user.click(screen.getByRole('option', { name: 'Create category "Pet Care"' }))

    expect(mockApi.post).toHaveBeenCalledWith('/categories', { name: 'Pet Care' })
    expect(await screen.findByLabelText('Your pick')).toHaveTextContent('Pet Care')

    key('Enter')
    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/bulk-label', {
      transaction_ids: [100],
      label: { category: 'Pet Care', subcategory: null, tags: [] },
    })
  })

  it('creates a subcategory under the chosen category', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    mockApi.post.mockImplementation((path, body) => {
      if (path === '/categories/subcategories') {
        return Promise.resolve({ id: 100, name: body.name, parent_id: body.parent_id })
      }
      return Promise.resolve({ reevaluated: [] })
    })
    await renderQueue()

    await user.keyboard('/Street Food')
    await user.click(screen.getByRole('option', { name: 'Create "Street Food" in Food' }))

    expect(mockApi.post).toHaveBeenCalledWith('/categories/subcategories', { parent_id: 1, name: 'Street Food' })
    expect(await screen.findByLabelText('Your pick')).toHaveTextContent('Food > Street Food')
  })

  it('keeps the typed name when the server rejects it', async () => {
    const user = userEvent.setup()
    stubGet([makeCard()])
    mockApi.post.mockRejectedValue(Object.assign(new Error("'Pets' already exists"), { status: 422 }))
    await renderQueue()

    await user.keyboard('/Pets{Enter}')

    expect(await screen.findByRole('combobox', { name: 'Another category' })).toHaveValue('Pets')
    expect(lastToast().slice(0, 2)).toEqual(['Couldn\'t add "Pets". \'Pets\' already exists', 'error'])
  })
})

describe('ReviewQueue unsorted payments', () => {
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
    expect(await heading('SWIGGY')).toBeInTheDocument()
  })

  it('says the work is done only when the server agrees', async () => {
    // The last local card is answered, but the server has one more waiting.
    stubGet(n => (n === 0 ? [makeCard()] : n === 1 ? [uber()] : []))
    await renderQueue()
    key('Enter')
    expect(await heading('UBER')).toBeInTheDocument()
    expect(screen.queryByText("You're all done!")).not.toBeInTheDocument()
  })

  it('shows a line about payments that have not been through automatic sorting, and starts it', async () => {
    stubGet([bare(100, 'SWIGGY'), bare(101, 'UBER')])
    jobCtx.startAnnotation.mockResolvedValue({ ai_active: false, rule_matched: 0, low_confidence: 2 })
    await renderQueue()
    expect(screen.getByText(
      '2 payments have no guess yet. Pick a category for each below, or run automatic sorting.'
    )).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Sort payments/ }))
    await act(async () => {})
    expect(jobCtx.startAnnotation).toHaveBeenCalledWith({})
  })

  it('says sorting is running while a job holds the slot', async () => {
    jobCtx.job = { status: 'running', kind: 'annotate' }
    stubGet([bare(100, 'SWIGGY')])
    await renderQueue()
    expect(screen.getByText(/Automatic sorting is running/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Sort payments/ })).not.toBeInTheDocument()
  })

  it('shows no line when every queued payment has a guess', async () => {
    stubGet([makeCard()])
    await renderQueue()
    expect(screen.queryByText(/no guess yet/)).not.toBeInTheDocument()
  })

  it('uses the singular for one payment', async () => {
    stubGet([bare(100, 'SWIGGY')])
    await renderQueue()
    expect(screen.getByText(
      '1 payment has no guess yet. Pick a category for it below, or run automatic sorting.'
    )).toBeInTheDocument()
  })

  it('offers no run when no model is configured', async () => {
    aiActive = false
    stubGet([bare(100, 'SWIGGY'), bare(101, 'UBER')])
    await renderQueue()
    expect(screen.getByText('2 payments have no guess yet. Pick a category for each below.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Sort payments/ })).not.toBeInTheDocument()
  })

  it('stops offering the run once a run has left the payments bare', async () => {
    jobCtx.startAnnotation.mockResolvedValue({ ai_active: true, llm_failed: 2 })
    stubGet(() => [bare(100, 'SWIGGY'), bare(101, 'UBER')])
    await renderQueue()
    fireEvent.click(screen.getByRole('button', { name: /Sort payments/ }))
    await act(async () => {})
    expect(await screen.findByText('2 payments have no guess yet. Pick a category for each below.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Sort payments/ })).not.toBeInTheDocument()
  })

  it('does not offer Confirm on a payment with no guess; Enter does nothing until a category is chosen', async () => {
    const user = userEvent.setup()
    stubGet([bare(100, 'SWIGGY', { suggestions: [{ category: 'Transport', subcategory: null, reason: 'You chose this once for SWIGGY' }] })])
    await renderQueue()
    expect(screen.queryByRole('button', { name: /Confirm/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pick a category' })).toBeInTheDocument()

    key('Enter')
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalled()
    expect(mockApi.patch).not.toHaveBeenCalled()

    // Choosing a suggestion, then Enter, answers it by hand as a plain manual annotation.
    await user.keyboard('1')
    expect(screen.getByRole('button', { name: /Confirm/ })).toBeInTheDocument()
    await user.keyboard('{Enter}')
    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/bulk-label', {
      transaction_ids: [100],
      label: { category: 'Transport', subcategory: null, tags: [] },
    })
    // No similar lookup for a payment with no annotation.
    expect(mockApi.get.mock.calls.some(([p]) => p.endsWith('/similar'))).toBe(false)
  })

  it('does not offer Confirm on an Uncategorized guess either', async () => {
    stubGet([makeCard({ category: 'Uncategorized', confidence: 0.97, suggestions: [] })])
    await renderQueue()
    expect(screen.queryByRole('button', { name: /Confirm/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pick a category' })).toBeInTheDocument()
    key('Enter')
    await act(async () => {})
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('answers a payee group that mixes guessed and unannotated payments by transaction', async () => {
    const user = userEvent.setup()
    const g = { group_key: 'OBEROI|debit', group_size: 2, payee: 'OBEROI' }
    stubGet([bare(100, 'OBEROI', g), makeCard({ annotation_id: 11, transaction_id: 101, raw_description: 'OBEROI TWO', category: 'Uncategorized', suggestions: [], ...g })])
    await renderQueue('OBEROI')
    await user.keyboard('/trans{Enter}{Enter}')
    await screen.findByText("You're all done!")
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/bulk-label', {
      transaction_ids: [100, 101],
      label: { category: 'Transport', subcategory: null, tags: [] },
    })
  })

  it('skips an unannotated payment by transaction', async () => {
    stubGet([bare(100, 'SWIGGY'), bare(101, 'UBER')])
    await renderQueue()
    key('s')
    await heading('UBER')
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/review-queue/skip', { transaction_id: 100 })
  })

  it('leaves no phantom set-aside item after skip, undo, confirm', async () => {
    // AUDIT B2: the count comes from the server, which forgets the snooze once the card is answered.
    mockApi.post.mockImplementation(path => {
      if (path === '/annotations/review-queue/skip') { snoozed = 1; return Promise.resolve({ undo_token: 't1' }) }
      if (path === UNDO) { snoozed = 0; return Promise.resolve({ restored: [100] }) }
      snoozed = 0
      return Promise.resolve({ reevaluated: [] })
    })
    // Loads: the first, the reload after the undo, then the check once the cards run out.
    stubGet(n => (n >= 2 ? [] : [makeCard(), uber()]))
    await renderQueue()

    key('s')
    await heading('UBER')
    key('z')
    await heading('SWIGGY')
    key('Enter')
    await heading('UBER')
    key('Enter')

    expect(await screen.findByText("You're all done!")).toBeInTheDocument()
    expect(screen.queryByText(/set aside/)).not.toBeInTheDocument()
    expect(screen.getByText('2 confirmed · 0 corrected · 0 skipped')).toBeInTheDocument()
  })
})

describe('ReviewQueue finish', () => {
  it('says how many were sorted, how many payees now sort themselves, and links back to the Dashboard', async () => {
    learnedRules = n => (n === 0
      ? [{ counterparty_key: 'UBER', direction: 'debit' }]
      : [{ counterparty_key: 'UBER', direction: 'debit' }, { counterparty_key: 'SWIGGY', direction: 'debit' }])
    stubGet([makeCard()])
    await renderQueue()
    key('Enter')

    expect(await screen.findByText("You're all done!")).toBeInTheDocument()
    expect(screen.getByText('You sorted 1 payment.')).toBeInTheDocument()
    expect(await screen.findByText('1 payee will now be sorted on its own from here.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the Dashboard' })).toHaveAttribute('href', '/dashboard')
  })

  it('counts only the rules this session created, with the plural', async () => {
    learnedRules = n => (n === 0
      ? []
      : ['A', 'B', 'C'].map(k => ({ counterparty_key: k, direction: 'debit' })))
    stubGet([makeCard(), uber()])
    await renderQueue()
    key('Enter')
    await heading('UBER')
    key('Enter')
    expect(await screen.findByText('3 payees will now be sorted on their own from here.')).toBeInTheDocument()
    expect(screen.getByText('You sorted 2 payments.')).toBeInTheDocument()
  })

  it('says nothing about new payees when no rule was created', async () => {
    learnedRules = () => [{ counterparty_key: 'UBER', direction: 'debit' }]
    stubGet([makeCard()])
    await renderQueue()
    key('Enter')
    await screen.findByText("You're all done!")
    await act(async () => {})
    expect(screen.queryByText(/on their own|on its own/)).not.toBeInTheDocument()
  })

  it('says nothing about new payees when the rules cannot be read', async () => {
    stubGet([makeCard()])
    const original = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => (path === '/annotations/learned-rules' ? Promise.reject(new Error('down')) : original(path)))
    await renderQueue()
    key('Enter')
    await screen.findByText("You're all done!")
    expect(screen.queryByText(/on their own|on its own/)).not.toBeInTheDocument()
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

    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load your review list.")
    expect(screen.queryByText(/all done/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Everything is sorted/)).not.toBeInTheDocument()

    failing = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await heading('SWIGGY')).toBeInTheDocument()
  })

  it('does not keep asking the server while it is failing', async () => {
    stubGet([makeCard()])
    const routed = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => (
      path === '/annotations/review-queue' ? Promise.reject(new Error('Network down')) : routed(path)
    ))
    renderPage()
    await screen.findByRole('alert')
    await act(async () => {})
    expect(mockApi.get.mock.calls.filter(([p]) => p === '/annotations/review-queue')).toHaveLength(1)
  })
})

describe('ReviewQueue side loads and dialogs', () => {
  it('says so, with Retry, when the categories or settings could not load, and keeps the card', async () => {
    stubGet([makeCard()])
    const routed = mockApi.get.getMockImplementation()
    let failing = true
    mockApi.get.mockImplementation(path => (
      failing && (path === '/config' || path === '/policies') ? Promise.reject(new TypeError('Failed to fetch')) : routed(path)
    ))
    renderPage()
    await heading('SWIGGY')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Couldn't load your settings and your policies.")
    failing = false
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'SWIGGY' })).toBeInTheDocument()
  })

  async function openPropagateDialog() {
    const user = userEvent.setup()
    stubGet(() => [makeCard(), uber()])
    const base = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => (
      path === '/annotations/10/similar'
        ? Promise.resolve([{
          transaction_id: 200, payee: 'SWIGGY BLR', txn_date: '2026-01-02', amount: 90, debit_credit: 'debit',
          category: 'Food', differs: true, similarity: 0.9,
        }])
        : base(path)
    ))
    mockApi.post.mockImplementation(path => Promise.resolve(
      path.endsWith('/apply-to-similar') ? { applied: 1, skipped: 0, undo_token: 'tok-sim' }
        : path === '/annotations/bulk-label' ? { updated: 1, reevaluated: [], undo_token: 'tok-ans' }
          : { restored: [] }
    ))
    await renderQueue()
    key('1')
    await user.keyboard('/trans')
    await user.keyboard('{Enter}')
    key('Enter')
    return screen.findByRole('dialog', { name: /Apply "Transport" to similar/ })
  }

  it('Escape dismisses the similar-payments dialog without applying', async () => {
    const dialog = await openPropagateDialog()
    fireEvent.keyDown(dialog, { key: 'Escape' })
    await heading('UBER')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(mockApi.post.mock.calls.some(([p]) => p.endsWith('/apply-to-similar'))).toBe(false)
  })

  it('Enter applies to the ticked payments', async () => {
    const dialog = await openPropagateDialog()
    expect(dialog).toHaveFocus()
    fireEvent.keyDown(dialog, { key: 'Enter' })
    await waitFor(() => expect(mockApi.post.mock.calls.some(([p]) => p.endsWith('/apply-to-similar'))).toBe(true))
  })
})

describe('ReviewQueue keyboard focus', () => {
  it('Enter on the next card saves nothing after a payment with no guess was answered', async () => {
    const user = userEvent.setup()
    const pick = { category: 'Transport', subcategory: null, reason: 'You chose this once for X' }
    stubGet([bare(100, 'SWIGGY', { suggestions: [pick] }), bare(101, 'UBER')])
    await renderQueue()

    await user.keyboard('1{Enter}')
    await heading('UBER')
    expect(mockApi.post).toHaveBeenCalledTimes(1) // the save of the first card
    mockApi.post.mockClear()

    // The next card has no pick. Enter must neither choose a category nor save.
    await user.keyboard('{Enter}')
    await act(async () => {})
    expect(screen.getByRole('button', { name: 'Pick a category' })).toBeInTheDocument()
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('a keyboard activation of a suggestion keeps focus on it and saves nothing', async () => {
    const user = userEvent.setup()
    stubGet([makeCard({ suggestions: [
      { category: 'Food', subcategory: null, reason: 'AI guess, 50% sure' },
      { category: 'Transport', subcategory: null, reason: 'You chose this once for SWIGGY' },
    ] })])
    await renderQueue()

    const choice = screen.getByRole('button', { name: /You chose this once/ })
    choice.focus()
    await user.keyboard('{Enter}') // the button's own activation: chooses it, saves nothing
    expect(mockApi.post).not.toHaveBeenCalled()
    expect(choice).toHaveFocus()
    expect(choice).toHaveAttribute('aria-pressed', 'true')
  })

  it('a mouse pick on a suggestion hands the keyboard back to the card', async () => {
    const user = userEvent.setup()
    stubGet([makeCard({ suggestions: [
      { category: 'Food', subcategory: null, reason: 'AI guess, 50% sure' },
      { category: 'Transport', subcategory: null, reason: 'You chose this once for SWIGGY' },
    ] })])
    await renderQueue()
    await user.click(screen.getByRole('button', { name: /You chose this once/ }))
    expect(screen.getByRole('group', { name: 'Payment to SWIGGY' })).toHaveFocus()
    await user.keyboard('{Enter}')
    await act(async () => {})
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/bulk-label', expect.anything())
  })

  it('moves focus to the labelled card when the card changes', async () => {
    stubGet([makeCard(), uber()])
    await renderQueue()
    expect(screen.getByRole('group', { name: 'Payment to SWIGGY' })).toHaveFocus()

    key('s')
    const second = await screen.findByRole('group', { name: 'Payment to UBER' })
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
  it('lists the keys, and "confirm" only when confirming is possible', async () => {
    stubGet([bare(100, 'SWIGGY')])
    await renderQueue()
    const legend = screen.getByText(/Keyboard:/).textContent
    expect(legend).not.toMatch(/confirm/)
    expect(legend).toMatch(/skip/)
    expect(legend).toMatch(/undo/)
    expect(legend).toMatch(/another category/)
    expect(legend).not.toMatch(/\bc\b|\bback\b/)
  })

  it('lists the number keys for the suggestions there are, and Enter for a guess that can be confirmed', async () => {
    stubGet([makeCard()])
    await renderQueue()
    const legend = screen.getByText(/Keyboard:/)
    expect(within(legend).getByText('1')).toBeInTheDocument()
    expect(within(legend).queryByText('2')).not.toBeInTheDocument()
    expect(legend.textContent).toMatch(/↵\s*confirm/)
  })

  it('counts a hand label on a payment with no guess as sorted by hand, not corrected', async () => {
    const user = userEvent.setup()
    stubGet([
      bare(100, 'SWIGGY', { suggestions: [{ category: 'Transport', subcategory: null, reason: 'You chose this once for SWIGGY' }] }),
      uber(),
    ])
    await renderQueue()
    await user.keyboard('1{Enter}')
    await heading('UBER')
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
    stubGet(n => (n === 0 ? [makeCard(), uber()] : n === 1 ? [] : [makeCard()]))
    mockApi.post.mockImplementation(path => {
      if (path === '/annotations/review-queue/skip') snoozed = 2
      if (path === '/annotations/review-queue/unsnooze') { snoozed = 0; return Promise.resolve({ restored: 2 }) }
      return Promise.resolve({ reevaluated: [] })
    })
    await renderQueue()
    key('s')
    await heading('UBER')
    key('s')
    await screen.findByText('2 payments are set aside')
    expect(screen.getByText('0 confirmed · 0 corrected · 2 skipped')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Review them again' }))
    await heading('SWIGGY')
    expect(screen.getByText(/0 confirmed · 0 corrected · 0 skipped/)).toBeInTheDocument()
  })

  it('says how much of a long queue is showing', async () => {
    waiting = 230
    stubGet([makeCard()])
    await renderQueue()
    expect(screen.getByText(/showing the first 1 of 230 waiting/)).toBeInTheDocument()
  })

  it('shows progress as answered over everything there is', async () => {
    stubGet([makeCard(), uber(), makeCard({ transaction_id: 102, annotation_id: 12, payee: 'ZOMATO' })])
    await renderQueue()
    expect(screen.getByText('0 / 3')).toBeInTheDocument()
    key('Enter')
    await heading('UBER')
    expect(screen.getByText('1 / 3')).toBeInTheDocument()
  })

  it('reloads the queue when a sorting run finishes, wherever it was started', async () => {
    jobCtx.job = { status: 'running', kind: 'annotate' }
    stubGet(n => (n === 0 ? [makeCard()] : [uber()]))
    const { rerender } = render(
      <MemoryRouter><PrivacyProvider><ToastProvider><ReviewQueue /></ToastProvider></PrivacyProvider></MemoryRouter>
    )
    await heading('SWIGGY')
    await act(async () => {})

    jobCtx.job = null
    rerender(
      <MemoryRouter><PrivacyProvider><ToastProvider><ReviewQueue /></ToastProvider></PrivacyProvider></MemoryRouter>
    )
    expect(await heading('UBER')).toBeInTheDocument()
  })
})

describe('the keys Teach Me lists', () => {
  const sugg = [
    { category: 'Food', subcategory: null, reason: 'one' },
    { category: 'Transport', subcategory: null, reason: 'two' },
    { category: 'Food', subcategory: 'Groceries', reason: 'three' },
  ]
  const member = (id, extra = {}) => makeCard({
    annotation_id: id, transaction_id: 100 + id, group_key: 'OBEROI|debit', group_size: 2, payee: 'OBEROI',
    suggestions: sugg, ...extra,
  })

  it.each(TEACH_ME_KEYS.choose.map((k, i) => [k, i]))('"%s" chooses suggestion %i', async (k, i) => {
    stubGet([member(10), member(11)])
    await renderQueue('OBEROI')
    key(k)
    const buttons = screen.getAllByRole('button', { pressed: true })
    expect(buttons).toHaveLength(1)
    expect(buttons[0]).toHaveTextContent(sugg[i].reason)
  })

  it('"a" toggles the group, "/" opens the category filter', async () => {
    stubGet([member(10), member(11)])
    await renderQueue('OBEROI')
    expect(screen.getByRole('checkbox')).toBeChecked()
    key(TEACH_ME_KEYS.group)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    key(TEACH_ME_KEYS.another)
    expect(screen.getByRole('combobox', { name: 'Another category' })).toHaveFocus()
  })

  it('Enter confirms, "s" skips, "z" undoes', async () => {
    stubGet(() => [makeCard(), uber(), bare(102, 'OLA')]) // the server keeps serving them: only the keys are under test
    await renderQueue()
    key(TEACH_ME_KEYS.confirm)
    await heading('UBER')
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/10/confirm', { category: 'Food' })
    mockApi.post.mockResolvedValue({ snoozed: true, undo_token: 'tok-s' })
    key(TEACH_ME_KEYS.skip)
    await heading('OLA')
    expect(mockApi.post).toHaveBeenCalledWith('/annotations/review-queue/skip', { transaction_id: 101 })
    mockApi.post.mockResolvedValue({ restored: [101] })
    key(TEACH_ME_KEYS.undo)
    await heading('UBER')
    expect(mockApi.post).toHaveBeenLastCalledWith(UNDO, { token: 'tok-s' })
  })
})

describe('an open dialog on Teach Me', () => {
  it('blocks the page keys, and Escape still closes it once focus has left', async () => {
    stubGet([makeCard(), uber()])
    await renderQueue()
    key('?')
    const sheet = await screen.findByRole('dialog', { name: 'Keyboard shortcuts' })
    key('s')
    key('z')
    fireEvent.keyDown(document.body, { key: 's' })
    expect(mockApi.post).not.toHaveBeenCalled()
    expect(screen.getByRole('heading', { name: 'SWIGGY' })).toBeInTheDocument()
    // The sheet, not the card, holds the keyboard.
    expect(sheet).toHaveFocus()
    // Focus wandering out of the sheet does not stop Escape.
    document.body.focus()
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
