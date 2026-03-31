import { createContext, useContext, useEffect, useState } from 'react'
import { api } from '../lib/api.js'

const StatementContext = createContext(null)

const STORAGE_KEY = 'fc_active_statement_id'

export function StatementProvider({ children }) {
  const [statements, setStatements] = useState([])
  const [activeStatement, setActiveStatementRaw] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get('/statements')
      .then(data => {
        // Sort newest statement_month first, then by uploaded_at for same month
        const sorted = [...data].sort((a, b) => {
          if (b.statement_month !== a.statement_month)
            return b.statement_month.localeCompare(a.statement_month)
          return new Date(b.uploaded_at) - new Date(a.uploaded_at)
        })
        setStatements(sorted)

        const savedId = localStorage.getItem(STORAGE_KEY)
        const saved = savedId ? sorted.find(s => s.id === savedId) : null
        // Default to most recent if nothing saved or saved no longer exists
        setActiveStatementRaw(saved ?? sorted[0] ?? null)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  function setActiveStatement(stmt) {
    setActiveStatementRaw(stmt)
    if (stmt) localStorage.setItem(STORAGE_KEY, stmt.id)
    else localStorage.removeItem(STORAGE_KEY)
  }

  return (
    <StatementContext.Provider value={{ statements, activeStatement, setActiveStatement, loading }}>
      {children}
    </StatementContext.Provider>
  )
}

export function useStatement() {
  return useContext(StatementContext)
}
