import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from '../test/router.jsx'
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
        <AnnotationProgress job={{ kind: 'annotate', status: 'running', processed: 40, total: 120 }} />
      </MemoryRouter>
    )
    const link = screen.getByRole('link', { name: /sorting your payments/i })
    expect(link).toHaveAttribute('href', '/transactions')
    expect(screen.getByText('40 of 120')).toBeInTheDocument()
  })

  it('names a re-embed for what it is, not "sorting"', () => {
    render(
      <MemoryRouter>
        <AnnotationProgress job={{ kind: 'reembed', status: 'running', processed: 544, total: 668 }} />
      </MemoryRouter>
    )
    expect(screen.getByRole('link', { name: /updating matching/i })).toHaveAttribute('href', '/settings?tab=ai')
    expect(screen.queryByText(/sorting/i)).not.toBeInTheDocument()
  })

  it('names a model download, in megabytes of the whole file, and leads to the AI settings', () => {
    render(
      <MemoryRouter>
        <AnnotationProgress job={{ kind: 'model_download', status: 'running', processed: 1288490189, total: 2899102925 }} />
      </MemoryRouter>
    )
    expect(screen.getByRole('link', { name: /downloading the ai model/i })).toHaveAttribute('href', '/settings?tab=ai')
    expect(screen.getByText('1.2 GB of 2.7 GB')).toBeInTheDocument()
    expect(screen.queryByText(/sorting/i)).not.toBeInTheDocument()
  })

  it('does not crash the app for a job kind it has never heard of', () => {
    render(
      <MemoryRouter>
        <AnnotationProgress job={{ kind: 'something_new', status: 'running', processed: 1, total: 4 }} />
      </MemoryRouter>
    )
    expect(screen.getByRole('link', { name: /working in the background/i })).toBeInTheDocument()
    expect(screen.getByText('1 of 4')).toBeInTheDocument()
  })
})
