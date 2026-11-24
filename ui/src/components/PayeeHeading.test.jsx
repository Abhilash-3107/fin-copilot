import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import PayeeHeading, { noteLabel } from './PayeeHeading.jsx'

const ITEM = { payee: 'Swiggy', raw_description: 'UPI/SWIGGY', debit_credit: 'debit', note: 'dinner', note_is_yours: false }

describe('PayeeHeading', () => {
  it('leads with the payee and the note', () => {
    render(<PayeeHeading item={ITEM} />)
    expect(screen.getByRole('heading', { name: 'Swiggy' })).toBeInTheDocument()
    expect(screen.getByText(/Your note/)).toHaveTextContent('Your note: dinner')
  })

  it('shows no note line when there is none', () => {
    render(<PayeeHeading item={{ ...ITEM, note: null }} />)
    expect(screen.queryByText(/note/i)).not.toBeInTheDocument()
  })

  it('calls the note on money in just Note, unless the user wrote it', () => {
    expect(noteLabel({ ...ITEM, debit_credit: 'credit' })).toBe('Note')
    expect(noteLabel({ ...ITEM, debit_credit: 'credit', note_is_yours: true })).toBe('Your note')
    expect(noteLabel(ITEM)).toBe('Your note')
  })
})
