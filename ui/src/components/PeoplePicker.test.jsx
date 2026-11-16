import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import PeoplePicker from './PeoplePicker.jsx'

const PEOPLE = [
  { id: 'p1', name: 'Neha Gupta', upi: 'neha.gupta@paytm' },
  { id: 'p2', name: 'Nitin Rao', upi: 'nitin' },
  { id: 'p3', name: 'Asha', upi: null },
]

function setup(props = {}) {
  const onChange = vi.fn()
  const onCreatePerson = vi.fn(async name => ({ id: 'new', name }))
  render(<PeoplePicker allPeople={PEOPLE} selectedIds={[]} onChange={onChange} onCreatePerson={onCreatePerson} {...props} />)
  return { input: screen.getByRole('combobox', { name: 'Search people' }), onChange, onCreatePerson }
}

describe('PeoplePicker', () => {
  it('shows a match token as it is, without a second at-sign', () => {
    const { input } = setup()
    fireEvent.focus(input)
    expect(screen.getByRole('option', { name: /Neha Gupta/ })).toHaveTextContent('Neha Guptaneha.gupta@paytm')
    expect(screen.getByRole('option', { name: /Neha Gupta/ })).not.toHaveTextContent('@neha')
  })

  it('typing filters, and Enter picks the highlighted match', () => {
    const { input, onChange } = setup()
    fireEvent.change(input, { target: { value: 'neha' } })
    expect(screen.getAllByRole('option')).toHaveLength(2) // Neha Gupta and the create row
    expect(screen.getByRole('option', { name: /Neha Gupta/ })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith(['p1'])
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('arrows move the highlight, which the input announces', () => {
    const { input, onChange } = setup()
    fireEvent.change(input, { target: { value: 'n' } })
    const options = screen.getAllByRole('option')
    expect(input).toHaveAttribute('aria-activedescendant', options[0].id)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input).toHaveAttribute('aria-activedescendant', options[1].id)
    expect(options[1]).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(input).toHaveAttribute('aria-activedescendant', options[0].id)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith(['p2'])
  })

  it('Escape closes the menu and keeps the typing', () => {
    const { input, onChange } = setup()
    fireEvent.change(input, { target: { value: 'neha' } })
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(input).toHaveValue('neha')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('Enter with only the create row creates the person', async () => {
    const { input, onCreatePerson, onChange } = setup()
    fireEvent.change(input, { target: { value: 'Zoya' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledWith(['new']))
    expect(onCreatePerson).toHaveBeenCalledWith('Zoya')
  })

  it('does nothing on Enter when nothing is typed', () => {
    const { input, onChange } = setup()
    fireEvent.focus(input)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).not.toHaveBeenCalled()
  })
})
