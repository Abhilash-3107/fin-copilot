import { describe, it, expect } from 'vitest'
import { sourceLabel, sourceFilterOptions } from './sources.js'

describe('source labels', () => {
  it('say where a category came from in plain words', () => {
    expect(sourceLabel('manual')).toBe('you')
    expect(sourceLabel('learned_rule')).toBe('remembered payee')
    expect(sourceLabel('rag_direct')).toBe('from history')
    expect(sourceLabel('rag_prompted')).toBe('from history')
    expect(sourceLabel('pair')).toBe('paired')
    expect(sourceLabel('pending')).toBe('not sorted')
    expect(sourceLabel(null)).toBe('not sorted')
    expect(sourceLabel(undefined)).toBe('not sorted')
  })

  it('lists one filter option per label', () => {
    const options = sourceFilterOptions(['rag_direct', 'rag_prompted', 'manual'])
    expect(options).toEqual([
      { label: 'from history', values: ['rag_direct', 'rag_prompted'] },
      { label: 'you', values: ['manual'] },
    ])
  })
})
