import { useState } from 'react'
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
    render(<Modal open title="Add person" onClose={onClose}><input aria-label="Name" autoFocus /></Modal>)
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
