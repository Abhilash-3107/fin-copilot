import { createContext, useContext, useCallback, useEffect, useMemo, useState } from 'react'
import { useStatement } from './StatementContext.jsx'

const PeriodContext = createContext(null)

const STORAGE_KEY = 'fc_period_month'

// Sentinel for "no month filter" — every ingested month, aggregated.
export const ALL_TIME = 'all'

export function PeriodProvider({ children }) {
  const { statements } = useStatement()
  const [month, setMonthRaw] = useState(() => localStorage.getItem(STORAGE_KEY) || null)

  // The months the user actually has data for, newest first.
  const months = useMemo(() => {
    const set = new Set(statements.map(s => s.statement_month).filter(Boolean))
    return [...set].sort((a, b) => b.localeCompare(a))
  }, [statements])

  // Once statements load, default the shared period to the latest month unless
  // the user (or a previous session) already chose one.
  useEffect(() => {
    if (month == null && months.length > 0) setMonthRaw(months[0])
  }, [month, months])

  const setMonth = useCallback(next => {
    setMonthRaw(next)
    if (next) localStorage.setItem(STORAGE_KEY, next)
    else localStorage.removeItem(STORAGE_KEY)
  }, [])

  return (
    <PeriodContext.Provider value={{ month, setMonth, months }}>
      {children}
    </PeriodContext.Provider>
  )
}

export function usePeriod() {
  const ctx = useContext(PeriodContext)
  if (!ctx) throw new Error('usePeriod must be used within a PeriodProvider')
  return ctx
}
