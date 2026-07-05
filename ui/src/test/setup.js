import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// Unmount and reset the DOM between tests so nothing leaks across specs.
afterEach(() => {
  cleanup()
})
