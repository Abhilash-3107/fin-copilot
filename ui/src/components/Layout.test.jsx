import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../test/router.jsx'

const { statementCtx } = vi.hoisted(() => ({ statementCtx: { error: null, reload: vi.fn() } }))
vi.mock('../contexts/StatementContext.jsx', () => ({ useStatement: () => statementCtx }))
vi.mock('../contexts/AnnotationJobContext.jsx', () => ({ useAnnotationJob: () => ({ job: null }) }))
vi.mock('./Sidebar.jsx', () => ({ default: () => <nav /> }))
vi.mock('./TopBar.jsx', () => ({ default: () => <header /> }))
vi.mock('./AnnotationProgress.jsx', () => ({ default: () => null }))
vi.mock('./CommandPalette.jsx', () => ({ default: () => null }))
vi.mock('./ShortcutSheet.jsx', () => ({ default: () => null }))
vi.mock('./DemoBanner.jsx', () => ({ default: () => null }))

import Layout from './Layout.jsx'

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route path="dashboard" element={<p>Dashboard page</p>} />
          <Route path="upload" element={<p>Upload page</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('Layout and the statement list', () => {
  it('says the statements could not load, above the page, with Retry', () => {
    statementCtx.error = new TypeError('Failed to fetch')
    renderAt('/dashboard')
    expect(screen.getByRole('alert')).toHaveTextContent("Couldn't load your statements.")
    expect(screen.getByText('Dashboard page')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(statementCtx.reload).toHaveBeenCalledTimes(1)
  })

  it('leaves Add Statement to show its own', () => {
    statementCtx.error = new TypeError('Failed to fetch')
    renderAt('/upload')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows nothing when the list loaded', () => {
    statementCtx.error = null
    renderAt('/dashboard')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
