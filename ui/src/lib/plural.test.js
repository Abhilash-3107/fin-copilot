import { describe, it, expect } from 'vitest'
import { count } from './plural.js'

describe('count', () => {
  it('uses the singular only for exactly one', () => {
    expect(count(0, 'group')).toBe('0 groups')
    expect(count(1, 'group')).toBe('1 group')
    expect(count(2, 'group')).toBe('2 groups')
  })

  it('accepts an irregular plural', () => {
    expect(count(2, 'person', 'people')).toBe('2 people')
  })
})
