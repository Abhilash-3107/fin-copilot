import { describe, it, expect } from 'vitest'

// The em dash reads as machine-written. Use a plain hyphen, in strings and in
// comments alike, so none appears on screen or creeps back in through a paste.
const sources = import.meta.glob(['../**/*.{js,jsx,css}', '!../**/*.test.{js,jsx}', '!../test/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

describe('source text', () => {
  it('finds the sources it is meant to check', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(20)
  })

  it('has no em dash outside tests, written out or as an escape', () => {
    // Built from escapes so this file holds no em dash itself.
    const EM_DASH = '\u2014'
    const forms = [EM_DASH, '\\u2014', '\\u{2014}', '&mdash;', '&#8212;', '&#x2014;']
    const offenders = Object.entries(sources)
      .filter(([, text]) => forms.some(form => text.toLowerCase().includes(form.toLowerCase())))
      .map(([file]) => file)
    expect(offenders).toEqual([])
  })
})
