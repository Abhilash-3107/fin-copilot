import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
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

function Raiser({ calls }) {
  const toast = useToast()
  return (
    <>
      {calls.map(([label, ...args]) => (
        <button key={label} onClick={() => toast(...args)}>{label}</button>
      ))}
    </>
  )
}

function renderRaiser(calls) {
  render(<PrivacyProvider><ToastProvider><Raiser calls={calls} /></ToastProvider></PrivacyProvider>)
}

describe('one toast at a time', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('replaces the toast on screen instead of stacking another', () => {
    renderRaiser([['one', 'First message'], ['two', 'Second message']])
    fireEvent.click(screen.getByText('one'))
    fireEvent.click(screen.getByText('two'))
    expect(screen.queryByText('First message')).not.toBeInTheDocument()
    expect(screen.getByText('Second message')).toBeInTheDocument()
  })

  it('does not let the replaced toast timer dismiss its successor', () => {
    renderRaiser([['one', 'First message', 'info', 1000], ['two', 'Second message', 'info', 5000]])
    fireEvent.click(screen.getByText('one'))
    act(() => { vi.advanceTimersByTime(500) })
    fireEvent.click(screen.getByText('two'))
    act(() => { vi.advanceTimersByTime(2000) })
    expect(screen.getByText('Second message')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(3500) })
    expect(screen.queryByText('Second message')).not.toBeInTheDocument()
  })

  it('carries one action: clicking it runs it and closes the toast', () => {
    const undo = vi.fn()
    renderRaiser([['go', 'Sorted as Food', 'success', { action: { label: 'Undo', onClick: undo } }]])
    fireEvent.click(screen.getByText('go'))
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(undo).toHaveBeenCalledOnce()
    expect(screen.queryByText('Sorted as Food')).not.toBeInTheDocument()
  })

  it('keeps a toast with an action on screen longer than a plain one', () => {
    renderRaiser([
      ['plain', 'Plain message'],
      ['acting', 'Acting message', 'info', { action: { label: 'Undo', onClick: () => {} } }],
    ])
    fireEvent.click(screen.getByText('plain'))
    act(() => { vi.advanceTimersByTime(3000) })
    expect(screen.queryByText('Plain message')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('acting'))
    act(() => { vi.advanceTimersByTime(3000) })
    expect(screen.getByText('Acting message')).toBeInTheDocument()
  })

  it('announces errors as alerts and the rest politely', () => {
    renderRaiser([['bad', 'Broke', 'error'], ['fine', 'Fine', 'success']])
    fireEvent.click(screen.getByText('bad'))
    expect(screen.getByRole('alert')).toHaveTextContent('Broke')
    fireEvent.click(screen.getByText('fine'))
    expect(screen.getByRole('status')).toHaveTextContent('Fine')
  })

  it('still takes a duration as the third argument', () => {
    renderRaiser([['timed', 'Timed message', 'info', 1000]])
    fireEvent.click(screen.getByText('timed'))
    act(() => { vi.advanceTimersByTime(1100) })
    expect(screen.queryByText('Timed message')).not.toBeInTheDocument()
  })

  it('does not run an action twice when it is clicked twice', () => {
    const undo = vi.fn()
    renderRaiser([['go', 'Sorted', 'success', { action: { label: 'Undo', onClick: undo } }]])
    fireEvent.click(screen.getByText('go'))
    const button = screen.getByRole('button', { name: 'Undo' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(undo).toHaveBeenCalledOnce()
  })
})
