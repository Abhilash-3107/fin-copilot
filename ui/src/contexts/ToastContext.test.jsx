import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ToastProvider, reloadWithToast } from './ToastContext.jsx'

describe('reloadWithToast', () => {
  beforeEach(() => sessionStorage.clear())

  it('shows the message once after the reload, then forgets it', () => {
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    reloadWithToast('Restored 7 payments from backup.db.', 'success')
    expect(reload).toHaveBeenCalledOnce()
    vi.unstubAllGlobals()

    const { unmount } = render(<ToastProvider><p>app</p></ToastProvider>)
    expect(screen.getByText('Restored 7 payments from backup.db.')).toBeInTheDocument()
    unmount()

    render(<ToastProvider><p>app</p></ToastProvider>)
    expect(screen.queryByText('Restored 7 payments from backup.db.')).not.toBeInTheDocument()
  })

  it('ignores a malformed parked message', () => {
    sessionStorage.setItem('fc_toast_after_reload', '{not json')
    render(<ToastProvider><p>app</p></ToastProvider>)
    expect(screen.getByText('app')).toBeInTheDocument()
    expect(sessionStorage.getItem('fc_toast_after_reload')).toBeNull()
  })
})
