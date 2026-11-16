import { describe, it, expect } from 'vitest'
import { count, joinWords } from './plural.js'

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

describe('joinWords', () => {
  it('reads as a sentence list', () => {
    expect(joinWords([])).toBe('')
    expect(joinWords(['the categories'])).toBe('the categories')
    expect(joinWords(['the categories', 'your settings'])).toBe('the categories and your settings')
    expect(joinWords(['the categories', 'your settings', 'your policies'])).toBe('the categories, your settings and your policies')
  })
})
