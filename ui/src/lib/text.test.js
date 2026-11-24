import { describe, it, expect } from 'vitest'
import { capCharacters } from './text.js'

describe('capCharacters', () => {
  it('leaves text within the cap alone', () => {
    expect(capCharacters('hello', 5)).toBe('hello')
  })

  it('cuts plain text at the cap', () => {
    expect(capCharacters('hello world', 5)).toBe('hello')
  })

  it('counts an emoji as one character, like the API', () => {
    const grin = '\u{1F600}'
    expect(capCharacters(grin.repeat(3), 3)).toBe(grin.repeat(3))
    expect(capCharacters(grin.repeat(4), 3)).toBe(grin.repeat(3))
  })

  it('never cuts an emoji in half', () => {
    const cut = capCharacters('ab\u{1F600}cd', 3)
    expect(cut).toBe('ab\u{1F600}')
  })
})
