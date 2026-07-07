import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AnnotationProgress from './AnnotationProgress.jsx'

describe('AnnotationProgress', () => {
  it('renders nothing when no job is running', () => {
    render(
      <MemoryRouter>
        <AnnotationProgress job={null} />
      </MemoryRouter>
    )
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('links the running-job card to the transactions page', () => {
    render(
      <MemoryRouter>
        <AnnotationProgress job={{ status: 'running', processed: 40, total: 120 }} />
      </MemoryRouter>
    )
    const link = screen.getByRole('link', { name: /categorizing your transactions/i })
    expect(link).toHaveAttribute('href', '/transactions')
    expect(screen.getByText('40 of 120')).toBeInTheDocument()
  })
})
