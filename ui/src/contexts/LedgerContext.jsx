import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api.js'
import { readStored, writeStored } from '../lib/storage.js'

const LedgerContext = createContext(null)

const ACCOUNT_KEY = 'fc_account_id'
// The "current statement" the Transactions picker used to remember. Nothing reads it
// now, so it is removed from the browser once instead of lingering.
const OLD_STATEMENT_KEY = 'fc_active_statement_id'

// What the screens share about the ledger: the accounts, the months that have
// payments, and which account the user is looking at (every account, or one).
// The accounts and the months are read together and set in the same step, so
// nothing that depends on both ever sees one without the other.
export function LedgerProvider({ children }) {
  const [accounts, setAccounts] = useState([])
  // The months that have payments, oldest first. A statement can start in one
  // month and run into the next, so they come from the payments.
  const [months, setMonths] = useState([])
  const [storedAccount, setStoredAccount] = useState(() => readStored(ACCOUNT_KEY) || '')
  const [loading, setLoading] = useState(true)
  // What everything else is scoped by: when it fails the app says so, with Retry.
  const [error, setError] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const reload = useCallback(() => setAttempt(n => n + 1), [])

  useEffect(() => { writeStored(OLD_STATEMENT_KEY, null) }, [])

  useEffect(() => {
    setError(null)
    Promise.all([api.get('/accounts'), api.get('/transactions/months')])
      .then(([list, paymentMonths]) => {
        setAccounts(list ?? [])
        setMonths(paymentMonths?.months ?? [])
      })
      .catch(setError)
      .finally(() => setLoading(false))
  }, [attempt])

  // The filter is for choosing between accounts, so it exists from two. A stored
  // account that is gone (or never was) reads as every account, with nothing to
  // tidy up and no second request when the list arrives.
  const multiple = accounts.length >= 2
  const accountId = multiple && accounts.some(a => a.id === storedAccount) ? storedAccount : ''
  const account = accounts.find(a => a.id === accountId) ?? null

  const setAccountId = useCallback(next => {
    setStoredAccount(next || '')
    writeStored(ACCOUNT_KEY, next || null)
  }, [])

  const value = useMemo(
    () => ({ accounts, months, multiple, accountId, account, setAccountId, loading, error, reload }),
    [accounts, months, multiple, accountId, account, setAccountId, loading, error, reload],
  )
  return <LedgerContext.Provider value={value}>{children}</LedgerContext.Provider>
}

// A screen outside the provider (a component drawn on its own) sees a ledger with a
// single account: no filter, no account labels.
const NO_LEDGER = {
  accounts: [], months: [], multiple: false, accountId: '', account: null,
  setAccountId: () => {}, loading: false, error: null, reload: () => {},
}

export function useLedger() {
  return useContext(LedgerContext) ?? NO_LEDGER
}
