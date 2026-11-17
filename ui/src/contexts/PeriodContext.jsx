import { createContext, useContext, useCallback, useEffect, useMemo, useState } from 'react'
import { useStatement } from './StatementContext.jsx'
import { readStored, writeStored } from '../lib/storage.js'

const PeriodContext = createContext(null)

const STORAGE_KEY = 'fc_period_month'

// Sentinel for "no month filter" - every ingested month, aggregated.
export const ALL_TIME = 'all'

export function PeriodProvider({ children }) {
  const { statements, loading: statementsLoading } = useStatement()
  const [month, setMonthRaw] = useState(() => readStored(STORAGE_KEY) || null)

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

  // False until the period is settled: while the statement list is still
  // loading, or in the render between its arrival and the default month above.
  // A page that asks the server about "the period" waits for this, so it asks
  // once, with the month it will keep, and never replaces its first answer.
  const ready = month != null || (!statementsLoading && months.length === 0)

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
