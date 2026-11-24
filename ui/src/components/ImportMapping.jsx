import { useEffect, useId, useRef, useState } from 'react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { errorReason } from '../lib/errors.js'
import { usePrivacy } from '../contexts/PrivacyContext.jsx'
import Amount, { maskRupees } from './Amount.jsx'

// The one question a spreadsheet statement may ask: which column is which, and
// which account the file is from. Shown on the file's own row. The server holds
// nothing between asks: the answer goes back with the same file, and what it
// would read with these columns comes from the server too (POST
// /statements/upload/preview), so the form never has a second row reader.

export const ROLES = [
  ['', 'Ignore'],
  ['date', 'Date'],
  ['description', 'Description'],
  ['debit', 'Paid out'],
  ['credit', 'Paid in'],
  ['amount', 'Amount'],
  ['drcr', 'Debit or credit marker'],
  ['balance', 'Balance'],
]

const NEW_ACCOUNT = '__new__'

// The narrowest a column of the table gets before the table scrolls instead.
const MIN_COLUMN_REM = 7

// A figure the server's amount reader would read out of a piece of text: a bare
// number or a rupee amount, with its brackets, sign, currency or Dr/Cr marker. A
// description that holds one ("Rent 5000") is hidden under privacy mode like any
// amount.
const FIGURE = /^[-+\u2212(]?(?:₹|rs\.?|inr)?\s*[\d,]*\d(?:\.\d+)?\)?[-\u2212]?(?:\s*(?:dr|cr|debit|credit)\.?)?$/i
const hasFigure = text => text.split(/\s+/).some(word => FIGURE.test(word))

// {role: column} from the server's guess, to one role (or '') per column.
function rolesFromGuess(columns, guess) {
  const roles = columns.map(() => '')
  for (const [role, index] of Object.entries(guess ?? {})) {
    if (index >= 0 && index < roles.length) roles[index] = role
  }
  return roles
}

function mappingOf(roles) {
  const mapping = {}
  roles.forEach((role, index) => { if (role) mapping[role] = index })
  return mapping
}

// A cell from the file, hidden in place under privacy mode like every other
// figure. Only the Date and Description columns stay readable (a description that
// holds a figure does not): whatever else the file holds (amounts, balances, a
// debit or credit marker, a column nobody named) can carry money in any written form.
function RawCell({ text, role }) {
  const { hidden } = usePrivacy()
  const readable = role === 'date' || (role === 'description' && !hasFigure(text))
  if (hidden && text.trim() !== '' && !readable) {
    return <span aria-hidden="true" data-privacy-mask className="blur-[5px] select-none">{text}</span>
  }
  return text
}

function Description({ text }) {
  const { hidden } = usePrivacy()
  if (hidden && hasFigure(text)) {
    return <span aria-hidden="true" data-privacy-mask className="blur-[5px] select-none">{text}</span>
  }
  return text
}

function Message({ children }) {
  const { hidden } = usePrivacy()
  return hidden ? maskRupees(children) : children
}

// What the file would be read as with the chosen columns, or why it cannot be.
function ParsedPreview({ state, caption = 'How the first rows read with these choices' }) {
  if (state.loading && !state.rows.length && !state.error) {
    return <p className="text-xs text-[#94a3b8]">Reading the first rows…</p>
  }
  if (state.error) {
    return <p role="alert" className="text-xs text-[#fca5a5]"><Message>{state.error}</Message></p>
  }
  return (
    <div className="overflow-x-auto" aria-busy={state.loading}>
      <table className="w-full text-xs">
        <caption className="text-left text-[#94a3b8] pb-1">{caption}</caption>
        <thead>
          <tr className="text-[#94a3b8] text-left">
            <th className="font-medium pr-3 pb-1">Date</th>
            <th className="font-medium pr-3 pb-1">Description</th>
            <th className="font-medium pr-3 pb-1 text-right">Amount</th>
            <th className="font-medium pb-1 text-right">Balance</th>
          </tr>
        </thead>
        <tbody>
          {state.rows.map((r, i) => (
            <tr key={i} className="text-[#e2e8f0]">
              <td className="pr-3 py-0.5 whitespace-nowrap">{dayjs(r.date).format('D MMM YYYY')}</td>
              <td className="pr-3 py-0.5 max-w-[22rem] truncate"><Description text={r.description} /></td>
              <td className="pr-3 py-0.5 text-right whitespace-nowrap">
                <Amount value={r.amount} debitCredit={r.direction} />
              </td>
              <td className="py-0.5 text-right whitespace-nowrap text-[#94a3b8]">
                {r.balance == null ? '-' : <Amount value={r.balance} />}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// The file's own words about the overlap: its payments mostly match nothing the
// account has over days it already covers. The person decides: add it anyway, say
// it is another account (an existing one, or a new one with a name), or leave it
// out. Focus starts on the safe action.
function OverlapNotice({ ask, onSubmit, onCancel }) {
  const id = useId()
  const leaveRef = useRef(null)
  const anotherRef = useRef(null)
  const choiceRef = useRef(null)
  const nameRef = useRef(null)
  const [choosing, setChoosing] = useState(false)
  const [account, setAccount] = useState('')
  const [newName, setNewName] = useState('')
  const others = (ask.accounts ?? []).filter(a => a.id !== ask.account_id)
  useEffect(() => { leaveRef.current?.focus() }, [])
  useEffect(() => { if (choosing) choiceRef.current?.focus() }, [choosing])
  useEffect(() => { if (account === NEW_ACCOUNT) nameRef.current?.focus() }, [account])

  const ready = account === NEW_ACCOUNT ? newName.trim() !== '' : account !== ''
  function submitAccount() {
    if (!ready) return
    onSubmit(account === NEW_ACCOUNT ? { new_account_name: newName.trim() } : { account_id: account })
  }
  function closeChoice() {
    setChoosing(false)
    // Back to the button that opened it: focus never falls to the page.
    requestAnimationFrame(() => anotherRef.current?.focus())
  }
  const button = 'px-3 py-1.5 rounded-md text-sm'
  return (
    <div
      className="mt-2 rounded-lg border border-[#451a03] bg-[#1c1208] p-3 space-y-2"
      onKeyDown={e => {
        if (e.key !== 'Escape') return
        e.stopPropagation()
        if (choosing) closeChoice()
        else onCancel()
      }}
    >
      <p role="alert" className="text-sm text-[#fdba74]"><Message>{ask.detail}</Message></p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onSubmit({ confirm_overlap: true })}
          className={`${button} bg-[#4f46e5] font-medium text-white hover:opacity-90`}
        >
          Add it anyway
        </button>
        <button
          ref={anotherRef}
          type="button"
          aria-expanded={choosing}
          onClick={() => setChoosing(true)}
          className={`${button} bg-[#1e2235] text-[#e2e8f0] hover:opacity-90`}
        >
          This is another account
        </button>
        <button
          ref={leaveRef}
          type="button"
          onClick={onCancel}
          className={`${button} bg-[#1e2235] text-[#94a3b8] hover:text-[#e2e8f0]`}
        >
          Leave it out
        </button>
      </div>
      {choosing && (
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={`${id}-account`} className="text-xs text-[#94a3b8]">Account</label>
          <select
            id={`${id}-account`}
            ref={choiceRef}
            value={account}
            onChange={e => setAccount(e.target.value)}
            className="bg-[#1e2235] border border-[#2d3148] focus:border-[#6366f1] text-[#e2e8f0] px-2 py-1 rounded text-sm focus:outline-none"
          >
            <option value="" disabled>Choose an account</option>
            {others.map(a => <option key={a.id} value={a.id}>{a.account_label}</option>)}
            <option value={NEW_ACCOUNT}>A new account</option>
          </select>
          {account === NEW_ACCOUNT && (
            <input
              ref={nameRef}
              aria-label="Name for the new account"
              placeholder="Name, for example Savings"
              value={newName}
              maxLength={60}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') { e.preventDefault(); if (!e.nativeEvent.isComposing && !e.repeat) submitAccount() }
              }}
              className="bg-[#1e2235] border border-[#2d3148] focus:border-[#6366f1] text-[#e2e8f0] px-2 py-1 rounded text-sm focus:outline-none w-64 placeholder:text-[#94a3b8]"
            />
          )}
          <button
            type="button"
            onClick={submitAccount}
            disabled={!ready}
            className={`${button} bg-[#4f46e5] font-medium text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            Add to this account
          </button>
        </div>
      )}
    </div>
  )
}

// `ask` is the server's 422 body. `onSubmit(answer)` sends the file again with it
// (the columns and account, the way round the dates go, or what to do about an
// overlap); `error` is what the last try said.
export default function ImportMapping({ ask, file, onSubmit, onCancel, busy = false, error = null }) {
  if (ask.needs?.includes('overlap')) {
    return <OverlapNotice ask={ask} onSubmit={onSubmit} onCancel={onCancel} />
  }
  return <MappingForm ask={ask} file={file} onSubmit={onSubmit} onCancel={onCancel} busy={busy} error={error} />
}

function MappingForm({ ask, file, onSubmit, onCancel, busy, error }) {
  const id = useId()
  const [roles, setRoles] = useState(() => rolesFromGuess(ask.columns, ask.guess))
  const noAccounts = ask.accounts.length === 0
  // The account the file already names (or its layout remembered) is where the form starts.
  const decided = ask.accounts.some(a => a.id === ask.account_id) ? ask.account_id : ''
  const [account, setAccount] = useState(decided || (noAccounts ? NEW_ACCOUNT : ''))
  const [newName, setNewName] = useState('')
  // Which way round the dates go, asked when the file does not say: day first is
  // the Indian convention, so it starts there (or on the answer remembered).
  const [dateOrder, setDateOrder] = useState(ask.date_order ?? 'dmy')
  const [preview, setPreview] = useState({
    rows: ask.parsed?.rows ?? [], error: ask.parsed?.error ?? null, choices: ask.parsed?.date_choices ?? null, loading: false,
  })
  const firstControl = useRef(null)
  const firstRun = useRef(true)

  useEffect(() => { firstControl.current?.focus() }, [])

  // What the server reads with these choices. The first answer came with the ask.
  useEffect(() => {
    if (firstRun.current) { firstRun.current = false; return undefined }
    // The form already says when one column holds two roles; the server has nothing to add.
    if (roles.some((role, i) => role && roles.indexOf(role) !== i)) return undefined
    let current = true
    setPreview(p => ({ ...p, loading: true }))
    const form = new FormData()
    form.append('file', file)
    form.append('mapping', JSON.stringify(mappingOf(roles)))
    api.upload('/statements/upload/preview', form)
      .then(got => { if (current) setPreview({ rows: got.rows, error: got.error, choices: got.date_choices ?? null, loading: false }) })
      .catch(e => { if (current) setPreview({ rows: [], error: errorReason(e), choices: null, loading: false }) })
    return () => { current = false }
  }, [roles, file])

  const asksMapping = ask.needs.includes('mapping')
  const asksAccount = ask.needs.includes('account')
  const clash = roles.find((role, i) => role && roles.indexOf(role) !== i)
  const accountReady = account === NEW_ACCOUNT ? newName.trim() !== '' : account !== ''
  // With the dates open, the reading that counts is the chosen one.
  const shown = preview.choices ? { ...preview, ...preview.choices[dateOrder] } : preview
  const ready = accountReady && !clash && !shown.error && !busy

  function submit() {
    if (!ready) return
    const answer = { mapping: JSON.stringify(mappingOf(roles)) }
    if (preview.choices) answer.date_order = dateOrder
    if (account === NEW_ACCOUNT) answer.new_account_name = newName.trim()
    else answer.account_id = account
    onSubmit(answer)
  }

  const labelOf = role => ROLES.find(([value]) => value === role)?.[1]
  return (
    <div
      role="group"
      aria-labelledby={`${id}-title`}
      className="mt-2 rounded-lg border border-[#2d3148] bg-[#0f1117] p-3 space-y-3"
      // A browser's open list takes its own Escape (it closes the list and the
      // page never sees the key), so an Escape that arrives here is the form's.
      onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); onCancel() } }}
    >
      <p id={`${id}-title`} className="text-sm text-[#e2e8f0]">
        {asksMapping ? 'Check what each column is' : asksAccount ? 'Which account is this file from?' : 'Which way round are the dates?'}
      </p>
      <p className="text-xs text-[#94a3b8]">
        {asksMapping
          ? "We couldn't tell the columns from the header. Choose what each one holds, and check the first rows read right. You are asked once: the next file with this header goes the same way."
          : asksAccount
            ? 'You are asked once: the next file with this header goes to the same account.'
            : "Nothing in this file says whether 01/10 is 1 October or 10 January. Choose the way round and check the first rows read right. The next file with this header is read the same way."}
      </p>

      <div className="overflow-x-auto">
        {/* Columns share the width, so the last select is never pushed off the edge;
            past seven columns the table keeps a readable minimum and scrolls. */}
        <table className="text-xs w-full table-fixed" style={{ minWidth: `${ask.columns.length * MIN_COLUMN_REM}rem` }}>
          <thead>
            <tr>
              {ask.columns.map((c, i) => (
                <th key={i} title={c} className="font-medium text-left text-[#94a3b8] pr-3 pb-1 align-bottom truncate">{c}</th>
              ))}
            </tr>
            <tr>
              {ask.columns.map((c, i) => (
                <th key={i} className="pr-3 pb-1.5 font-normal text-left">
                  <select
                    ref={i === 0 ? firstControl : null}
                    aria-label={`What column ${c} holds`}
                    value={roles[i]}
                    onChange={e => setRoles(list => list.map((r, j) => (j === i ? e.target.value : r)))}
                    className="w-full min-w-0 text-ellipsis bg-[#1e2235] border border-[#2d3148] focus:border-[#6366f1] text-[#e2e8f0] px-1.5 py-1 rounded text-xs focus:outline-none"
                  >
                    {ROLES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ask.preview.map((row, r) => (
              <tr key={r} className="text-[#94a3b8]">
                {ask.columns.map((c, i) => (
                  <td key={i} className="pr-3 py-0.5 truncate whitespace-nowrap">
                    <RawCell text={row[i] ?? ''} role={roles[i]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {clash && (
        <p role="alert" className="text-xs text-[#fca5a5]">
          More than one column is set to {labelOf(clash)}. Each can be used once.
        </p>
      )}

      {preview.choices ? (
        <fieldset className="space-y-2">
          <legend className="text-xs text-[#94a3b8] pb-1">Dates are</legend>
          {[['dmy', 'Day first (01/10 is 1 October)'], ['mdy', 'Month first (01/10 is 10 January)']].map(([value, label]) => (
            <div key={value} className="space-y-1">
              <label className="flex items-center gap-2 text-sm text-[#e2e8f0]">
                <input
                  type="radio"
                  name={`${id}-dates`}
                  value={value}
                  checked={dateOrder === value}
                  onChange={() => setDateOrder(value)}
                />
                {label}
              </label>
              <div className="pl-6"><ParsedPreview state={{ ...preview, ...preview.choices[value] }} caption={`How the first rows read, ${value === 'dmy' ? 'day first' : 'month first'}`} /></div>
            </div>
          ))}
        </fieldset>
      ) : (
        <ParsedPreview state={preview} />
      )}

      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={`${id}-account`} className="text-xs text-[#94a3b8]">Account</label>
        <select
          id={`${id}-account`}
          value={account}
          onChange={e => setAccount(e.target.value)}
          className="bg-[#1e2235] border border-[#2d3148] focus:border-[#6366f1] text-[#e2e8f0] px-2 py-1 rounded text-sm focus:outline-none"
        >
          {!noAccounts && <option value="" disabled>Choose an account</option>}
          {ask.accounts.map(a => <option key={a.id} value={a.id}>{a.account_label}</option>)}
          <option value={NEW_ACCOUNT}>A new account</option>
        </select>
        {account === NEW_ACCOUNT && (
          <input
            aria-label="Name for the new account"
            placeholder={ask.file_account_last4 ? `Name, for the account ending ${ask.file_account_last4}` : 'Name, for example Savings'}
            value={newName}
            maxLength={60}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') { e.preventDefault(); if (!e.nativeEvent.isComposing && !e.repeat) submit() }
            }}
            className="bg-[#1e2235] border border-[#2d3148] focus:border-[#6366f1] text-[#e2e8f0] px-2 py-1 rounded text-sm focus:outline-none w-64 placeholder:text-[#94a3b8]"
          />
        )}
      </div>

      {error && <p role="alert" className="text-xs text-[#fca5a5]"><Message>{error}</Message></p>}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={!ready}
          className="px-3 py-1.5 rounded-md bg-[#4f46e5] text-sm font-medium text-white hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {busy ? 'Adding…' : 'Add this statement'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1.5 rounded-md bg-[#1e2235] text-sm text-[#94a3b8] hover:text-[#e2e8f0]"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
