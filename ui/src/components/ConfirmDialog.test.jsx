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
