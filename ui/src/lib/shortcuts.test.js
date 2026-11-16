import { describe, it, expect, vi, afterEach } from 'vitest'
import { shortcutScopes, modKeyLabel } from './shortcuts.js'

afterEach(() => vi.unstubAllGlobals())

describe('shortcutScopes', () => {
  it('lists the global, search, Teach Me and Transactions shortcuts', () => {
    expect(shortcutScopes().map(s => s.scope)).toEqual(['Everywhere', 'Search', 'Teach Me', 'Transactions'])
  })

  it('gives every shortcut keys and a label, with no em dash', () => {
    for (const { items } of shortcutScopes()) {
      for (const item of items) {
        expect(item.keys.length).toBeGreaterThan(0)
        expect(item.label).not.toMatch(/\u2014/)
        expect(item.label.length).toBeGreaterThan(3)
      }
    }
  })

  it('names the modifier for the platform', () => {
    vi.stubGlobal('navigator', { platform: 'MacIntel' })
    expect(modKeyLabel()).toBe('⌘')
    vi.stubGlobal('navigator', { platform: 'Win32' })
    expect(modKeyLabel()).toBe('Ctrl')
    expect(shortcutScopes()[0].items[0].keys[0]).toBe('Ctrl')
  })
})
