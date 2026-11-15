import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api.js'

// What kind of data this ledger holds, for the banner, Settings > Data and the
// upload page. `hosted` is the shared demo instance (nothing there is yours or
// removable). `sample` is the bundled demo ledger on your own install, which
// you can remove; `mixed` means real statements sit next to it.
const NONE = { hosted: false, sample: false, mixed: false, refresh: () => {} }
const DemoDataCtx = createContext(NONE)

export function DemoDataProvider({ children }) {
  const [state, setState] = useState({ hosted: false, sample: false, mixed: false })

  const refresh = useCallback(async () => {
    try {
      const [health, demo] = await Promise.all([api.get('/health'), api.get('/data/demo')])
      setState({ hosted: !!health.demo_mode, sample: !!demo.loaded, mixed: !!demo.mixed })
    } catch {
      // Leave the banner off rather than guess; the pages work without it.
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const value = useMemo(() => ({ ...state, refresh }), [state, refresh])
  return <DemoDataCtx.Provider value={value}>{children}</DemoDataCtx.Provider>
}

export const useDemoData = () => useContext(DemoDataCtx)
