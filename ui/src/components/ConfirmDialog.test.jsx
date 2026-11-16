import { StrictMode, useState } from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, within, fireEvent } from '@testing-library/react'
import ConfirmDialog from './ConfirmDialog.jsx'
import Modal from './Modal.jsx'

describe('ConfirmDialog', () => {
  it('is a modal dialog named by its title, holding both buttons', () => {
    const onConfirm = vi.fn()
    render(<ConfirmDialog open title="Delete everything?" confirmLabel="Delete everything" onConfirm={onConfirm} onCancel={() => {}} />)
    const dialog = screen.getByRole('dialog', { name: 'Delete everything?' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete everything' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })

  it('renders nothing while closed', () => {
    render(<ConfirmDialog open={false} title="Hidden" onConfirm={() => {}} onCancel={() => {}} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('dialog keyboard outside the dialog', () => {
  it('Escape cancels wherever focus is, and other keys never reach the page', () => {
    const onCancel = vi.fn()
    const onPageKey = vi.fn()
    window.addEventListener('keydown', onPageKey)
    render(
      <>
        <button>Behind</button>
        <ConfirmDialog open title="Delete?" confirmLabel="Delete" onConfirm={() => {}} onCancel={onCancel} />
      </>,
    )
    const behind = screen.getByRole('button', { name: 'Behind' })
    behind.focus()
    fireEvent.keyDown(behind, { key: 's' })
    fireEvent.keyDown(document.body, { key: 's' })
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 's' })
    expect(onPageKey).not.toHaveBeenCalled()
    fireEvent.keyDown(behind, { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
    window.removeEventListener('keydown', onPageKey)
  })

  it('Enter with focus behind a non-danger dialog confirms it and does not click the page button', () => {
    const onConfirm = vi.fn()
    render(
      <>
        <button>Behind</button>
        <ConfirmDialog open title="Delete?" onConfirm={onConfirm} onCancel={() => {}} />
      </>,
    )
    const notPrevented = fireEvent.keyDown(screen.getByRole('button', { name: 'Behind' }), { key: 'Enter' })
    expect(notPrevented).toBe(false)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('only the topmost of two open dialogs acts on a key', () => {
    const lower = vi.fn()
    const upper = vi.fn()
    render(
      <>
        <ConfirmDialog open title="Lower" onConfirm={() => {}} onCancel={lower} />
        <ConfirmDialog open title="Upper" onConfirm={() => {}} onCancel={upper} />
      </>,
    )
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(upper).toHaveBeenCalledTimes(1)
    expect(lower).not.toHaveBeenCalled()
  })

  it('works under React StrictMode, focusing the marked field and not the dialog', () => {
    render(
      <StrictMode>
        <Modal open title="Add person" onClose={() => {}}><input aria-label="Name" data-autofocus /></Modal>
      </StrictMode>,
    )
    expect(screen.getByLabelText('Name')).toHaveFocus()
  })
})

describe('Modal', () => {
  it('is a modal dialog named by its title', () => {
    const onClose = vi.fn()
    render(<Modal open title="Add person" onClose={onClose}><input aria-label="Name" /></Modal>)
    const dialog = screen.getByRole('dialog', { name: 'Add person' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(within(dialog).getByLabelText('Name')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

function Harness({ children }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)}>Open it</button>
      {open && children(() => setOpen(false))}
    </>
  )
}

describe('dialog keyboard', () => {
  it('Escape cancels a ConfirmDialog and never reaches window key handlers', () => {
    const onCancel = vi.fn()
    const onWindowKey = vi.fn()
    window.addEventListener('keydown', onWindowKey)
    render(<ConfirmDialog open title="Delete this statement?" onConfirm={() => {}} onCancel={onCancel} />)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    window.removeEventListener('keydown', onWindowKey)
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onWindowKey).not.toHaveBeenCalled()
  })

  it('Enter confirms, unless focus is on a button, which keeps its own meaning', () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    render(<ConfirmDialog open title="Delete?" confirmLabel="Delete" onConfirm={onConfirm} onCancel={onCancel} />)
    fireEvent.keyDown(screen.getByRole('button', { name: 'Cancel' }), { key: 'Enter' })
    expect(onConfirm).not.toHaveBeenCalled()
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Enter' })
    expect(onConfirm).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Enter', repeat: true })
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('a danger dialog starts on Cancel and one Enter confirms nothing', () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    render(<ConfirmDialog open danger title="Delete all data?" confirmLabel="Delete everything" onConfirm={onConfirm} onCancel={onCancel} />)
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('button', { name: 'Cancel' }), { key: 'Enter' })
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Enter' })
    fireEvent.keyDown(document.body, { key: 'Enter' })
    expect(onConfirm).not.toHaveBeenCalled()
    // Enter on a focused button is the browser's own click, so the Delete button still works.
    fireEvent.click(screen.getByRole('button', { name: 'Delete everything' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('moves focus into the dialog on open and back to the opener on close', () => {
    render(
      <Harness>
        {close => <ConfirmDialog open title="Delete?" onConfirm={close} onCancel={close} />}
      </Harness>,
    )
    const opener = screen.getByRole('button', { name: 'Open it' })
    opener.focus()
    fireEvent.click(opener)
    expect(screen.getByRole('dialog')).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })

  it('a Modal closes on Escape, takes focus, and leaves an autofocused field alone', () => {
    const onClose = vi.fn()
    render(<Modal open title="Add person" onClose={onClose}><input aria-label="Name" data-autofocus /></Modal>)
    expect(screen.getByLabelText('Name')).toHaveFocus()
    fireEvent.keyDown(screen.getByLabelText('Name'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('a Modal without a field to focus focuses itself, and returns focus on close', () => {
    render(<Harness>{close => <Modal open title="Info" onClose={close}><p>Hello</p></Modal>}</Harness>)
    const opener = screen.getByRole('button', { name: 'Open it' })
    opener.focus()
    fireEvent.click(opener)
    expect(screen.getByRole('dialog')).toHaveFocus()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(opener).toHaveFocus()
  })
})
