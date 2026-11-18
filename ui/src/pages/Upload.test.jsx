import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { HashRouter } from '../test/router.jsx'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
}))
const { pollJob } = vi.hoisted(() => ({ pollJob: vi.fn() }))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {}, pollAnnotationJob: pollJob }))
const { toast } = vi.hoisted(() => ({ toast: vi.fn() }))
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => toast,
}))
const { jobCtx } = vi.hoisted(() => ({ jobCtx: { job: null, startAnnotation: null, watchJobs: null } }))
vi.mock('../contexts/AnnotationJobContext.jsx', () => ({
  useAnnotationJob: () => jobCtx,
}))

const { demoCtx } = vi.hoisted(() => ({ demoCtx: { sample: false, removable: false, mixed: false, hosted: false, refresh: vi.fn() } }))
vi.mock('../contexts/DemoDataContext.jsx', () => ({
  useDemoData: () => demoCtx,
}))

// What GET /statements and GET /accounts answer, so a test sets the ledger and not the URLs.
const { ledger } = vi.hoisted(() => ({ ledger: { statements: [], accounts: [] } }))
const serve = () => {
  ledger.statements = []
  ledger.accounts = []
  mockApi.get.mockImplementation(path => Promise.resolve(path === '/accounts' ? ledger.accounts : ledger.statements))
}

const reloadAppStatements = vi.fn()
vi.mock('../contexts/StatementContext.jsx', () => ({
  useStatement: () => ({ reload: reloadAppStatements }),
}))
const { privacy } = vi.hoisted(() => ({ privacy: { hidden: false } }))
vi.mock('../contexts/PrivacyContext.jsx', () => ({
  usePrivacy: () => privacy,
}))

const stmt = {
  id: 's1', bank_name: 'kotak', account_id: 'a1', account_label: 'Kotak savings',
  statement_month: '2026-07', balance_check: null,
  txn_count: 10, sorted_count: 2, uploaded_at: '2026-07-01T10:00:00',
}

const uploaded = (id, extra = {}) => ({
  id, bank_name: 'kotak', account_id: 'a1', account_label: 'Kotak savings', account_is_new: false,
  statement_month: '2026-07', warnings: [],
  transaction_count: 8, balance_check: { checked: 7, consistent: 7, balanced: true },
  sorting_job_id: `job-${id}`, ...extra,
})
// 8 rows: 5 by rules, 2 from history, 1 left undecided (no model).
const SORTED = {
  total_processed: 8, rule_matched: 5, rag_direct_annotated: 2, llm_annotated: 0,
  llm_failed: 1, low_confidence: 0, already_annotated: 0, ai_active: false, llm_error: null,
}

// What GET /statements says once sorting has ended: the one definition of sorted.
const ledgerRows = (...rows) => rows.map(([id, total, sorted]) => ({ ...stmt, id, txn_count: total, sorted_count: sorted }))

import Upload from './Upload.jsx'

const renderUpload = () => render(<HashRouter><Upload /></HashRouter>)

// "Add statement" for one file, "Add 2 statements" for two.
const ADD_BUTTON = /^Add (\d+ statements|statement)$/

const pdf = () => new File(['%PDF-1.4'], 'statement.pdf', { type: 'application/pdf' })

describe('Upload', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    jobCtx.job = null
    jobCtx.startAnnotation = vi.fn()
    jobCtx.watchJobs = vi.fn()
    pollJob.mockResolvedValue(SORTED)
    Object.assign(demoCtx, { sample: false, removable: false, mixed: false, hosted: false })
    serve()
    mockApi.upload.mockResolvedValue(uploaded('s1'))
  })

  it('clears the file input after a pick, so the same file can be picked again', async () => {
    ledger.statements = ledgerRows(['s1', 8, 7])
    const { container } = renderUpload()
    const input = container.querySelector('input[type=file]')
    // jsdom does not model a file input's path value, so record what the page
    // writes to it. A browser only fires "change" when that value differs, so
    // a stale path would swallow a re-pick of the same file after upload,
    // delete or Cancel - which is exactly what users hit.
    const writes = []
    Object.defineProperty(input, 'value', {
      configurable: true,
      get: () => '',
      set: v => { writes.push(v) },
    })

    fireEvent.change(input, { target: { files: [pdf()] } })
    expect(screen.getByText('statement.pdf')).toBeInTheDocument()
    expect(writes).toEqual([''])

    fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
    await waitFor(() => expect(mockApi.upload).toHaveBeenCalledTimes(1))
    await screen.findByText('7 sorted, 1 needs you')

    fireEvent.change(input, { target: { files: [pdf()] } })
    expect(writes).toEqual(['', ''])
    expect(screen.getByRole('button', { name: ADD_BUTTON })).toBeInTheDocument()
  })

  it('shows a statement\'s warnings on its own row', async () => {
    mockApi.upload.mockResolvedValueOnce(uploaded('s1', { warnings: ['2 rows had no date and were skipped.'] }))
    const { container } = renderUpload()
    fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
    fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
    const row = (await screen.findByText('statement.pdf')).closest('li')
    expect(await within(row).findByText('2 rows had no date and were skipped.')).toBeInTheDocument()
  })

  it("Sort again runs through the job flow and shows that run's result", async () => {
    ledger.statements = [stmt]
    jobCtx.startAnnotation.mockResolvedValue({
      total_processed: 8, rule_matched: 5, rag_direct_annotated: 1, llm_annotated: 2,
      llm_failed: 0, low_confidence: 0, already_annotated: 2, ai_active: true, llm_error: null,
    })
    renderUpload()
    fireEvent.click(await screen.findByRole('button', { name: /sort again/i }))
    await waitFor(() => expect(jobCtx.startAnnotation).toHaveBeenCalledWith({ statement_id: 's1' }))
    expect(await screen.findByText('Figured out by AI: 2')).toBeInTheDocument()
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/auto-annotate', expect.anything())
  })

  it('counts a statement as sorted by what Teach Me is not waiting on', async () => {
    ledger.statements = [{ ...stmt, sorted_count: 6 }]
    renderUpload()
    expect(await screen.findByText(/Partial · 6\/10/)).toBeInTheDocument()
  })

  it('every Sort again waits while another run is tracked', async () => {
    ledger.statements = [stmt]
    jobCtx.job = { status: 'running', kind: 'reembed' }
    renderUpload()
    expect(await screen.findByRole('button', { name: /sort again/i })).toBeDisabled()
  })

  it('shows the provider error in the results panel', async () => {
    ledger.statements = [stmt]
    jobCtx.startAnnotation.mockResolvedValue({
      total_processed: 8, rule_matched: 5, llm_annotated: 0, llm_failed: 3,
      ai_active: true, llm_error: 'provider refused the request (HTTP 404)',
    })
    renderUpload()
    fireEvent.click(await screen.findByRole('button', { name: /sort again/i }))
    expect(await screen.findByText(/Your AI model stopped answering, so this run stopped using it/))
      .toBeInTheDocument()
    // The provider's own words are for Test connection, not for this panel.
    expect(screen.queryByText(/HTTP 404/)).not.toBeInTheDocument()
  })

  // The page lives under a HashRouter, so a plain href="/settings" opens the
  // dashboard; the links must go through the router and name the tab.
  it('links to the AI & Model tab of Settings through the router', async () => {
    ledger.statements = [stmt]
    jobCtx.startAnnotation.mockResolvedValue({
      total_processed: 8, rule_matched: 5, llm_annotated: 0, llm_failed: 3,
      ai_active: true, llm_error: 'provider refused the request (HTTP 404)',
    })
    renderUpload()
    fireEvent.click(await screen.findByRole('button', { name: /sort again/i }))
    const link = await screen.findByRole('link', { name: /Settings › AI & Model/ })
    expect(link.getAttribute('href')).toBe('#/settings?tab=ai')
  })

  it('links to the AI & Model tab when AI is off', async () => {
    ledger.statements = [stmt]
    jobCtx.startAnnotation.mockResolvedValue({
      total_processed: 8, rule_matched: 5, llm_annotated: 0, llm_failed: 3,
      ai_active: false, llm_error: null,
    })
    renderUpload()
    fireEvent.click(await screen.findByRole('button', { name: /sort again/i }))
    const link = await screen.findByRole('link', { name: /Settings › AI & Model/ })
    expect(link.getAttribute('href')).toBe('#/settings?tab=ai')
  })

  it('tells you to remove sample data before adding a statement, and points at the bar', async () => {
    Object.assign(demoCtx, { sample: true, removable: true })
    const { container } = renderUpload()
    expect(await screen.findByText(/still has sample data/)).toBeInTheDocument()
    // One remove action per screen: the bar's. The notice only explains.
    expect(screen.queryByRole('button', { name: 'Remove sample data' })).not.toBeInTheDocument()
    // The dropzone is disabled for assistive tech, and its words are not dimmed
    // (a faded line fails contrast): only the frame goes quiet.
    const zone = screen.getByText(/Drop your bank statements here/).closest('[aria-disabled]')
    expect(zone).toHaveAttribute('aria-disabled', 'true')
    expect(zone.className).not.toMatch(/opacity-/)
    expect(zone.querySelector('p').className).not.toMatch(/opacity-/)
    const picker = container.querySelector('input[type="file"]')
    expect(picker).toBeDisabled()
    const open = vi.spyOn(picker, 'click')
    fireEvent.click(zone)
    expect(open).not.toHaveBeenCalled()
  })

  it('shows no sample-data notice on a ledger without it', async () => {
    renderUpload()
    await waitFor(() => expect(mockApi.get).toHaveBeenCalled())
    expect(screen.queryByText(/still has sample data/)).not.toBeInTheDocument()
  })

  it('on the hosted demo does not tell you to remove sample data, and leaves the dropzone to the API', async () => {
    Object.assign(demoCtx, { sample: true, removable: false, hosted: true })
    const { container } = renderUpload()
    await waitFor(() => expect(mockApi.get).toHaveBeenCalled())
    expect(screen.queryByText(/still has sample data/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove sample data' })).not.toBeInTheDocument()
    expect(container.querySelector('.pointer-events-none')).toBeNull()
  })

  it('refreshes the sample-data status after deleting a statement', async () => {
    ledger.statements = [stmt]
    mockApi.delete.mockResolvedValue({})
    renderUpload()
    await screen.findByText('Kotak savings')
    fireEvent.click(screen.getByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(mockApi.delete).toHaveBeenCalledWith('/statements/s1'))
    await waitFor(() => expect(demoCtx.refresh).toHaveBeenCalledTimes(1))
  })

  it('refreshes the sample-data status after resetting a statement', async () => {
    ledger.statements = [stmt]
    mockApi.delete.mockResolvedValue({})
    renderUpload()
    await screen.findByText('Kotak savings')
    fireEvent.click(screen.getByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Start over' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start over' }))
    await waitFor(() => expect(mockApi.delete).toHaveBeenCalledWith('/statements/s1/data'))
    await waitFor(() => expect(demoCtx.refresh).toHaveBeenCalledTimes(1))
  })

  describe('naming an account from the menu', () => {
    const name = async () => {
      ledger.statements = [stmt]
      renderUpload()
      await screen.findByText('Kotak savings')
      fireEvent.click(screen.getByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Name this account' }))
      return screen.getByRole('textbox', { name: 'Name this account' })
    }

    it('names the account, not the statement, and returns focus to the More button after a save', async () => {
      mockApi.patch.mockResolvedValue({})
      const input = await name()
      expect(input).toHaveFocus()
      expect(input).toHaveValue('Kotak savings')
      fireEvent.change(input, { target: { value: 'HDFC salary' } })
      fireEvent.keyDown(input, { key: 'Enter' })
      await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith('/accounts/a1', { name: 'HDFC salary' }))
      await waitFor(() => expect(screen.getByRole('button', { name: 'More for Kotak savings - Jul 2026' })).toHaveFocus())
      // The app-wide statement list (the Transactions picker) is told.
      expect(reloadAppStatements).toHaveBeenCalled()
    })

    it('returns focus to the More button after Escape, and saves nothing', async () => {
      const input = await name()
      fireEvent.keyDown(input, { key: 'Escape' })
      expect(screen.getByRole('button', { name: 'More for Kotak savings - Jul 2026' })).toHaveFocus()
      expect(mockApi.patch).not.toHaveBeenCalled()
    })

    it('keeps its Enter to itself, and ignores a held one', async () => {
      mockApi.patch.mockResolvedValue({})
      const input = await name()
      fireEvent.change(input, { target: { value: 'HDFC salary' } })
      expect(fireEvent.keyDown(input, { key: 'Enter', repeat: true })).toBe(false)
      expect(fireEvent.keyDown(input, { key: 'Enter', isComposing: true })).toBe(false)
      expect(mockApi.patch).not.toHaveBeenCalled()
      expect(fireEvent.keyDown(input, { key: 'Enter' })).toBe(false)
      await waitFor(() => expect(mockApi.patch).toHaveBeenCalledTimes(1))
    })

    it('saving the words already shown changes nothing, so a derived label stays derived', async () => {
      const input = await name()
      fireEvent.keyDown(input, { key: 'Enter' })
      await waitFor(() => expect(screen.getByRole('button', { name: 'More for Kotak savings - Jul 2026' })).toHaveFocus())
      expect(mockApi.patch).not.toHaveBeenCalled()
    })

    it('clears the name when it is emptied', async () => {
      mockApi.patch.mockResolvedValue({})
      const input = await name()
      fireEvent.change(input, { target: { value: '   ' } })
      fireEvent.keyDown(input, { key: 'Enter' })
      await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith('/accounts/a1', { name: '   ' }))
    })

    it('keeps the input open and says why when the name is refused', async () => {
      mockApi.patch.mockRejectedValue(Object.assign(new Error('An account name can be at most 60 characters.'), { status: 422 }))
      const input = await name()
      fireEvent.change(input, { target: { value: 'x' } })
      fireEvent.keyDown(input, { key: 'Enter' })
      await waitFor(() => expect(mockApi.patch).toHaveBeenCalled())
      expect(screen.getByRole('textbox', { name: 'Name this account' })).toBeInTheDocument()
    })
  })

  describe('the accounts', () => {
    const kotak = {
      id: 'a1', account_label: 'Kotak savings', name: 'Kotak savings', bank_name: 'kotak', account_ref_last4: '8074',
      statement_count: 2, first_date: '2026-06-01', last_date: '2026-07-31', balance: 123456, balance_as_of: '2026-07-31',
    }
    const hdfc = {
      id: 'a2', account_label: 'HDFC ··5678', name: null, bank_name: 'hdfc', account_ref_last4: '5678',
      statement_count: 1, first_date: '2026-07-01', last_date: '2026-07-31', balance: null, balance_as_of: null,
    }

    it('lists each account with its statements and its latest balance and date', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [kotak, hdfc]
      renderUpload()
      const list = (await screen.findByText('Your accounts')).closest('div')
      expect(within(list).getByText('Kotak savings')).toBeInTheDocument()
      expect(within(list).getByText('2 statements')).toBeInTheDocument()
      expect(within(list).getByText('₹1,23,456')).toBeInTheDocument()
      expect(within(list).getByText(/as of 31 Jul 2026/)).toBeInTheDocument()
      // An account with no balance on record shows none.
      expect(within(list).getByText('HDFC ··5678')).toBeInTheDocument()
      expect(within(list).getByText('1 statement')).toBeInTheDocument()
      expect(within(list).getAllByText(/as of/)).toHaveLength(1)
    })

    it('hides the balance in privacy mode', async () => {
      privacy.hidden = true
      try {
        ledger.statements = [stmt]
        ledger.accounts = [kotak]
        renderUpload()
        const list = (await screen.findByText('Your accounts')).closest('div')
        // The figure is masked in place and read out as hidden.
        expect(list.querySelector('[data-privacy-mask]')).toHaveTextContent('₹1,23,456')
        expect(within(list).getByText('Amount hidden')).toBeInTheDocument()
      } finally {
        privacy.hidden = false
      }
    })

    it('shows no accounts list before there is an account', async () => {
      renderUpload()
      await screen.findByText(/No statements yet/)
      expect(screen.queryByText('Your accounts')).not.toBeInTheDocument()
    })

    it('offers Move to account only when there is another account or something to split out', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ ...kotak, statement_count: 1 }]
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      expect(screen.queryByRole('menuitem', { name: /Move to account/ })).not.toBeInTheDocument()
    })

    it('moves a statement to another account from its menu', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [kotak, hdfc]
      mockApi.patch.mockResolvedValue({})
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      expect(screen.getAllByRole('menuitem').map(i => i.textContent))
        .toEqual(['Name this account', 'Move to account…', 'Start over', 'Delete'])
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))

      const dialog = screen.getByRole('dialog', { name: 'Move to account' })
      // Only the other accounts are offered.
      expect(within(dialog).queryByRole('button', { name: 'Kotak savings' })).not.toBeInTheDocument()
      fireEvent.click(within(dialog).getByRole('button', { name: 'HDFC ··5678' }))
      await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith('/statements/s1', { account_id: 'a2' }))
      await waitFor(() => expect(reloadAppStatements).toHaveBeenCalled())
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('offers a new account as a target, with an optional name, when the account holds other statements', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ ...kotak, statement_count: 2 }]
      mockApi.patch.mockResolvedValue({ account_id: 'a9', account_label: 'Joint account' })
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      // No other account, but a new one can be split out.
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      const dialog = screen.getByRole('dialog', { name: 'Move to account' })
      expect(within(dialog).getByText('A new account')).toBeInTheDocument()
      fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name for the new account' }), { target: { value: 'Joint account' } })
      fireEvent.click(within(dialog).getByRole('button', { name: 'Move to a new account' }))
      await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith('/statements/s1', { new_account: true, name: 'Joint account' }))
      await waitFor(() => expect(reloadAppStatements).toHaveBeenCalled())
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    const openSplit = async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ ...kotak, statement_count: 2 }, hdfc]
      mockApi.patch.mockResolvedValue({ account_id: 'a9', account_label: 'Joint' })
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      return screen.getByRole('textbox', { name: 'Name for the new account' })
    }

    it('moves to a new account with Enter in the name field, and that Enter does nothing else', async () => {
      const input = await openSplit()
      fireEvent.change(input, { target: { value: 'Joint' } })
      // The Enter is the field's alone: its default action must not reach the
      // "More for" button the dialog hands focus back to.
      expect(fireEvent.keyDown(input, { key: 'Enter' })).toBe(false)
      await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith('/statements/s1', { new_account: true, name: 'Joint' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'More for Kotak savings - Jul 2026' })).toHaveFocus()
    })

    it('does not move on a held Enter or one that confirms an input method', async () => {
      const input = await openSplit()
      fireEvent.change(input, { target: { value: 'Joint' } })
      expect(fireEvent.keyDown(input, { key: 'Enter', repeat: true })).toBe(false)
      expect(fireEvent.keyDown(input, { key: 'Enter', isComposing: true })).toBe(false)
      expect(mockApi.patch).not.toHaveBeenCalled()
    })

    it('needs a name for a new account: the button waits, Enter does nothing, and a hint says why', async () => {
      const input = await openSplit()
      const dialog = screen.getByRole('dialog', { name: 'Move to account' })
      const move = within(dialog).getByRole('button', { name: 'Move to a new account' })
      expect(move).toBeDisabled()
      expect(input).toBeRequired()
      expect(within(dialog).getByText('Give it a name, so you can tell the two accounts apart.')).toBeInTheDocument()
      fireEvent.keyDown(input, { key: 'Enter' })
      fireEvent.change(input, { target: { value: '   ' } })
      expect(move).toBeDisabled()
      fireEvent.keyDown(input, { key: 'Enter' })
      fireEvent.click(move)
      expect(mockApi.patch).not.toHaveBeenCalled()
      expect(screen.getByRole('dialog', { name: 'Move to account' })).toBeInTheDocument()
      fireEvent.change(input, { target: { value: 'Joint' } })
      expect(move).toBeEnabled()
    })

    it('offers no new account for the only statement of an account', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ ...kotak, statement_count: 1 }, hdfc]
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      expect(screen.queryByText('A new account')).not.toBeInTheDocument()
    })

    it('closes the move dialog with Escape and moves nothing', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [kotak, hdfc]
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(mockApi.patch).not.toHaveBeenCalled()
    })
  })

  describe('overlapping statements', () => {
    const drop = async () => {
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      return (await screen.findByText('statement.pdf')).closest('li')
    }

    it('says how many payments were new and how many the ledger already had', async () => {
      mockApi.upload.mockResolvedValue(uploaded('s1', {
        statement_month: '2026-10', period_start: '2026-10-05', period_end: '2026-11-15',
        printed_start: '2026-10-05', printed_end: '2026-11-15',
        rows_in_file: 42, rows_added: 18, rows_already_in: 24, transaction_count: 18,
      }))
      const row = await drop()
      await waitFor(() => expect(row).toHaveTextContent('5 Oct to 15 Nov - 18 new payments, 24 already in your ledger - Balance checks out'))
    })

    it('uses the singular for one new payment', async () => {
      mockApi.upload.mockResolvedValue(uploaded('s1', { rows_in_file: 9, rows_added: 1, rows_already_in: 8, transaction_count: 1 }))
      const row = await drop()
      await waitFor(() => expect(row).toHaveTextContent('1 new payment, 8 already in your ledger'))
    })

    it('says only what was added when nothing was already in', async () => {
      mockApi.upload.mockResolvedValue(uploaded('s1', { rows_in_file: 8, rows_added: 8, rows_already_in: 0 }))
      const row = await drop()
      await waitFor(() => expect(row).toHaveTextContent('8 payments added'))
      expect(row).not.toHaveTextContent('already in your ledger')
    })

    it('shows the refusal of a file with nothing new as the server words it', async () => {
      mockApi.upload.mockRejectedValue(Object.assign(
        new Error('Nothing new: all 42 payments in this file are already in Kotak savings.'), { status: 409 },
      ))
      const row = await drop()
      await waitFor(() => expect(row).toHaveTextContent('Nothing new: all 42 payments in this file are already in Kotak savings.'))
      expect(row).not.toHaveTextContent("Couldn't read this file")
    })

    it('lists a statement by the days it covers when that is not one calendar month', async () => {
      ledger.statements = [
        { ...stmt, id: 's1', statement_month: '2026-10', period_start: '2026-10-05', period_end: '2026-11-15', printed_start: '2026-10-05', printed_end: '2026-11-15' },
        { ...stmt, id: 's2', statement_month: '2026-09', period_start: '2026-09-02', period_end: '2026-09-28', printed_start: '2026-09-01', printed_end: '2026-09-30' },
      ]
      renderUpload()
      const rows = await screen.findAllByRole('row')
      expect(within(rows[1]).getByText('5 Oct to 15 Nov')).toBeInTheDocument()
      expect(within(rows[2]).getByText('Sep 2026')).toBeInTheDocument()
    })

    it('shows when a statement was added in local time, though the ledger stores UTC', async () => {
      ledger.statements = [{ ...stmt, uploaded_at: '2026-07-01 10:00:00' }]
      renderUpload()
      const local = new Date(Date.UTC(2026, 6, 1, 10, 0, 0))
      const pad = n => String(n).padStart(2, '0')
      expect(await screen.findByText(`01 Jul 2026 ${pad(local.getHours())}:${pad(local.getMinutes())}`)).toBeInTheDocument()
    })

    it('says in the delete confirmation that payments another statement skipped go for good, only when it applies', async () => {
      ledger.statements = [{ ...stmt, shared_with_others: 3 }]
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }))
      expect(screen.getByRole('dialog')).toHaveTextContent(
        '3 of its payments are also in another statement, which did not add them again, so deleting this removes them from your ledger.',
      )
    })

    it('says it in the singular for one payment', async () => {
      ledger.statements = [{ ...stmt, shared_with_others: 1 }]
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }))
      expect(screen.getByRole('dialog')).toHaveTextContent(
        '1 of its payments is also in another statement, which did not add it again, so deleting this removes it from your ledger.',
      )
    })

    it('has no such sentence when no other statement shares its payments', async () => {
      ledger.statements = [{ ...stmt, shared_with_others: 0 }]
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }))
      expect(screen.getByRole('dialog')).not.toHaveTextContent('another statement')
    })

    it('says in the move message how many payments the account already had', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ id: 'a1', account_label: 'Kotak savings', statement_count: 1 }, { id: 'a2', account_label: 'HDFC ··5678', statement_count: 1 }]
      mockApi.patch.mockResolvedValue({ account_id: 'a2', account_label: 'HDFC ··5678', rows_already_there: 4, statement_deleted: false })
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'HDFC ··5678' }))
      await waitFor(() => expect(toast).toHaveBeenCalledWith('Moved Jul 2026 to HDFC ··5678. 4 payments already there, so left out.', 'info'))
    })

    it('says the work kept on the payments left out, when there was some', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ id: 'a1', account_label: 'Kotak savings', statement_count: 1 }, { id: 'a2', account_label: 'HDFC ··5678', statement_count: 1 }]
      mockApi.patch.mockResolvedValue({ account_id: 'a2', account_label: 'HDFC ··5678', rows_already_there: 2, work_carried_over: 2, statement_deleted: false })
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'HDFC ··5678' }))
      await waitFor(() => expect(toast).toHaveBeenCalledWith(
        'Moved Jul 2026 to HDFC ··5678. 2 payments already there, so left out; your labels and notes on them were kept.', 'info',
      ))
    })

    it('counts the payments the work was on when it was on only some of them', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ id: 'a1', account_label: 'Kotak savings', statement_count: 1 }, { id: 'a2', account_label: 'HDFC ··5678', statement_count: 1 }]
      mockApi.patch.mockResolvedValue({ account_id: 'a2', account_label: 'HDFC ··5678', rows_already_there: 3, work_carried_over: 1, statement_deleted: false })
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'HDFC ··5678' }))
      await waitFor(() => expect(toast).toHaveBeenCalledWith(
        'Moved Jul 2026 to HDFC ··5678. 3 payments already there, so left out; your labels and notes on 1 of them were kept.', 'info',
      ))
    })

    it('says when the account kept its own label or note instead, in the singular and the plural', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ id: 'a1', account_label: 'Kotak savings', statement_count: 1 }, { id: 'a2', account_label: 'HDFC ··5678', statement_count: 1 }]
      mockApi.patch.mockResolvedValue({ account_id: 'a2', account_label: 'HDFC ··5678', rows_already_there: 3, work_carried_over: 0, work_not_carried: 1, statement_deleted: false })
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'HDFC ··5678' }))
      await waitFor(() => expect(toast).toHaveBeenCalledWith(
        "Moved Jul 2026 to HDFC ··5678. 3 payments already there, so left out. 1 payment had a different label or note in this account, and the account's own was kept.", 'info',
      ))
    })

    it('says the same beside a removed statement, and has no such sentence at zero', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ id: 'a1', account_label: 'Kotak savings', statement_count: 1 }, { id: 'a2', account_label: 'HDFC ··5678', statement_count: 1 }]
      mockApi.patch.mockResolvedValue({ account_id: 'a2', account_label: 'HDFC ··5678', rows_already_there: 8, work_not_carried: 2, statement_deleted: true })
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'HDFC ··5678' }))
      await waitFor(() => expect(toast).toHaveBeenCalledWith(
        "Jul 2026 was not moved: all 8 payments in it were already in HDFC ··5678, so it was removed. 2 payments had a different label or note in this account, and the account's own was kept.", 'info',
      ))
    })

    it('says which payments stay in the account the statement left', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ id: 'a1', account_label: 'Kotak savings', statement_count: 1 }, { id: 'a2', account_label: 'HDFC ··5678', statement_count: 1 }]
      mockApi.patch.mockResolvedValue({
        account_id: 'a2', account_label: 'HDFC ··5678', rows_already_there: 0, work_not_carried: 0,
        payments_left_behind: 5, left_behind_account_label: 'Kotak savings', statement_deleted: false,
      })
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'HDFC ··5678' }))
      await waitFor(() => expect(toast).toHaveBeenCalledWith(
        'Moved Jul 2026 to HDFC ··5678. 5 payments in its dates stay in Kotak savings, where they were already.', 'info',
      ))
    })

    it('says it in the singular for one payment', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ id: 'a1', account_label: 'Kotak savings', statement_count: 1 }, { id: 'a2', account_label: 'HDFC ··5678', statement_count: 1 }]
      mockApi.patch.mockResolvedValue({
        account_id: 'a2', account_label: 'HDFC ··5678', rows_already_there: 0,
        payments_left_behind: 1, left_behind_account_label: 'Kotak savings', statement_deleted: false,
      })
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'HDFC ··5678' }))
      await waitFor(() => expect(toast).toHaveBeenCalledWith(
        'Moved Jul 2026 to HDFC ··5678. 1 payment in its dates stays in Kotak savings, where it was already.', 'info',
      ))
    })

    it('says the one payment in a removed statement in the singular, and what was kept', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ id: 'a1', account_label: 'Kotak savings', statement_count: 1 }, { id: 'a2', account_label: 'HDFC ··5678', statement_count: 1 }]
      mockApi.patch.mockResolvedValue({ account_id: 'a2', account_label: 'HDFC ··5678', rows_already_there: 1, work_carried_over: 1, statement_deleted: true })
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'HDFC ··5678' }))
      await waitFor(() => expect(toast).toHaveBeenCalledWith(
        'Jul 2026 was not moved: the one payment in it was already in HDFC ··5678, so it was removed; your labels and notes on it were kept.', 'info',
      ))
    })

    it('says the statement was removed when everything in it was already there', async () => {
      ledger.statements = [stmt]
      ledger.accounts = [{ id: 'a1', account_label: 'Kotak savings', statement_count: 1 }, { id: 'a2', account_label: 'HDFC ··5678', statement_count: 1 }]
      mockApi.patch.mockResolvedValue({ account_id: 'a2', account_label: 'HDFC ··5678', rows_already_there: 8, statement_deleted: true })
      renderUpload()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'HDFC ··5678' }))
      await waitFor(() => expect(toast).toHaveBeenCalledWith(
        'Jul 2026 was not moved: all 8 payments in it were already in HDFC ··5678, so it was removed.', 'info',
      ))
    })
  })

  describe('where an added statement went', () => {
    const drop = async () => {
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      return (await screen.findByText('statement.pdf')).closest('li')
    }

    it('says it went into an existing account by name', async () => {
      mockApi.upload.mockResolvedValue(uploaded('s1', { account_label: 'Kotak savings', account_is_new: false }))
      const row = await drop()
      expect(await within(row).findByText('Added to Kotak savings')).toBeInTheDocument()
      expect(within(row).queryByRole('button', { name: 'Name it' })).not.toBeInTheDocument()
    })

    it('says where the statement is now once it has been moved, not where it landed', async () => {
      mockApi.upload.mockResolvedValue(uploaded('s1', { account_label: 'Kotak savings', account_is_new: false }))
      ledger.statements = [{ ...stmt, id: 's1' }]
      ledger.accounts = [{ id: 'a1', account_label: 'Kotak savings', statement_count: 1 }, { id: 'a2', account_label: 'HDFC ··5678', statement_count: 1 }]
      mockApi.patch.mockResolvedValue({ account_id: 'a2', account_label: 'HDFC ··5678', rows_already_there: 0, statement_deleted: false })
      const row = await drop()
      expect(await within(row).findByText('Added to Kotak savings')).toBeInTheDocument()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'HDFC ··5678' }))
      expect(await within(row).findByText('Moved to HDFC ··5678')).toBeInTheDocument()
      expect(within(row).queryByText(/Added to Kotak savings/)).not.toBeInTheDocument()
    })

    it('drops the account part when the move removed the statement', async () => {
      mockApi.upload.mockResolvedValue(uploaded('s1', { account_label: 'Kotak savings', account_is_new: false }))
      ledger.statements = [{ ...stmt, id: 's1' }]
      ledger.accounts = [{ id: 'a1', account_label: 'Kotak savings', statement_count: 1 }, { id: 'a2', account_label: 'HDFC ··5678', statement_count: 1 }]
      mockApi.patch.mockResolvedValue({ account_id: 'a2', account_label: 'HDFC ··5678', rows_already_there: 8, statement_deleted: true })
      const row = await drop()
      expect(await within(row).findByText('Added to Kotak savings')).toBeInTheDocument()
      fireEvent.click(await screen.findByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Move to account…' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'HDFC ··5678' }))
      await waitFor(() => expect(within(row).queryByText(/Added to Kotak savings/)).not.toBeInTheDocument())
      expect(within(row).queryByText(/Moved to/)).not.toBeInTheDocument()
    })

    it('says a new account was made, and lets you name it right there with Enter', async () => {
      mockApi.upload.mockResolvedValue(uploaded('s1', { account_id: 'a9', account_label: 'Kotak ··8074', account_is_new: true }))
      mockApi.patch.mockResolvedValue({})
      const row = await drop()
      expect(await within(row).findByText(/Added to a new account, Kotak ··8074\./)).toBeInTheDocument()

      fireEvent.click(within(row).getByRole('button', { name: 'Name it' }))
      const input = within(row).getByRole('textbox', { name: 'Name this account' })
      expect(input).toHaveFocus()
      // The accounts list now knows the new name.
      ledger.accounts = [{ id: 'a9', account_label: 'Kotak savings', statement_count: 1, balance: null }]
      fireEvent.change(input, { target: { value: 'Kotak savings' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith('/accounts/a9', { name: 'Kotak savings' }))
      expect(await within(row).findByText(/Added to a new account, Kotak savings\./)).toBeInTheDocument()
      expect(within(row).queryByRole('textbox')).not.toBeInTheDocument()
      expect(within(row).queryByRole('button', { name: 'Name it' })).not.toBeInTheDocument()
      // Focus lands on the line that now says the name, not on the page.
      expect(within(row).getByText(/Added to a new account, Kotak savings\./)).toHaveFocus()
    })

    it('keeps Name it when a blank name is saved, and still puts focus on the line', async () => {
      mockApi.upload.mockResolvedValue(uploaded('s1', { account_id: 'a9', account_label: 'Kotak ··8074', account_is_new: true }))
      mockApi.patch.mockResolvedValue({})
      const row = await drop()
      fireEvent.click(await within(row).findByRole('button', { name: 'Name it' }))
      const input = within(row).getByRole('textbox', { name: 'Name this account' })
      fireEvent.change(input, { target: { value: '   ' } })
      fireEvent.keyDown(input, { key: 'Enter' })
      await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith('/accounts/a9', { name: '   ' }))
      expect(await within(row).findByRole('button', { name: 'Name it' })).toBeInTheDocument()
      expect(within(row).getByText(/Added to a new account, Kotak ··8074\./)).toHaveFocus()
    })

    it('offers no naming once the account has been named from the statement list', async () => {
      mockApi.upload.mockResolvedValue(uploaded('s1', { account_id: 'a9', account_label: 'Kotak ··8074', account_is_new: true }))
      ledger.accounts = [{ id: 'a9', account_label: 'Salary account', statement_count: 1, balance: null }]
      const row = await drop()
      expect(await within(row).findByText(/Added to a new account, Salary account\./)).toBeInTheDocument()
      expect(within(row).queryByRole('button', { name: 'Name it' })).not.toBeInTheDocument()
    })

    it('leaves the account as it was when naming is cancelled with Escape', async () => {
      mockApi.upload.mockResolvedValue(uploaded('s1', { account_id: 'a9', account_label: 'Kotak ··8074', account_is_new: true }))
      const row = await drop()
      fireEvent.click(await within(row).findByRole('button', { name: 'Name it' }))
      fireEvent.keyDown(within(row).getByRole('textbox', { name: 'Name this account' }), { key: 'Escape' })
      expect(within(row).queryByRole('textbox')).not.toBeInTheDocument()
      expect(within(row).getByRole('button', { name: 'Name it' })).toBeInTheDocument()
      expect(within(row).getByRole('button', { name: 'Name it' })).toHaveFocus()
      expect(mockApi.patch).not.toHaveBeenCalled()
    })

    it('does not take focus when the row is only drawn', async () => {
      mockApi.upload.mockResolvedValue(uploaded('s1', { account_id: 'a9', account_label: 'Kotak ··8074', account_is_new: true }))
      const row = await drop()
      await within(row).findByRole('button', { name: 'Name it' })
      expect(within(row).getByText(/Added to a new account/)).not.toHaveFocus()
      expect(within(row).getByRole('button', { name: 'Name it' })).not.toHaveFocus()
    })
  })

  it('says tags go too before starting a statement over', async () => {
    ledger.statements = [stmt]
    mockApi.delete.mockRejectedValue(Object.assign(new Error('Start over once sorting finishes.'), { status: 409 }))
    renderUpload()
    await screen.findByText('Kotak savings')
    fireEvent.click(screen.getByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Start over' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('every category and tag on "Kotak savings - Jul 2026"')
    fireEvent.click(screen.getByRole('button', { name: 'Start over' }))
    await waitFor(() => expect(mockApi.delete).toHaveBeenCalledWith('/statements/s1/data'))
  })

  it('keeps only Sort again on the row, with naming, start over and delete in a menu, and no memory controls', async () => {
    ledger.statements = [stmt]
    renderUpload()
    await screen.findByText('Kotak savings')
    expect(screen.getByRole('button', { name: /sort again/i })).toBeInTheDocument()
    expect(screen.queryByText(/memory|build|rebuild/i)).not.toBeInTheDocument()
    expect(screen.getAllByRole('columnheader').map(h => h.textContent.trim()))
      .toEqual(['Account', 'Period', 'Balanced', 'Sorted', 'Added', 'Actions'])
    fireEvent.click(screen.getByRole('button', { name: 'More for Kotak savings - Jul 2026' }))
    expect(screen.getAllByRole('menuitem').map(i => i.textContent)).toEqual(['Name this account', 'Start over', 'Delete'])
  })

  describe('before the first upload', () => {
    it('says which banks work, what happens with a locked PDF, where the file stays and what happens next', async () => {
      renderUpload()
      await waitFor(() => expect(mockApi.get).toHaveBeenCalled())
      expect(screen.getByText(/Kotak statements/)).toBeInTheDocument()
      expect(screen.getByText(/running-balance column/)).toBeInTheDocument()
      expect(screen.getByText(/password-protected PDFs work/i)).toBeInTheDocument()
      expect(screen.getByText(
        /Your statement stays on this computer\. If you connect an online AI model in Settings, each payment.s date, amount, description and note are sent to it for sorting, along with a few similar payments you have already sorted and how you sorted them\./,
      )).toBeInTheDocument()
      expect(screen.getByText(/balance is checked, then sorting starts by itself/i)).toBeInTheDocument()
    })

    it('leaves the hosted demo to its own message', async () => {
      Object.assign(demoCtx, { sample: true, hosted: true })
      renderUpload()
      await waitFor(() => expect(mockApi.get).toHaveBeenCalled())
      expect(screen.queryByText(/Kotak statements/)).not.toBeInTheDocument()
    })

    it('gives way to the files once some are chosen', async () => {
      const { container } = renderUpload()
      await screen.findByText(/No statements yet/)
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      expect(screen.queryByText(/Kotak statements/)).not.toBeInTheDocument()
    })
  })

  describe('several files', () => {
    const named = name => new File(['%PDF-1.4'], name, { type: 'application/pdf' })

    it('lets the picker take several files', async () => {
      const { container } = renderUpload()
      await screen.findByText(/No statements yet/)
      expect(container.querySelector('input[type=file]').multiple).toBe(true)
    })

    it('uploads them one after another, each with its own result and a sorted summary', async () => {
      ledger.statements = ledgerRows(['s1', 8, 7], ['s2', 8, 8])
      mockApi.upload
        .mockResolvedValueOnce(uploaded('s1'))
        .mockResolvedValueOnce(uploaded('s2', { account_id: 'a2', account_label: 'HDFC ··5678', account_is_new: true }))
      pollJob
        .mockResolvedValueOnce(SORTED)
        .mockResolvedValueOnce({ ...SORTED, rule_matched: 6, rag_direct_annotated: 2, llm_failed: 0 })
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [named('a.pdf'), named('b.pdf')] } })
      expect(screen.getByText('a.pdf')).toBeInTheDocument()
      expect(screen.getByText('b.pdf')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))

      expect(await screen.findByText('7 sorted, 1 needs you')).toBeInTheDocument()
      expect(await screen.findByText('8 sorted, 0 need you')).toBeInTheDocument()
      expect(mockApi.upload).toHaveBeenCalledTimes(2)
      expect(mockApi.upload.mock.calls[0][1].get('file').name).toBe('a.pdf')
      expect(mockApi.upload.mock.calls[1][1].get('file').name).toBe('b.pdf')
      // Each row reports what went in and the balance check.
      expect(screen.getAllByText(/8 payments added/)).toHaveLength(2)
      expect(screen.getAllByText(/Balance checks out/)).toHaveLength(2)
      // One account of the whole drop, with the way on.
      expect(screen.getByRole('status')).toHaveTextContent('Finished: 15 sorted, 1 needs you')
      expect(screen.getByRole('link', { name: /Teach Me/ }).getAttribute('href')).toBe('#/review')
    })

    it('keeps a drop in progress when more files are picked mid-drop', async () => {
      let finishFirst
      mockApi.upload
        .mockImplementationOnce(() => new Promise(resolve => { finishFirst = () => resolve(uploaded('s1')) }))
        .mockResolvedValueOnce(uploaded('s2'))
      pollJob.mockReturnValue(new Promise(() => {}))
      const { container } = renderUpload()
      const input = container.querySelector('input[type=file]')
      fireEvent.change(input, { target: { files: [named('a.pdf'), named('b.pdf')] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      expect(await screen.findByText('Reading the statement…')).toBeInTheDocument()

      // A third file is picked while a.pdf is being read and b.pdf waits its turn.
      fireEvent.change(input, { target: { files: [named('c.pdf')] } })
      expect(screen.getByText('a.pdf')).toBeInTheDocument()
      expect(screen.getByText('c.pdf')).toBeInTheDocument()

      finishFirst()
      // The drop carries on: b.pdf is still uploaded, and a.pdf shows its sorting.
      await waitFor(() => expect(mockApi.upload).toHaveBeenCalledTimes(2))
      expect(screen.getByText('a.pdf')).toBeInTheDocument()
      expect(screen.getByText('b.pdf')).toBeInTheDocument()
      expect(screen.getAllByText(/Waiting to sort|Getting started/).length).toBeGreaterThan(0)
    })

    it('lets only finished files make way for a new pick', async () => {
      ledger.statements = ledgerRows(['s1', 8, 7])
      const { container } = renderUpload()
      const input = container.querySelector('input[type=file]')
      fireEvent.change(input, { target: { files: [named('a.pdf')] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      await screen.findByText('7 sorted, 1 needs you')
      fireEvent.change(input, { target: { files: [named('b.pdf')] } })
      expect(screen.queryByText('a.pdf')).not.toBeInTheDocument()
      expect(screen.getByText('b.pdf')).toBeInTheDocument()
    })

    it('keeps going when one file fails, says why, and lets that file be retried', async () => {
      ledger.statements = ledgerRows(['s1', 8, 7], ['s2', 8, 7])
      mockApi.upload
        .mockRejectedValueOnce(Object.assign(new Error('This PDF is password-protected. Enter its password and add it again.'), { status: 422 }))
        .mockResolvedValueOnce(uploaded('s2'))
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [named('locked.pdf'), named('open.pdf')] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))

      expect(await screen.findByText(/Couldn't read this file\. This PDF is password-protected/)).toBeInTheDocument()
      expect(await screen.findByText('7 sorted, 1 needs you')).toBeInTheDocument()
      expect(mockApi.upload).toHaveBeenCalledTimes(2)

      // Enter the password and upload again: only the failed file goes up.
      mockApi.upload.mockResolvedValueOnce(uploaded('s1'))
      fireEvent.change(screen.getByPlaceholderText(/Password/), { target: { value: 'hunter2' } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      await waitFor(() => expect(mockApi.upload).toHaveBeenCalledTimes(3))
      const retry = mockApi.upload.mock.calls[2][1]
      expect(retry.get('file').name).toBe('locked.pdf')
      expect(retry.get('password')).toBe('hunter2')
    })

    it('refuses a file that is not a PDF before anything is uploaded', async () => {
      const { container } = renderUpload()
      const txt = new File(['hello'], 'notes.txt', { type: 'text/plain' })
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [txt, named('ok.pdf')] } })

      expect(screen.getByRole('alert')).toHaveTextContent("notes.txt isn't a PDF")
      expect(screen.queryByText('notes.txt', { selector: 'span' })).not.toBeInTheDocument()
      // The PDF next to it is still taken.
      expect(screen.getByText('ok.pdf')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      await waitFor(() => expect(mockApi.upload).toHaveBeenCalledTimes(1))
    })

    it('keeps the refusal on screen while the PDFs go up, until the next pick', async () => {
      const { container } = renderUpload()
      const input = container.querySelector('input[type=file]')
      const txt = new File(['hello'], 'notes.txt', { type: 'text/plain' })
      fireEvent.change(input, { target: { files: [txt, named('ok.pdf')] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      await waitFor(() => expect(mockApi.upload).toHaveBeenCalledTimes(1))
      expect(screen.getByRole('alert')).toHaveTextContent("notes.txt isn't a PDF")

      // The next pick replaces it: with only PDFs there is nothing to refuse.
      fireEvent.change(input, { target: { files: [named('next.pdf')] } })
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('refuses a dropped non-PDF the same way', async () => {
      const { container } = renderUpload()
      await screen.findByText(/No statements yet/)
      const zone = container.querySelector('input[type=file]').parentElement
      const txt = new File(['hello'], 'notes.txt', { type: 'text/plain' })
      fireEvent.drop(zone, { dataTransfer: { files: [txt] } })
      expect(screen.getByRole('alert')).toHaveTextContent("notes.txt isn't a PDF")
      expect(mockApi.upload).not.toHaveBeenCalled()
    })
  })

  describe('sorting starts by itself', () => {
    it('reports what the statements table says, not the job counters', async () => {
      // The job counted 7 of 8 as decided, but by the review bar Teach Me uses
      // only 5 are sorted: the page must agree with the table and the queue.
      ledger.statements = ledgerRows(['s1', 8, 5])
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      expect(await screen.findByText('5 sorted, 3 need you', { selector: 'p' })).toBeInTheDocument()
      expect(screen.getByRole('status')).toHaveTextContent('Finished: 5 sorted, 3 need you')
    })

    it('shows progress and then the outcome without a click on anything else', async () => {
      ledger.statements = ledgerRows(['s1', 8, 7])
      let finish
      pollJob.mockImplementation((_id, onProgress) => {
        onProgress({ status: 'running', processed: 3, total: 8 })
        return new Promise(r => { finish = r })
      })
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))

      expect(await screen.findByText('Sorting 3 of 8')).toBeInTheDocument()
      // The floating progress card is pointed at the job the server started.
      expect(jobCtx.watchJobs).toHaveBeenCalled()
      expect(mockApi.post).not.toHaveBeenCalled()
      expect(jobCtx.startAnnotation).not.toHaveBeenCalled()

      finish(SORTED)
      expect(await screen.findByText('7 sorted, 1 needs you')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /Teach Me/ })).toBeInTheDocument()
      // The table is reloaded so its Sorted column catches up.
      expect(mockApi.get.mock.calls.filter(([p]) => p === '/statements').length).toBeGreaterThan(1)
    })

    it('says a job waiting behind another is waiting', async () => {
      pollJob.mockImplementation((_id, onProgress) => {
        onProgress({ status: 'queued', processed: 0, total: 0 })
        return new Promise(() => {})
      })
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      expect(await screen.findByText('Waiting to sort')).toBeInTheDocument()
    })

    it('says plainly when no model is connected, with the way to Settings', async () => {
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      expect(await screen.findByText(/No model is connected/)).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /Settings › AI & Model/ }).getAttribute('href')).toBe('#/settings?tab=ai')
    })

    it('says when the model stopped answering partway, with the way to Settings', async () => {
      pollJob.mockResolvedValue({ ...SORTED, ai_active: true, llm_error: 'connection refused' })
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      expect(await screen.findByText(/Your AI model stopped answering/)).toBeInTheDocument()
      expect(screen.queryByText(/connection refused/)).not.toBeInTheDocument()
      expect(screen.getByRole('link', { name: /Settings › AI & Model/ }).getAttribute('href')).toBe('#/settings?tab=ai')
    })

    it('reports a sorting job that failed, and keeps the statement', async () => {
      pollJob.mockRejectedValue(new Error('Went stale without a worker'))
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: ADD_BUTTON }))
      expect(await screen.findByText(/Sorting stopped/)).toBeInTheDocument()
      expect(screen.getByText(/8 payments added/)).toBeInTheDocument()
    })
  })
})

describe('Upload when the statement list fails', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    jobCtx.job = null
    jobCtx.startAnnotation = vi.fn()
    Object.assign(demoCtx, { sample: false, removable: false, mixed: false, hosted: false })
  })

  it('says the list could not load, never "No statements yet", and recovers on Retry', async () => {
    mockApi.get.mockRejectedValue(new TypeError('Failed to fetch'))
    renderUpload()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Couldn't load your statements.")
    expect(screen.queryByText(/No statements yet/)).not.toBeInTheDocument()
    serve()
    ledger.statements = [stmt]
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Kotak savings')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not flash "No statements yet" while the list is still loading', async () => {
    let release
    mockApi.get.mockReturnValue(new Promise(resolve => { release = () => resolve([]) }))
    renderUpload()
    expect(screen.queryByText(/No statements yet/)).not.toBeInTheDocument()
    release()
    expect(await screen.findByText(/No statements yet/)).toBeInTheDocument()
  })
})
