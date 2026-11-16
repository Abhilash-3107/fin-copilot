import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import InlineError from './InlineError.jsx'

describe('InlineError', () => {
  it('is an alert that says what could not load, with a Retry button', () => {
    const onRetry = vi.fn()
    render(<InlineError what="your statements" error={new TypeError('Failed to fetch')} onRetry={onRetry} />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent("Couldn't load your statements.")
    expect(alert).toHaveTextContent("Finance Copilot isn't answering.")
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('adds the server reason only for a refused request', () => {
    const { rerender } = render(
      <InlineError what="the groups" error={Object.assign(new Error('Bad month'), { status: 422 })} onRetry={() => {}} />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Bad month')
    rerender(<InlineError what="the groups" error={Object.assign(new Error('Internal Server Error'), { status: 500 })} onRetry={() => {}} />)
    expect(screen.getByRole('alert')).not.toHaveTextContent('Internal Server Error')
  })

  it('shows a busy Retry while the retry is running', () => {
    render(<InlineError what="the groups" onRetry={() => {}} retrying />)
    expect(screen.getByRole('button', { name: 'Retrying…' })).toBeDisabled()
  })
})
