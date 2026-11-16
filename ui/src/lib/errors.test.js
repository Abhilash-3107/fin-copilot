import { describe, it, expect } from 'vitest'
import { errorReason } from './errors.js'

describe('errorReason', () => {
  it('gives the server reason for a refused request', () => {
    expect(errorReason(Object.assign(new Error('Pick a month'), { status: 422 }))).toBe('Pick a month')
  })

  it('says nothing useful-sounding for a server crash', () => {
    expect(errorReason(Object.assign(new Error('Internal Server Error'), { status: 500 }))).toBe('')
  })

  it('says the app did not answer when the request never got there', () => {
    expect(errorReason(new TypeError('Failed to fetch'))).toBe("Finance Copilot isn't answering.")
  })

  it('has no reason when there is no error', () => {
    expect(errorReason(null)).toBe('')
  })
})
