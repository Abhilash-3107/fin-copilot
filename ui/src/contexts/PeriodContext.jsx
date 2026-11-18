import { createContext, useContext, useCallback, useEffect, useMemo, useState } from 'react'
import { useStatement } from './StatementContext.jsx'
import { readStored, writeStored } from '../lib/storage.js'

const PeriodContext = createContext(null)

const STORAGE_KEY = 'fc_period_month'

// Sentinel for "no month filter" - every ingested month, aggregated.
export const ALL_TIME = 'all'

export function PeriodProvider({ children }) {
  const { months: ledgerMonths, loading: statementsLoading } = useStatement()
  const [month, setMonthRaw] = useState(() => readStored(STORAGE_KEY) || null)

  // The months that have payments, newest first.
  const months = useMemo(() => [...ledgerMonths].sort((a, b) => b.localeCompare(a)), [ledgerMonths])

  // Once statements load, default the shared period to the latest month unless
  // the user (or a previous session) already chose one.
  useEffect(() => {
    if (month == null && months.length > 0) setMonthRaw(months[0])
  }, [month, months])

  // Whether the period is decided: the statements and months have arrived and a
  // month is chosen (remembered, or the latest). A page that asks the server about
  // the period waits for this, so it asks once and not again when it settles.
  const ready = !statementsLoading && (month != null || months.length === 0)

  const setMonth = useCallback(next => {
    setMonthRaw(next)
    writeStored(STORAGE_KEY, next || null)
  }, [])

  return (
    <PeriodContext.Provider value={{ month, setMonth, months, ready }}>
      {children}
    </PeriodContext.Provider>
  )
}

export function usePeriod() {
  const ctx = useContext(PeriodContext)
  if (!ctx) throw new Error('usePeriod must be used within a PeriodProvider')
  return ctx
}
