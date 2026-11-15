import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api.js'

// What kind of data this ledger holds, for the banner, Settings > Data and the
// upload page. `hosted` is the shared demo instance (nothing there is yours or
// removable). `sample` is the bundled demo ledger; `removable` is the sample
// ledger on your own install, the only place the remove action exists (the
// hosted instance refuses it); `mixed` means real statements sit next to it.
const NONE = { hosted: false, sample: false, removable: false, mixed: false, refresh: () => {} }
const DemoDataCtx = createContext(NONE)

export function DemoDataProvider({ children }) {
  const [state, setState] = useState({ hosted: false, sample: false, mixed: false })

  // Two independent reads: a failed sample-data status must not hide the
  // hosted banner, and the other way round. A failure leaves that part as it
  // was rather than guessing; the pages work without either.
  const refresh = useCallback(async () => {
    const [health, demo] = await Promise.allSettled([api.get('/health'), api.get('/data/demo')])
    setState(prev => ({
      hosted: health.status === 'fulfilled' ? !!health.value.demo_mode : prev.hosted,
      sample: demo.status === 'fulfilled' ? !!demo.value.loaded : prev.sample,
      mixed: demo.status === 'fulfilled' ? !!demo.value.mixed : prev.mixed,
    }))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const value = useMemo(
    () => ({ ...state, removable: state.sample && !state.hosted, refresh }),
    [state, refresh],
  )
  return <DemoDataCtx.Provider value={value}>{children}</DemoDataCtx.Provider>
}

export const useDemoData = () => useContext(DemoDataCtx)
