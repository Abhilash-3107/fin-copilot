import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { useCallback } from 'react'
import { useLoad } from './useLoad.js'

describe('useLoad', () => {
  it('loads, then holds the data', async () => {
    const fetcher = vi.fn().mockResolvedValue(['a'])
    const { result } = renderHook(() => useLoad(fetcher))
    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toEqual(['a'])
    expect(result.current.error).toBeNull()
  })

  it('keeps the error, never an empty answer, and recovers on reload', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce(['a'])
    const { result } = renderHook(() => useLoad(fetcher))
    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(result.current.data).toBeNull()
    expect(result.current.loading).toBe(false)
    act(() => result.current.reload())
    await waitFor(() => expect(result.current.data).toEqual(['a']))
    expect(result.current.error).toBeNull()
  })

  it('loads again when the loader changes and ignores the older answer', async () => {
    let release
    const slow = () => new Promise(resolve => { release = () => resolve('old') })
    const fast = () => Promise.resolve('new')
    const { result, rerender } = renderHook(({ fn }) => useLoad(useCallback(fn, [fn])), { initialProps: { fn: slow } })
    rerender({ fn: fast })
    await waitFor(() => expect(result.current.data).toBe('new'))
    release()
    await new Promise(r => setTimeout(r, 0))
    expect(result.current.data).toBe('new')
  })
})
