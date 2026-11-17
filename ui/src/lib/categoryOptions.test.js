import { describe, it, expect } from 'vitest'
import { categoryOptions, matchCategories, labelOf } from './categoryOptions.js'

const TREE = [
  { id: 1, name: 'Food & Dining', parent_id: null },
  { id: 2, name: 'Transport', parent_id: null },
  { id: 3, name: 'Uncategorized', parent_id: null },
  { id: 4, name: 'Groceries', parent_id: 1 },
  { id: 5, name: 'Cafes', parent_id: 1 },
  { id: 6, name: 'Fuel', parent_id: 2 },
]
const OPTIONS = categoryOptions(TREE)
const names = query => matchCategories(OPTIONS, query).map(labelOf)

describe('categoryOptions', () => {
  it('lists every category and subcategory, parents before their children', () => {
    expect(OPTIONS.map(labelOf)).toEqual([
      'Food & Dining', 'Food & Dining > Groceries', 'Food & Dining > Cafes',
      'Transport', 'Transport > Fuel',
    ])
  })

  it('never offers Uncategorized, which is not an answer', () => {
    expect(OPTIONS.map(labelOf).join()).not.toMatch(/Uncategorized/)
  })

  it('carries the category and subcategory names apart', () => {
    expect(OPTIONS[1]).toMatchObject({ category: 'Food & Dining', subcategory: 'Groceries' })
    expect(OPTIONS[0]).toMatchObject({ category: 'Food & Dining', subcategory: null })
  })
})

describe('matchCategories', () => {
  it('finds a subcategory from a few letters', () => {
    expect(names('gro')).toEqual(['Food & Dining > Groceries'])
  })

  it('matches every word, across the category and the subcategory', () => {
    expect(names('food caf')).toEqual(['Food & Dining > Cafes'])
  })

  it('ignores case and surrounding space', () => {
    expect(names('  FUEL ')).toEqual(['Transport > Fuel'])
  })

  it('lists the best match first: a name that starts with the text beats one that only contains it', () => {
    expect(names('ca')).toEqual(['Food & Dining > Cafes'])
    // 'Transport > Fuel' only contains "tr" in its parent's name, so the category itself comes first.
    expect(names('tr')).toEqual(['Transport', 'Transport > Fuel'])
    expect(names('f').slice(0, 2)).toEqual(['Food & Dining', 'Transport > Fuel'])
  })

  it('returns nothing for an empty or unknown query', () => {
    expect(names('')).toEqual([])
    expect(names('zzz')).toEqual([])
  })

  it('caps the list', () => {
    const many = categoryOptions([
      { id: 0, name: 'Top', parent_id: null },
      ...Array.from({ length: 20 }, (_, i) => ({ id: i + 1, name: `Item ${i}`, parent_id: 0 })),
    ])
    expect(matchCategories(many, 'item')).toHaveLength(8)
  })
})
