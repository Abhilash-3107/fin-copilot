import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import ShortcutSheet from './ShortcutSheet.jsx'
import { shortcutScopes } from '../lib/shortcuts.js'

describe('ShortcutSheet', () => {
  it('opens on ? and lists every shortcut from the one list, by scope', () => {
    render(<ShortcutSheet />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.keyDown(window, { key: '?', shiftKey: true })
    const dialog = screen.getByRole('dialog', { name: 'Keyboard shortcuts' })
    for (const { scope, items } of shortcutScopes()) {
      const group = within(dialog).getByRole('group', { name: scope })
      for (const item of items) expect(within(group).getByText(item.label)).toBeInTheDocument()
    }
  })

  it('Escape closes it', () => {
    render(<ShortcutSheet />)
    fireEvent.keyDown(window, { key: '?' })
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('does not open while typing in a field', () => {
    render(
      <>
        <input aria-label="Note" />
        <ShortcutSheet />
      </>,
    )
    fireEvent.keyDown(screen.getByLabelText('Note'), { key: '?' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('does not open for a modified key', () => {
    render(<ShortcutSheet />)
    fireEvent.keyDown(window, { key: '?', ctrlKey: true })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
