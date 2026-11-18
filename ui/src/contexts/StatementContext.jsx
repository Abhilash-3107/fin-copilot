import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { readStored, writeStored } from '../lib/storage.js'
import { parseStoredTime } from '../lib/dates.js'

const StatementContext = createContext(null)

const STORAGE_KEY = 'fc_active_statement_id'

export function StatementProvider({ children }) {
  const [statements, setStatements] = useState([])
  // The months that have payments, oldest first. A statement can start in one
  // month and run into the next, so they come from the payments and not from
  // the statements. They are read with the statements and set in the same step,
  // so nothing that depends on both ever sees one without the other.
  const [months, setMonths] = useState([])
  const [activeStatement, setActiveStatementRaw] = useState(null)
  const [loading, setLoading] = useState(true)
  // The list everything else is scoped by: when it fails the app says so, with Retry.
  const [error, setError] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const reload = useCallback(() => setAttempt(n => n + 1), [])

  useEffect(() => {
    setError(null)
    Promise.all([api.get('/statements'), api.get('/transactions/months')])
      .then(([data, paymentMonths]) => {
        setMonths(paymentMonths?.months ?? [])
        // Sort newest statement_month first, then by uploaded_at for same month
        const sorted = [...data].sort((a, b) => {
          if (b.statement_month !== a.statement_month)
            return b.statement_month.localeCompare(a.statement_month)
          return parseStoredTime(b.uploaded_at) - parseStoredTime(a.uploaded_at)
        })
        setStatements(sorted)

        const savedId = readStored(STORAGE_KEY)
        const saved = savedId ? sorted.find(s => s.id === savedId) : null
        // Default to most recent if nothing saved or saved no longer exists
        setActiveStatementRaw(saved ?? sorted[0] ?? null)
      })
      .catch(setError)
      .finally(() => setLoading(false))
  }, [attempt])

  function setActiveStatement(stmt) {
    setActiveStatementRaw(stmt)
    writeStored(STORAGE_KEY, stmt ? stmt.id : null)
  }

  return (
    <StatementContext.Provider value={{ statements, months, activeStatement, setActiveStatement, loading, error, reload }}>
      {children}
    </StatementContext.Provider>
  )
}

export function useStatement() {
  return useContext(StatementContext)
}
