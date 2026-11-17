import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import TopBar from './TopBar.jsx'
import { OPEN_PALETTE_EVENT } from '../lib/shortcuts.js'

describe('TopBar search', () => {
  it('is a visible search field that shows the shortcut and opens the palette', () => {
    const opened = vi.fn()
    window.addEventListener(OPEN_PALETTE_EVENT, opened)
    render(<PrivacyProvider><TopBar onMenu={() => {}} /></PrivacyProvider>)
    const search = screen.getByRole('button', { name: /Search/ })
    expect(search).toHaveTextContent(/Search/)
    expect(search).toHaveTextContent(/⌘K|Ctrl K/)
    fireEvent.click(search)
    window.removeEventListener(OPEN_PALETTE_EVENT, opened)
    expect(opened).toHaveBeenCalledTimes(1)
  })
})

describe('TopBar shortcuts button', () => {
  it('opens the shortcut sheet', () => {
    const opened = vi.fn()
    window.addEventListener('fc:open-shortcuts', opened)
    render(<PrivacyProvider><TopBar onMenu={() => {}} /></PrivacyProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Keyboard shortcuts' }))
    window.removeEventListener('fc:open-shortcuts', opened)
    expect(opened).toHaveBeenCalledTimes(1)
  })
})
