import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CategoryTypeahead from './CategoryTypeahead.jsx'

const TREE = [
  { id: 1, name: 'Food & Dining', parent_id: null },
  { id: 2, name: 'Transport', parent_id: null },
  { id: 4, name: 'Groceries', parent_id: 1 },
  { id: 5, name: 'Cafes', parent_id: 1 },
  { id: 6, name: 'Fuel', parent_id: 2 },
]

function setup(props = {}) {
  const handlers = { onPick: vi.fn(), onCreate: vi.fn().mockResolvedValue(true), onDone: vi.fn() }
  render(<CategoryTypeahead tree={TREE} chosenCategory="" {...handlers} {...props} />)
  return { ...handlers, user: userEvent.setup(), box: screen.getByRole('combobox', { name: 'Another category' }) }
}

describe('CategoryTypeahead', () => {
  it('ignores the Enter that confirms an input-method composition', async () => {
    const { user, box, onPick } = setup()
    await user.type(box, 'gro')
    fireEvent.keyDown(box, { key: 'Enter', isComposing: true })
    expect(onPick).not.toHaveBeenCalled()
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onPick).toHaveBeenCalled()
  })

  it('shows nothing until something is typed', () => {
    setup()
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('finds a subcategory from a few letters, with its category', async () => {
    const { user, box } = setup()
    await user.type(box, 'gro')
    expect(screen.getByRole('option', { name: 'Food & Dining > Groceries' })).toBeInTheDocument()
  })

  it('picks the top match with Enter and hands the keyboard back', async () => {
    const { user, box, onPick, onDone } = setup()
    await user.type(box, 'gro{Enter}')
    expect(onPick).toHaveBeenCalledWith({ category: 'Food & Dining', subcategory: 'Groceries' })
    expect(onDone).toHaveBeenCalled()
    expect(box).toHaveValue('')
  })

  it('moves through the matches with the arrow keys', async () => {
    const { user, box, onPick } = setup()
    await user.type(box, 'f')
    await user.keyboard('{ArrowDown}{Enter}')
    expect(onPick).toHaveBeenCalledOnce()
    expect(onPick.mock.calls[0][0]).not.toEqual({ category: 'Food & Dining', subcategory: null })
  })

  it('marks the active option for assistive technology', async () => {
    const { user, box } = setup()
    await user.type(box, 'gro')
    const option = screen.getByRole('option', { name: 'Food & Dining > Groceries' })
    expect(option).toHaveAttribute('aria-selected', 'true')
    expect(box).toHaveAttribute('aria-activedescendant', option.id)
  })

  it('closes on Escape without picking', async () => {
    const { user, box, onPick, onDone } = setup()
    await user.type(box, 'gro{Escape}')
    expect(onPick).not.toHaveBeenCalled()
    expect(onDone).toHaveBeenCalled()
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('picks with the mouse too', async () => {
    const { user, box, onPick } = setup()
    await user.type(box, 'fuel')
    await user.click(screen.getByRole('option', { name: 'Transport > Fuel' }))
    expect(onPick).toHaveBeenCalledWith({ category: 'Transport', subcategory: 'Fuel' })
  })

  it('offers to create a category that does not exist', async () => {
    const { user, box, onCreate } = setup()
    await user.type(box, 'Pets{Enter}')
    expect(onCreate).toHaveBeenCalledWith(null, 'Pets')
  })

  it('offers a subcategory of the category being chosen', async () => {
    const { user, box, onCreate } = setup({ chosenCategory: 'Transport' })
    await user.type(box, 'Parking')
    await user.click(screen.getByRole('option', { name: 'Create "Parking" in Transport' }))
    expect(onCreate).toHaveBeenCalledWith(2, 'Parking')
  })

  it('does not offer to create next to a real match', async () => {
    const { user, box } = setup()
    await user.type(box, 'Fuel')
    expect(screen.queryByRole('option', { name: /Create/ })).not.toBeInTheDocument()
    await user.clear(box)
    await user.type(box, 'gro')
    expect(screen.queryByRole('option', { name: /Create/ })).not.toBeInTheDocument()
  })

  it('stays open with the typed name when creating fails', async () => {
    const { user, box } = setup({ onCreate: vi.fn().mockResolvedValue(false) })
    await user.type(box, 'Pets{Enter}')
    expect(box).toHaveValue('Pets')
  })
})
