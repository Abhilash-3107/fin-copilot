import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PrivacyProvider } from './PrivacyContext.jsx'
import { ToastProvider, reloadWithToast, useToast } from './ToastContext.jsx'

function Caller({ message }) {
  const toast = useToast()
  return <button onClick={() => toast(message, 'error', 60_000)}>raise</button>
}

function renderToast(message) {
  render(
    <PrivacyProvider>
      <ToastProvider>
        <Caller message={message} />
      </ToastProvider>
    </PrivacyProvider>
  )
  fireEvent.click(screen.getByRole('button', { name: 'raise' }))
}

const WARNING = "Opening balance ₹12,000.00 doesn't match the closing balance ₹11,500.00 - a statement in between may be missing."

beforeEach(() => localStorage.clear())

describe('toasts and privacy mode', () => {
  it('shows a server message as written while amounts are shown', () => {
    renderToast(WARNING)
    expect(screen.getByText(WARNING)).toBeInTheDocument()
  })

  it('masks the amounts in a message while amounts are hidden', () => {
    localStorage.setItem('fc_privacy_hidden', '1')
    renderToast(WARNING)
    expect(screen.queryByText(/₹/)).not.toBeInTheDocument()
    expect(screen.getByText(/a statement in between may be missing/)).toBeInTheDocument()
  })

  it('masks a toast that is already showing when amounts get hidden', () => {
    renderToast(WARNING)
    fireEvent.keyDown(window, { key: 'P', shiftKey: true })
    expect(screen.queryByText(/₹/)).not.toBeInTheDocument()
  })
})

function renderApp() {
  return render(<PrivacyProvider><ToastProvider><p>app</p></ToastProvider></PrivacyProvider>)
}

describe('reloadWithToast', () => {
  beforeEach(() => sessionStorage.clear())

  it('shows the message once after the reload, then forgets it', () => {
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    reloadWithToast('Restored 7 payments from backup.db.', 'success')
    expect(reload).toHaveBeenCalledOnce()
    vi.unstubAllGlobals()

    const { unmount } = renderApp()
    expect(screen.getByText('Restored 7 payments from backup.db.')).toBeInTheDocument()
    unmount()

    renderApp()
    expect(screen.queryByText('Restored 7 payments from backup.db.')).not.toBeInTheDocument()
  })

  it('ignores a malformed parked message', () => {
    sessionStorage.setItem('fc_toast_after_reload', '{not json')
    renderApp()
    expect(screen.getByText('app')).toBeInTheDocument()
    expect(sessionStorage.getItem('fc_toast_after_reload')).toBeNull()
  })
})
