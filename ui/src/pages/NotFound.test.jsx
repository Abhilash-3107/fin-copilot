import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../test/router.jsx'
import NotFound from './NotFound.jsx'

describe('NotFound', () => {
  it('says the page is not found and links home', () => {
    render(
      <MemoryRouter initialEntries={['/nope']}>
        <Routes>
          <Route path="*" element={<NotFound />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the Dashboard' })).toHaveAttribute('href', '/dashboard')
  })
})
