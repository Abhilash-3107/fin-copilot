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

  it('Escape leaves the file out, from any control', () => {
    const { onCancel } = setup()
    fireEvent.keyDown(roleOf('When'), { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(screen.getByLabelText('Account'), { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(2)
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
      detail: 'This file covers days Kotak savings already has, and all 8 of its payments on those days match nothing there.',
      account_id: 'a1',
    })

    it('says so and leaves the choice to the person', () => {
      const { onSubmit, onCancel } = setup({ ask: held })
      expect(screen.getByRole('alert')).toHaveTextContent('match nothing there')
      fireEvent.click(screen.getByRole('button', { name: 'Add it anyway' }))
      expect(onSubmit).toHaveBeenCalledWith({ confirm_overlap: true })
      fireEvent.click(screen.getByRole('button', { name: 'Leave it out' }))
      expect(onCancel).toHaveBeenCalled()
    })

    it('puts focus on the safe action, leaving it out, and Escape leaves it out', () => {
      const { onCancel } = setup({ ask: held })
      expect(screen.getByRole('button', { name: 'Leave it out' })).toHaveFocus()
      fireEvent.keyDown(screen.getByRole('button', { name: 'Leave it out' }), { key: 'Escape' })
      expect(onCancel).toHaveBeenCalled()
    })

    it('offers another account: one that exists, or a new one with a name', () => {
      const { onSubmit } = setup({ ask: held })
      fireEvent.click(screen.getByRole('button', { name: 'This is another account' }))
      const choose = screen.getByLabelText('Account')
      expect(choose).toHaveFocus()
      // The account it was held against is not offered as "another".
      expect(within(choose).queryByRole('option', { name: 'Kotak savings' })).not.toBeInTheDocument()
      const add = screen.getByRole('button', { name: 'Add to this account' })
      expect(add).toBeDisabled()
      fireEvent.change(choose, { target: { value: 'a2' } })
      fireEvent.click(add)
      expect(onSubmit).toHaveBeenCalledWith({ account_id: 'a2' })
    })

    it('a new account needs a name and focus goes to the name; Enter adds', () => {
      const { onSubmit } = setup({ ask: held })
      fireEvent.click(screen.getByRole('button', { name: 'This is another account' }))
      fireEvent.change(screen.getByLabelText('Account'), { target: { value: '__new__' } })
      const name = screen.getByLabelText('Name for the new account')
      expect(name).toHaveFocus()
      expect(screen.getByRole('button', { name: 'Add to this account' })).toBeDisabled()
      fireEvent.change(name, { target: { value: ' Joint ' } })
      fireEvent.keyDown(name, { key: 'Enter' })
      expect(onSubmit).toHaveBeenCalledWith({ new_account_name: 'Joint' })
    })

    it('Escape closes the account choice first and returns to its button; a second Escape leaves the file out', async () => {
      const { onCancel } = setup({ ask: held })
      const another = screen.getByRole('button', { name: 'This is another account' })
      fireEvent.click(another)
      fireEvent.keyDown(screen.getByLabelText('Account'), { key: 'Escape' })
      expect(onCancel).not.toHaveBeenCalled()
      expect(screen.queryByLabelText('Account')).not.toBeInTheDocument()
      await waitFor(() => expect(another).toHaveFocus())
      fireEvent.keyDown(another, { key: 'Escape' })
      expect(onCancel).toHaveBeenCalledTimes(1)
    })
  })

  describe('the account the file already names', () => {
    it('starts on it, and it can still be changed', () => {
      const { onSubmit } = setup({ ask: ask({ needs: ['mapping'], account_id: 'a2' }) })
      expect(screen.getByLabelText('Account')).toHaveValue('a2')
      fireEvent.click(screen.getByRole('button', { name: 'Add this statement' }))
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ account_id: 'a2' }))
      fireEvent.change(screen.getByLabelText('Account'), { target: { value: 'a1' } })
      expect(screen.getByLabelText('Account')).toHaveValue('a1')
    })

    it('ignores an account that is not in the list', () => {
      setup({ ask: ask({ needs: ['mapping'], account_id: 'gone' }) })
      expect(screen.getByLabelText('Account')).toHaveValue('')
    })
  })

  describe('when the dates could be read either way', () => {
    const rows = date => ({
      rows: [{ date, description: 'UPI/A/1', amount: 100, direction: 'debit', balance: null }], error: null,
    })
    const dates = ask({
      needs: ['dates'],
      account_id: 'a1',
      date_order: 'dmy',
      parsed: { ...rows('2026-10-01'), date_choices: { dmy: rows('2026-10-01'), mdy: rows('2026-01-10') } },
    })

    it('asks which way round, day first chosen, with the parsed rows under each choice', () => {
      const { onSubmit } = setup({ ask: dates })
      expect(screen.getByRole('group', { name: 'Which way round are the dates?' })).toBeInTheDocument()
      const day = screen.getByRole('radio', { name: /Day first/ })
      const month = screen.getByRole('radio', { name: /Month first/ })
      expect(day).toBeChecked()
      expect(month).not.toBeChecked()
      expect(within(screen.getByRole('table', { name: /day first/ })).getByText('1 Oct 2026')).toBeInTheDocument()
      expect(within(screen.getByRole('table', { name: /month first/ })).getByText('10 Jan 2026')).toBeInTheDocument()
      fireEvent.click(month)
      fireEvent.click(screen.getByRole('button', { name: 'Add this statement' }))
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ date_order: 'mdy', account_id: 'a1' }))
    })

    it('starts on the answer the layout remembered', () => {
      setup({ ask: { ...dates, date_order: 'mdy' } })
      expect(screen.getByRole('radio', { name: /Month first/ })).toBeChecked()
    })

    it('will not add while the chosen reading cannot be read', () => {
      const broken = { ...dates, parsed: { ...dates.parsed, date_choices: { dmy: rows('2026-10-01'), mdy: { rows: [], error: 'The date "x" in row 3 isn\'t one we can read.' } } } }
      setup({ ask: broken })
      expect(screen.getByRole('button', { name: 'Add this statement' })).toBeEnabled()
      fireEvent.click(screen.getByRole('radio', { name: /Month first/ }))
      expect(screen.getByRole('button', { name: 'Add this statement' })).toBeDisabled()
    })

    it('sends no date order for a file whose dates are settled', () => {
      const { onSubmit } = setup()
      fireEvent.change(screen.getByLabelText('Account'), { target: { value: 'a1' } })
      fireEvent.click(screen.getByRole('button', { name: 'Add this statement' }))
      expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('date_order')
    })
  })

  describe('the column table', () => {
    it('shares the width between the columns, so the last select is never pushed off the edge', () => {
      setup({ ask: ask({
        columns: ['When', 'What', 'Moved', 'Left', 'Note'],
        preview: [['2026-10-02', 'UPI/X', '-1.00', '9.00', 'rent']],
        guess: { date: 0, description: 1, amount: 2 },
      }) })
      const selects = screen.getAllByRole('combobox').filter(el => el.getAttribute('aria-label')?.startsWith('What column'))
      expect(selects).toHaveLength(5)
      selects.forEach(el => expect(el.className).toContain('w-full'))
      const table = selects[0].closest('table')
      expect(table.className).toContain('table-fixed')
    })

    it('keeps a readable width per column and scrolls past that', () => {
      const columns = Array.from({ length: 12 }, (_, i) => `C${i}`)
      setup({ ask: ask({ columns, preview: [columns.map(() => 'x')], guess: { date: 0, description: 1 } }) })
      const table = screen.getAllByRole('combobox')[0].closest('table')
      expect(table.style.minWidth).toBe('84rem')
      expect(table.parentElement.className).toContain('overflow-x-auto')
    })
  })

  describe('privacy mode on the raw preview', () => {
    it.each(['1000.00 Dr', '9000.00 Cr', '₹1,000', 'INR 500', '(250.00)', '100.00-', '12'])(
      'hides %s in any column but the date and the description',
      figure => {
        privacy.hidden = true
        setup({ ask: ask({
          columns: ['When', 'What', 'Moved', 'Left', 'Note'],
          preview: [['2026-10-02', 'UPI/SWIGGY/1', figure, figure, figure]],
          guess: { date: 0, description: 1, amount: 2 },
        }) })
        const shown = screen.getAllByText(figure)
        expect(shown).toHaveLength(3)
        shown.forEach(el => expect(el).toHaveAttribute('data-privacy-mask'))
      },
    )

    it('hides a cell of a column left out, whatever it holds', () => {
      privacy.hidden = true
      setup({ ask: ask({ preview: [['2026-10-02', 'UPI/X', 'Dr 1000', '51460.00']], guess: { date: 0, description: 1 } }) })
      expect(screen.getByText('Dr 1000')).toHaveAttribute('data-privacy-mask')
    })

    it('leaves the date and a description with no figure readable', () => {
      privacy.hidden = true
      setup()
      expect(screen.getByText('2026-10-02')).not.toHaveAttribute('data-privacy-mask')
      screen.getAllByText('UPI/SWIGGY/ORDER/1/UPI').forEach(el => expect(el).not.toHaveAttribute('data-privacy-mask'))
    })

    it('hides a description that holds a figure, here and in the parsed rows', () => {
      privacy.hidden = true
      setup({ ask: ask({
        preview: [['2026-10-02', 'Rent 5000 for October', '-540.00', '1.00']],
        parsed: { rows: [{ date: '2026-10-02', description: 'Rent 5000', amount: 540, direction: 'debit', balance: null }], error: null },
      }) })
      expect(screen.getByText('Rent 5000 for October')).toHaveAttribute('data-privacy-mask')
      expect(screen.getByText('Rent 5000')).toHaveAttribute('data-privacy-mask')
    })

    it('shows the figures when privacy mode is off', () => {
      setup({ ask: ask({ preview: [['2026-10-02', 'x', '₹1,000', 'INR 500']], guess: { date: 0, description: 1 } }) })
      expect(screen.getByText('₹1,000')).not.toHaveAttribute('data-privacy-mask')
    })
  })
})
