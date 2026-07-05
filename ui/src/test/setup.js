import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// jsdom in this environment doesn't expose a Storage implementation, so provide
// a minimal in-memory localStorage for code that persists UI state (privacy
// mode, active statement, etc.).
if (typeof globalThis.localStorage === 'undefined' || typeof globalThis.localStorage.getItem !== 'function') {
  const store = new Map()
  globalThis.localStorage = {
    getItem: key => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => { store.set(key, String(value)) },
    removeItem: key => { store.delete(key) },
    clear: () => { store.clear() },
    key: index => Array.from(store.keys())[index] ?? null,
    get length() { return store.size },
  }
}

// Unmount and reset the DOM between tests so nothing leaks across specs.
afterEach(() => {
  cleanup()
  localStorage.clear()
})
