import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({ mockApi: { post: vi.fn() } }))
vi.mock('../lib/api.js', () => ({ api: mockApi }))
const noopToast = () => {}
vi.mock('../contexts/ToastContext.jsx', () => ({ useToast: () => noopToast }))

import TaxonomyHealth from './TaxonomyHealth.jsx'

const HEALTH = {
  duplicates: [{
    category: 'Food & Dining',
    keep: { id: 'cat_food_cafe', name: 'Cafe & Snacks', uses: 30 },
    merge: { id: 'u_cafes', name: 'Cafes', uses: 13 },
    same_name: false,
  }],
  orphans: [{
    category: 'Food & Dining', category_id: 'cat_food_dining', subcategory: 'Restaurant', count: 1,
    suggestion: { id: 'cat_food_restaurants', name: 'Restaurants' },
  }],
}
const CATS = [
  { id: 'cat_food_dining', name: 'Food & Dining', parent_id: null },
  { id: 'cat_food_restaurants', name: 'Restaurants', parent_id: 'cat_food_dining' },
  { id: 'cat_food_cafe', name: 'Cafe & Snacks', parent_id: 'cat_food_dining' },
]

describe('TaxonomyHealth', () => {
  beforeEach(() => { vi.clearAllMocks(); mockApi.post.mockResolvedValue({ moved: 1 }) })

  it('renders nothing when the taxonomy is tidy', () => {
    const { container } = render(<TaxonomyHealth health={{ duplicates: [], orphans: [] }} categories={CATS} onChanged={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('merges a duplicate only after a second, explicit click', async () => {
    const onChanged = vi.fn().mockResolvedValue()
    render(<TaxonomyHealth health={HEALTH} categories={CATS} onChanged={onChanged} />)
    fireEvent.click(screen.getByRole('button', { name: /Merge into "Cafe & Snacks"/ }))
    expect(mockApi.post).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /Move 13 transactions and delete "Cafes"\?/ }))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(mockApi.post).toHaveBeenCalledWith('/categories/subcategories/u_cafes/merge', { target_id: 'cat_food_cafe' })
  })

  it('moves an orphan to the suggested subcategory', async () => {
    const onChanged = vi.fn().mockResolvedValue()
    render(<TaxonomyHealth health={HEALTH} categories={CATS} onChanged={onChanged} />)
    expect(screen.getByRole('combobox', { name: /Move "Restaurant" to/ })).toHaveValue('cat_food_restaurants')
    fireEvent.click(screen.getByRole('button', { name: 'Move' }))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(mockApi.post).toHaveBeenCalledWith('/categories/tidy-up/orphans', {
      category_id: 'cat_food_dining', name: 'Restaurant', target_id: 'cat_food_restaurants',
    })
  })

  it('remembers a deliberate distinction', async () => {
    const onChanged = vi.fn().mockResolvedValue()
    render(<TaxonomyHealth health={HEALTH} categories={CATS} onChanged={onChanged} />)
    fireEvent.click(screen.getByRole('button', { name: /Keep both/ }))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(mockApi.post).toHaveBeenCalledWith('/categories/tidy-up/dismiss', { keep_id: 'cat_food_cafe', merge_id: 'u_cafes' })
  })
})
