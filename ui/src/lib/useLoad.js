import { useCallback, useEffect, useState } from 'react'

// Fetch, loading, error and retry for one block of data.
//
// `load` must be stable (wrap it in useCallback): a new function means "this is
// a different question", so it loads again and an older answer is ignored.
// A failed load keeps `error` and never an empty `data`, so the caller can show
// an inline error with Retry instead of an empty state.
export function useLoad(load) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let current = true
    setState(s => ({ ...s, loading: true, error: null }))
    Promise.resolve()
      .then(load)
      .then(
        data => { if (current) setState({ data, error: null, loading: false }) },
        error => { if (current) setState({ data: null, error, loading: false }) },
      )
    return () => { current = false }
  }, [load, attempt])

  const reload = useCallback(() => setAttempt(n => n + 1), [])
  const setData = useCallback(next => {
    setState(s => ({ ...s, data: typeof next === 'function' ? next(s.data) : next }))
  }, [])
  return { ...state, reload, setData }
}
