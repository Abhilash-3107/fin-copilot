import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({ mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {} }))
const noopToast = () => {}
vi.mock('../contexts/ToastContext.jsx', () => ({ useToast: () => noopToast }))

import CategorySettings from './CategorySettings.jsx'

const CATS = [
  { id: 'food', name: 'Food', parent_id: null },
  { id: 'cafe', name: 'Cafes', parent_id: 'food' },
  { id: 'rest', name: 'Restaurants', parent_id: 'food' },
]

describe('CategorySettings when a reload fails after a change', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.post.mockResolvedValue({ moved: 2 })
  })

  it('says the list may be out of date, with Retry, instead of silently showing the old one', async () => {
    let failing = false
    mockApi.get.mockImplementation(path => {
      if (failing) return Promise.reject(new TypeError('Failed to fetch'))
      return Promise.resolve(path === '/categories' ? CATS : { duplicates: [], orphans: [] })
    })
    render(<CategorySettings />)
    expect(await screen.findByText('Cafes')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    failing = true
    fireEvent.click(screen.getAllByTitle('Merge into another subcategory')[0])
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'rest' } })
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Couldn't load your categories.")

    failing = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})
