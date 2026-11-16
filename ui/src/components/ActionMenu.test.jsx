import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ActionMenu from './ActionMenu.jsx'

function setup() {
  const onRename = vi.fn()
  const onDelete = vi.fn()
  render(
    <>
      <ActionMenu
        label="More for Kotak"
        items={[
          { label: 'Rename', onSelect: onRename },
          { label: 'Delete', onSelect: onDelete, danger: true },
        ]}
      />
      <p>outside</p>
    </>,
  )
  return { onRename, onDelete, user: userEvent.setup(), button: () => screen.getByRole('button', { name: 'More for Kotak' }) }
}

describe('ActionMenu', () => {
  it('is closed until opened, and says so to a screen reader', async () => {
    const { user, button } = setup()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(button()).toHaveAttribute('aria-haspopup', 'menu')
    expect(button()).toHaveAttribute('aria-expanded', 'false')
    await user.click(button())
    expect(screen.getByRole('menu', { name: 'More for Kotak' })).toBeInTheDocument()
    expect(button()).toHaveAttribute('aria-expanded', 'true')
  })

  it('puts focus on the first item and moves with the arrow keys, wrapping', async () => {
    const { user, button } = setup()
    await user.click(button())
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toHaveFocus()
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('menuitem', { name: 'Delete' })).toHaveFocus()
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toHaveFocus()
    await user.keyboard('{ArrowUp}')
    expect(screen.getByRole('menuitem', { name: 'Delete' })).toHaveFocus()
    await user.keyboard('{Home}')
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toHaveFocus()
    await user.keyboard('{End}')
    expect(screen.getByRole('menuitem', { name: 'Delete' })).toHaveFocus()
  })

  it('runs the chosen item with Enter, closes, and hands focus back to the button', async () => {
    const { user, button, onRename, onDelete } = setup()
    await user.click(button())
    await user.keyboard('{Enter}')
    expect(onRename).toHaveBeenCalledTimes(1)
    expect(onDelete).not.toHaveBeenCalled()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(button()).toHaveFocus()
  })

  it('closes on Escape and returns focus to the button', async () => {
    const { user, button } = setup()
    await user.click(button())
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(button()).toHaveFocus()
  })

  it('closes on Tab and on a click outside, without running anything', async () => {
    const { user, button, onRename, onDelete } = setup()
    await user.click(button())
    await user.keyboard('{Tab}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    await user.click(button())
    await user.click(screen.getByText('outside'))
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(onRename).not.toHaveBeenCalled()
    expect(onDelete).not.toHaveBeenCalled()
  })

  it('closes when the button is pressed again', async () => {
    const { user, button } = setup()
    await user.click(button())
    await user.click(button())
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})
