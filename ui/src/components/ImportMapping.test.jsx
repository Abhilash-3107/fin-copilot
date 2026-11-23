import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({ mockApi: { upload: vi.fn() } }))
vi.mock('../lib/api.js', () => ({ api: mockApi }))
const { privacy } = vi.hoisted(() => ({ privacy: { hidden: false } }))
vi.mock('../contexts/PrivacyContext.jsx', () => ({ usePrivacy: () => privacy }))

import ImportMapping from './ImportMapping.jsx'

const ask = (extra = {}) => ({
  detail: "We couldn't tell which column is which. Check the columns, and choose the account.",
  needs: ['mapping', 'account'],
  columns: ['When', 'What', 'Moved', 'Left'],
  preview: [
    ['2026-10-02', 'UPI/SWIGGY/ORDER/1/UPI', '-540.00', '51460.00'],
    ['2026-10-03', 'NEFT/ORBIT/FEES', '42500.00', '93960.00'],
  ],
  guess: { date: 0, description: 1, amount: 2, balance: 3 },
  parsed: {
    rows: [{ date: '2026-10-02', description: 'UPI/SWIGGY/ORDER/1/UPI', amount: 540, direction: 'debit', balance: 51460 }],
    error: null,
  },
  accounts: [{ id: 'a1', account_label: 'Kotak savings' }, { id: 'a2', account_label: 'SBI savings' }],
  file_account_last4: null,
  ...extra,
})

const file = new File(['x'], 'export.csv', { type: 'text/csv' })

function setup(props = {}) {
  const onSubmit = vi.fn()
  const onCancel = vi.fn()
  const utils = render(<ImportMapping ask={ask()} file={file} onSubmit={onSubmit} onCancel={onCancel} {...props} />)
  return { onSubmit, onCancel, ...utils }
}

const roleOf = name => screen.getByRole('combobox', { name: `What column ${name} holds` })

describe('ImportMapping', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    privacy.hidden = false
    mockApi.upload.mockResolvedValue({ rows: [], error: null })
  })

  it('starts with the roles the server guessed, one select per column', () => {
    setup()
    expect(roleOf('When')).toHaveValue('date')
    expect(roleOf('What')).toHaveValue('description')
    expect(roleOf('Moved')).toHaveValue('amount')
    expect(roleOf('Left')).toHaveValue('balance')
  })

  it('shows the first rows as the file has them and as they would be read', () => {
    setup()
    expect(screen.getByText('-540.00')).toBeInTheDocument()
    const read = screen.getByRole('table', { name: /How the first rows read/ })
    expect(within(read).getByText('2 Oct 2026')).toBeInTheDocument()
    expect(within(read).getByText('−₹540.00')).toBeInTheDocument()
  })

  it('asks the server what the new choice reads as, and shows it', async () => {
    mockApi.upload.mockResolvedValue({
      rows: [{ date: '2026-10-02', description: 'x', amount: 1, direction: 'credit', balance: null }],
      error: null,
    })
    setup()
    fireEvent.change(roleOf('Left'), { target: { value: '' } })
    await waitFor(() => expect(mockApi.upload).toHaveBeenCalledTimes(1))
    const [path, form] = mockApi.upload.mock.calls[0]
    expect(path).toBe('/statements/upload/preview')
    expect(form.get('file')).toBe(file)
    expect(JSON.parse(form.get('mapping'))).toEqual({ date: 0, description: 1, amount: 2 })
    expect(await screen.findByText('+₹1.00')).toBeInTheDocument()
  })

  it('shows the reason a choice cannot be read, beside the choice', async () => {
    mockApi.upload.mockResolvedValue({ rows: [], error: 'The date "10/25/2026" in row 3 isn\'t one we can read.' })
    setup()
    fireEvent.change(roleOf('Left'), { target: { value: '' } })
    expect(await screen.findByRole('alert')).toHaveTextContent('row 3')
    fireEvent.change(screen.getByLabelText('Account'), { target: { value: 'a1' } })
    expect(screen.getByRole('button', { name: 'Add this statement' })).toBeDisabled()
  })

  it('will not add while one column holds two roles', () => {
    setup()
    fireEvent.change(roleOf('Left'), { target: { value: 'amount' } })
    expect(screen.getByRole('alert')).toHaveTextContent('More than one column is set to Amount')
    fireEvent.change(screen.getByLabelText('Account'), { target: { value: 'a1' } })
    expect(screen.getByRole('button', { name: 'Add this statement' })).toBeDisabled()
  })

  it('needs an account before it can add, and sends the choice with the columns', () => {
    const { onSubmit } = setup()
    const add = screen.getByRole('button', { name: 'Add this statement' })
    expect(add).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Account'), { target: { value: 'a2' } })
    expect(add).toBeEnabled()
    fireEvent.click(add)
    expect(onSubmit).toHaveBeenCalledWith({
      mapping: JSON.stringify({ date: 0, description: 1, amount: 2, balance: 3 }),
      account_id: 'a2',
    })
  })

  it('a new account needs a name, and Enter in its field adds', () => {
    const { onSubmit } = setup()
    fireEvent.change(screen.getByLabelText('Account'), { target: { value: '__new__' } })
    const name = screen.getByLabelText('Name for the new account')
    expect(screen.getByRole('button', { name: 'Add this statement' })).toBeDisabled()
    fireEvent.keyDown(name, { key: 'Enter' })
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.change(name, { target: { value: '  Savings 2 ' } })
    fireEvent.keyDown(name, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ new_account_name: 'Savings 2' }))
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('account_id')
  })

  it('with no account in the ledger it starts on a new one', () => {
    setup({ ask: ask({ accounts: [] }) })
    expect(screen.getByLabelText('Name for the new account')).toBeInTheDocument()
  })

  it('Escape leaves the file out, but not while a list is open', () => {
    const { onCancel } = setup()
    fireEvent.keyDown(roleOf('When'), { key: 'Escape' })
    expect(onCancel).not.toHaveBeenCalled()
    fireEvent.keyDown(screen.getByRole('group'), { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('Cancel leaves the file out', () => {
    const { onCancel } = setup()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalled()
  })

  it('focus starts on the first choice', () => {
    setup()
    expect(roleOf('When')).toHaveFocus()
  })

  it('says what the last try said when it was not enough', () => {
    setup({ error: 'The running balance does not add up at row 4.' })
    expect(screen.getByText('The running balance does not add up at row 4.')).toBeInTheDocument()
  })

  it('a file that asks only for its account says so and still lets the columns be checked', () => {
    setup({ ask: ask({ needs: ['account'] }) })
    expect(screen.getByText('Which account is this file from?')).toBeInTheDocument()
    expect(roleOf('Moved')).toHaveValue('amount')
  })

  it('hides figures from the file under privacy mode', () => {
    privacy.hidden = true
    const { container } = setup({ ask: ask({ parsed: { rows: [], error: "the balance should be ₹1,200.00 here" } }) })
    expect(container.querySelectorAll('[data-privacy-mask]').length).toBeGreaterThan(0)
    expect(screen.getByText('-540.00')).toHaveAttribute('data-privacy-mask')
    expect(screen.getByText('2026-10-02')).not.toHaveAttribute('data-privacy-mask')
    expect(screen.getByRole('alert')).not.toHaveTextContent('1,200.00')
  })

  describe('when the file mostly matches nothing', () => {
    const held = ask({
      needs: ['overlap'],
      detail: 'This file covers days Kotak savings already has, and 8 of its 8 payments on those days match nothing there.',
    })

    it('says so and leaves the choice to the person', () => {
      const { onSubmit, onCancel } = setup({ ask: held })
      expect(screen.getByRole('alert')).toHaveTextContent('match nothing there')
      fireEvent.click(screen.getByRole('button', { name: 'Add it anyway' }))
      expect(onSubmit).toHaveBeenCalledWith({ confirm_overlap: true })
      fireEvent.click(screen.getByRole('button', { name: 'Leave it out' }))
      expect(onCancel).toHaveBeenCalled()
    })

    it('puts focus on adding it anyway and Escape leaves it out', () => {
      const { onCancel } = setup({ ask: held })
      expect(screen.getByRole('button', { name: 'Add it anyway' })).toHaveFocus()
      fireEvent.keyDown(screen.getByRole('button', { name: 'Add it anyway' }), { key: 'Escape' })
      expect(onCancel).toHaveBeenCalled()
    })
  })
})
