import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Modal from '../components/Modal.jsx'

function Page({ children }) {
  return (
    <>
      <button>Behind before</button>
      <Modal open title="Pick one" onClose={() => {}}>
        {children}
      </Modal>
      <button>Behind after</button>
    </>
  )
}

describe('useDialog Tab trap', () => {
  it('Tab on the last control goes to the first, Shift+Tab on the first to the last', async () => {
    const user = userEvent.setup()
    render(
      <Page>
        <input aria-label="Name" />
        <button>Save</button>
      </Page>,
    )
    const close = screen.getByRole('button', { name: 'Close' })
    const name = screen.getByLabelText('Name')
    const save = screen.getByRole('button', { name: 'Save' })

    save.focus()
    await user.tab()
    expect(close).toHaveFocus()

    await user.tab({ shift: true })
    expect(save).toHaveFocus()

    await user.tab({ shift: true })
    expect(name).toHaveFocus()
  })

  it('a full lap with Tab never reaches the page behind', async () => {
    const user = userEvent.setup()
    render(
      <Page>
        <input aria-label="Name" />
        <button>Save</button>
      </Page>,
    )
    const behind = [screen.getByRole('button', { name: 'Behind before' }), screen.getByRole('button', { name: 'Behind after' })]
    for (let i = 0; i < 8; i++) {
      await user.tab()
      expect(behind).not.toContain(document.activeElement)
      expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true)
    }
  })

  it('Tab with focus on the dialog itself goes to its first control, Shift+Tab to its last', () => {
    render(
      <Page>
        <button>Save</button>
      </Page>,
    )
    const dialog = screen.getByRole('dialog')
    dialog.focus()
    expect(fireEvent.keyDown(dialog, { key: 'Tab' })).toBe(false)
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
    dialog.focus()
    expect(fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true })).toBe(false)
    expect(screen.getByRole('button', { name: 'Save' })).toHaveFocus()
  })

  it('Tab from outside the dialog comes back in', () => {
    render(
      <Page>
        <button>Save</button>
      </Page>,
    )
    const behind = screen.getByRole('button', { name: 'Behind after' })
    behind.focus()
    expect(fireEvent.keyDown(behind, { key: 'Tab' })).toBe(false)
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true)
  })

  it('skips disabled controls and leaves Tab alone between two controls', async () => {
    const user = userEvent.setup()
    render(
      <Page>
        <button disabled>Not now</button>
        <button>Save</button>
        <button>Other</button>
      </Page>,
    )
    const save = screen.getByRole('button', { name: 'Save' })
    const other = screen.getByRole('button', { name: 'Other' })
    save.focus()
    await user.tab()
    expect(other).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
  })
})
