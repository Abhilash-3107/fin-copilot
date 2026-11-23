import { useCallback, useEffect, useRef, useState } from 'react'
import { Upload as UploadIcon, FileText, Zap, Check, X, AlertTriangle, ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import dayjs from 'dayjs'
import { api, pollAnnotationJob } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { useAnnotationJob } from '../contexts/AnnotationJobContext.jsx'
import ConfirmDialog from '../components/ConfirmDialog.jsx'
import InlineError from '../components/InlineError.jsx'
import ActionMenu from '../components/ActionMenu.jsx'
import { useDemoData } from '../contexts/DemoDataContext.jsx'
import { useLedger } from '../contexts/LedgerContext.jsx'
import Modal from '../components/Modal.jsx'
import BooksGrid from '../components/BooksGrid.jsx'
import NameField, { isFieldEnter } from '../components/NameField.jsx'
import Tooltip from '../components/Tooltip.jsx'
import { formatStoredTime, statementSpan } from '../lib/dates.js'
import { annotationSummary, sortedText, summaryDuration } from '../lib/annotationSummary.js'
import { count } from '../lib/plural.js'
import { couldnt, errorReason } from '../lib/errors.js'

// Running-balance reconciliation badge (4.20): turns the parser's balance-chain
// rigor into visible trust. Green check when every consecutive pair reconciles,
// amber warning when any break is found; nothing when there's no chain to check.
function BalancedBadge({ check }) {
  if (!check || check.checked === 0) return <span className="text-xs text-[#94a3b8]">-</span>
  const ok = check.balanced
  return (
    <Tooltip content={ok
      ? 'Every running-balance step reconciles with its amount - the parse is internally consistent.'
      : "Some rows don't reconcile against the running balance; this statement may have been parsed imperfectly."}>
      <span className={`inline-flex items-center gap-1 whitespace-nowrap text-xs px-2 py-0.5 rounded-full font-medium ${
        ok ? 'bg-[#14532d] text-[#86efac]' : 'bg-[#451a03] text-[#fdba74]'
      }`}>
        {ok ? <Check size={12} /> : <AlertTriangle size={12} />}
        {check.consistent}/{check.checked}
      </span>
    </Tooltip>
  )
}

// Coverage badge: full (green), partial (amber), none (grey).
function CoverageBadge({ done, total }) {
  if (!total) return <span className="text-xs text-[#94a3b8]">-</span>
  const full = done >= total
  const none = done === 0
  const cls = full
    ? 'bg-[#14532d] text-[#86efac]'
    : none
      ? 'bg-[#1e2235] text-[#94a3b8]'
      : 'bg-[#451a03] text-[#fdba74]'
  const label = full ? 'Full' : none ? 'None' : 'Partial'
  return (
    <span className={`whitespace-nowrap text-xs px-2 py-0.5 rounded-full font-medium ${cls}`}>
      {label} · {done}/{total}
    </span>
  )
}

// A dialog's text is built while it is closed, when there is no statement yet.
const spanOf = s => (s ? statementSpan(s) : '')

const isPdf = f => f.type === 'application/pdf' || /\.pdf$/i.test(f.name)

// One line per file in a drop: what went in, then how sorting is going. The
// states are ready (chosen, not sent), uploading, sorting, sorted, and failed
// (the file did not go in: the reason is shown and the file can be retried).
function balanceLine(check) {
  if (!check || check.checked === 0) return 'No running balance to check'
  return check.balanced
    ? 'Balance checks out'
    : `Balance check: ${check.consistent} of ${check.checked} steps reconcile`
}

// What deleting a statement says. Payments that another statement also covered
// were skipped when that one was added (or moved in), so they go for good: one
// plain sentence.
function deleteDescription(statement) {
  if (!statement) return ''
  const shared = statement.shared_with_others ?? 0
  const sentence = shared > 0
    ? ` ${shared} of its payments ${shared === 1 ? 'is' : 'are'} also in another statement, which did not add ${shared === 1 ? 'it' : 'them'} again, so deleting this removes ${shared === 1 ? 'it' : 'them'} from your ledger.`
    : ''
  return `This will permanently remove "${statement.account_label} - ${statementSpan(statement)}" and all its payments.${sentence} This can't be undone.`
}

// What the file brought: all of it, or only what the account did not have yet.
function addedLine(statement) {
  const already = statement.rows_already_in ?? 0
  if (already === 0) return `${count(statement.rows_added ?? statement.transaction_count, 'payment')} added`
  return `${count(statement.rows_added, 'new payment')}, ${already} already in your ledger`
}

function sortLine(entry) {
  if (entry.status === 'sorted') return entry.outcome ? sortedText(entry.outcome.sorted, entry.outcome.needYou) : 'Sorting finished'
  if (entry.status === 'sortFailed') return `Sorting stopped${entry.message ? ` (${entry.message})` : ''}. Use Sort again below.`
  if (!entry.job) return 'Getting started…'
  if (entry.job.status === 'queued') return 'Waiting to sort'
  const { processed = 0, total = 0 } = entry.job
  return total ? `Sorting ${processed} of ${total}` : 'Sorting'
}

// "A new account" as a place to move a statement to: for one that ingest put in
// the wrong account (a second account that printed no number joins the bank's
// only account). It needs a name: without one, the two accounts would read the
// same everywhere.
function NewAccountForm({ onMove }) {
  const [name, setName] = useState('')
  const ready = name.trim() !== ''
  return (
    <div className="mt-4 pt-4 border-t border-[#2d3148]">
      <p className="text-sm font-medium text-[#e2e8f0] mb-2">A new account</p>
      <div className="flex items-center gap-2">
        <input
          aria-label="Name for the new account"
          aria-describedby="new-account-hint"
          placeholder="Name"
          required
          value={name}
          maxLength={60}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => { if (isFieldEnter(e) && ready) onMove(name) }}
          className="flex-1 bg-[#1e2235] border border-[#2d3148] focus:border-[#6366f1] text-[#e2e8f0] px-2 py-1.5 rounded text-sm focus:outline-none placeholder:text-[#94a3b8]"
        />
        <button
          type="button"
          onClick={() => onMove(name)}
          disabled={!ready}
          className="px-3 py-1.5 rounded-md bg-[#4f46e5] text-sm font-medium text-white hover:opacity-90 transition-opacity disabled:opacity-50 disabled:hover:opacity-50 disabled:cursor-not-allowed"
        >
          Move to a new account
        </button>
      </div>
      <p id="new-account-hint" className="text-xs text-[#94a3b8] mt-1.5">
        Give it a name, so you can tell the two accounts apart.
      </p>
    </div>
  )
}

// Where the statement went: into an account the ledger already had, or a new
// one, which is the moment to give it a name. Focus follows the keyboard: Escape
// returns to "Name it", saving lands on the line that now says the name.
function AccountLine({ statement, label, onName }) {
  const [naming, setNaming] = useState(false)
  const [named, setNamed] = useState(false)
  const lineRef = useRef(null)
  const nameItRef = useRef(null)
  // Where focus goes when the input closes. Null until the input has been used,
  // so a row that is only drawn never takes focus.
  const returnTo = useRef(null)
  useEffect(() => {
    if (naming || !returnTo.current) return
    const target = returnTo.current === 'button' ? nameItRef.current ?? lineRef.current : lineRef.current
    target?.focus()
    returnTo.current = null
  }, [naming])

  // Moved since it was added: the line says where it is now.
  if (statement.account_moved) return <p className="text-xs text-[#94a3b8] mt-0.5">Moved to {label}</p>
  if (!statement.account_is_new) return <p className="text-xs text-[#94a3b8] mt-0.5">Added to {label}</p>
  if (naming) {
    return (
      <div className="mt-1">
        <NameField
          initial=""
          onSave={async name => {
            if (await onName(name)) {
              returnTo.current = 'line'
              // A blank name clears it: nothing was named, so "Name it" stays.
              if (name.trim()) setNamed(true)
              setNaming(false)
            }
          }}
          onCancel={() => { returnTo.current = 'button'; setNaming(false) }}
        />
      </div>
    )
  }
  return (
    <p ref={lineRef} tabIndex={-1} className="text-xs text-[#94a3b8] mt-0.5 outline-none">
      Added to a new account, {label}.
      {/* Named here, or from the statement list: either way there is nothing left to offer. */}
      {!named && label === statement.account_label && (
        <>
          {' '}
          <button ref={nameItRef} type="button" onClick={() => setNaming(true)} className="text-[#818cf8] hover:underline">
            Name it
          </button>
        </>
      )}
    </p>
  )
}

function FileRow({ entry, onRemove, accountLabel, onNameAccount }) {
  const { file, status, statement } = entry
  const added = statement && (
    <>
      {statementSpan(statement)}
      {' - '}{addedLine(statement)}
      {' - '}{balanceLine(statement.balance_check)}
    </>
  )
  return (
    <li className="flex items-start gap-3 px-4 py-3 border-b border-[#1a1d27] last:border-b-0">
      <FileText size={14} className="text-[#818cf8] shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1">
        <span className="text-sm text-[#e2e8f0] break-all">{file.name}</span>
        {status === 'ready' && <p className="text-xs text-[#94a3b8] mt-0.5">Ready to add</p>}
        {status === 'uploading' && <p className="text-xs text-[#94a3b8] mt-0.5">Reading the statement…</p>}
        {status === 'failed' && <p className="text-xs text-[#fca5a5] mt-0.5">{entry.message}</p>}
        {added && <p className="text-xs text-[#94a3b8] mt-0.5">{added}</p>}
        {statement?.account_id && (
          <AccountLine
            statement={statement}
            label={accountLabel ?? statement.account_label}
            onName={name => onNameAccount(statement.account_id, name)}
          />
        )}
        {statement?.transfers_paired > 0 && (
          <p className="text-xs text-[#94a3b8] mt-0.5">
            {count(statement.transfers_paired, 'transfer')} paired with your other accounts
          </p>
        )}
        {/* Warnings belong to the file they came from, so they sit on its row instead of in a toast. */}
        {statement?.warnings?.map(w => <p key={w} className="text-xs text-[#fdba74] mt-0.5">{w}</p>)}
        {['sorting', 'sorted', 'sortFailed'].includes(status) && (
          <p className={`text-xs mt-0.5 ${status === 'sortFailed' ? 'text-[#fdba74]' : 'text-[#e2e8f0]'}`}>
            {sortLine(entry)}
          </p>
        )}
      </div>
      {onRemove && (
        <button
          onClick={onRemove}
          aria-label={`Remove ${file.name}`}
          className="text-[#94a3b8] hover:text-[#e2e8f0] transition-colors"
        >
          <X size={14} />
        </button>
      )}
    </li>
  )
}

export default function Upload() {
  const toast = useToast()
  const { removable, refresh: refreshDemoData } = useDemoData()
  // job is non-null while any run is tracked (here, another page, or
  // re-attached after a reload): one run at a time, so every Sort again waits.
  const { job, startAnnotation, watchJobs } = useAnnotationJob()
  const hosted = useDemoData().hosted
  // The app-wide accounts and months (the account filter and the period picker read
  // them): told when anything here changes a statement or its account's name.
  const { reload: reloadAppStatements } = useLedger()
  const [statements, setStatements] = useState([])
  const [accounts, setAccounts] = useState([])
  // The books: accounts by months, and the moves between the user's own accounts.
  const [books, setBooks] = useState({ months: [], accounts: [], transfers: [] })
  // The cell whose statements the list below is narrowed to: { accountId, month }.
  const [detail, setDetail] = useState(null)
  // The files of this drop, one entry each, and the ones refused up front.
  const [entries, setEntries] = useState([])
  const [refused, setRefused] = useState([])
  // Until the list answers, "No statements yet" would be a guess; a failure says so with Retry.
  const [listState, setListState] = useState({ loaded: false, error: null })
  const [password, setPassword] = useState('')
  const [uploading, setUploading] = useState(false)
  const nextId = useRef(0)
  const [dragOver, setDragOver] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [resetTarget, setResetTarget] = useState(null)
  const [annotatingId, setAnnotatingId] = useState(null)
  const [lastResult, setLastResult] = useState(null)
  // The statement whose account is being named from its menu, and the one being moved.
  const [editingId, setEditingId] = useState(null)
  const [moveTarget, setMoveTarget] = useState(null)
  // Each row's More button, to hand focus back to after a rename.
  const moreButtons = useRef({})
  const fileRef = useRef(null)

  const loadStatements = useCallback(async () => {
    try {
      const data = await api.get('/statements')
      setStatements(data)
      setListState({ loaded: true, error: null })
      return data
    } catch (e) {
      setListState({ loaded: true, error: e })
      return null
    }
  }, [])

  // The accounts are a small list beside the statements; if it cannot be read
  // the statements still show, so it keeps its last answer quietly.
  const loadAccounts = useCallback(async () => {
    try {
      setAccounts(await api.get('/accounts'))
    } catch { /* the statements list carries the error and its Retry */ }
  }, [])

  // Like the accounts, the grid is read quietly: the statements list carries the error.
  const loadBooks = useCallback(async () => {
    try {
      const next = await api.get('/accounts/books')
      if (Array.isArray(next?.accounts)) setBooks(next)
    } catch { /* the statements list carries the error and its Retry */ }
  }, [])

  // Everything that depends on which statements exist and what their accounts are called.
  const refresh = useCallback(async () => {
    const [rows] = await Promise.all([loadStatements(), loadAccounts(), loadBooks()])
    reloadAppStatements()
    return rows
  }, [loadStatements, loadAccounts, loadBooks, reloadAppStatements])

  useEffect(() => { loadStatements(); loadAccounts(); loadBooks() }, [loadStatements, loadAccounts, loadBooks])

  const patchEntry = useCallback((id, patch) => {
    setEntries(list => list.map(e => (e.id === id ? { ...e, ...patch } : e)))
  }, [])

  // Take the PDFs, say which of the others were left out. Only files that are
  // finished and sorted make way for the new ones: a file still being read or
  // sorted belongs to a drop in progress and must stay on the page.
  function addFiles(files) {
    const picked = Array.from(files)
    const pdfs = picked.filter(isPdf)
    setRefused(picked.filter(f => !isPdf(f)).map(f => `${f.name} isn't a PDF. Only PDF statements can be added.`))
    if (pdfs.length === 0) return
    setEntries(list => [
      ...list.filter(e => e.status !== 'sorted'),
      ...pdfs.map(file => ({ id: nextId.current++, file, status: 'ready' })),
    ])
  }

  function onDrop(e) {
    e.preventDefault()
    setDragOver(false)
    addFiles(e.dataTransfer.files)
  }

  function onFileChange(e) {
    addFiles(e.target.files)
    // Clear the input so picking the same file again (after an upload, a
    // delete or Remove) still fires a change event.
    e.target.value = ''
  }

  // When the job ends, what to tell the person comes from the same place as
  // the statements table and Teach Me: the server's own count of sorted
  // payments for this statement, not the job's counters.
  function trackSorting(id, statementId, jobId) {
    pollAnnotationJob(jobId, j => patchEntry(id, { job: j }))
      .then(async result => {
        const row = (await loadStatements())?.find(r => r.id === statementId)
        const outcome = row ? { sorted: row.sorted_count, needYou: row.txn_count - row.sorted_count } : null
        patchEntry(id, { status: 'sorted', result, outcome })
      })
      .catch(e => {
        patchEntry(id, { status: 'sortFailed', message: errorReason(e) })
        loadStatements()
      })
  }

  // One file after another: each has its own result, so a bad file does not
  // lose the others. The server starts sorting a statement the moment it lands;
  // this only follows that work.
  async function handleUpload() {
    const todo = entries.filter(e => ['ready', 'failed'].includes(e.status))
    if (todo.length === 0) return
    setUploading(true)
    for (const entry of todo) {
      patchEntry(entry.id, { status: 'uploading', message: null })
      try {
        const form = new FormData()
        form.append('file', entry.file)
        if (password) form.append('password', password)
        const result = await api.upload('/statements/upload', form)
        if (result.sorting_job_id) {
          patchEntry(entry.id, { status: 'sorting', statement: result, job: null })
          watchJobs()
          trackSorting(entry.id, result.id, result.sorting_job_id)
        } else {
          patchEntry(entry.id, { status: 'sortFailed', statement: result, message: "it couldn't be queued" })
        }
        refresh()
      } catch (e) {
        // A refusal with a reason of its own ("Nothing new: ...", "already added") reads as it is.
        patchEntry(entry.id, { status: 'failed', message: e?.status === 409 ? e.message : couldnt('read this file', e) })
      }
    }
    setUploading(false)
  }

  async function deleteStatement() {
    if (!deleteTarget) return
    try {
      await api.delete(`/statements/${deleteTarget.id}`)
      toast(`Deleted ${deleteTarget.account_label} - ${statementSpan(deleteTarget)}`, 'info')
      setDeleteTarget(null)
      refresh()
      refreshDemoData()
    } catch (e) {
      toast(couldnt('delete', e), 'error')
    }
  }

  async function resetStatementData() {
    if (!resetTarget) return
    try {
      await api.delete(`/statements/${resetTarget.id}/data`)
      toast(`Started over with ${resetTarget.account_label} - ${statementSpan(resetTarget)}`, 'info')
      setResetTarget(null)
      loadStatements()
      refreshDemoData()
    } catch (e) {
      toast(couldnt('start over', e), 'error')
    }
  }

  async function autoAnnotate(stmt) {
    setAnnotatingId(stmt.id)
    setLastResult(null)
    try {
      // The job flow, like every other entry point: one run at a time, progress
      // in the floating card, and the stale-job reaper behind it.
      const result = await startAnnotation({ statement_id: stmt.id })
      setLastResult(result)
      const summary = annotationSummary(result)
      toast(summary.message, summary.type, summaryDuration(summary))
      loadStatements()
    } catch (e) {
      toast(couldnt('sort', e), 'error')
    } finally {
      setAnnotatingId(null)
    }
  }

  // Naming an account, from a file's row or from a statement's menu. Resolves
  // true when it was saved, so the caller knows whether to close its input.
  async function nameAccount(accountId, name) {
    try {
      await api.patch(`/accounts/${accountId}`, { name })
      await refresh()
      return true
    } catch (e) {
      toast(couldnt('name this account', e), 'error')
      return false
    }
  }

  // The naming ended (saved or cancelled): the menu that started it gets focus
  // back, so the keyboard picks up where it left off.
  function endEditName(id) {
    setEditingId(null)
    moreButtons.current[id]?.focus()
  }

  async function saveEditName(s, name) {
    // Saving the words already shown is no change, and must not turn a derived
    // label into a name that would stop following the bank and digits.
    if (name.trim() === s.account_label || await nameAccount(s.account_id, name)) endEditName(s.id)
  }

  // Somewhere to move it: another account, or a new one split out of an account
  // that holds more than this statement.
  function canMove(s) {
    return accounts.some(a => a.id !== s.account_id) || canSplit(s)
  }

  function canSplit(s) {
    return (accounts.find(a => a.id === s.account_id)?.statement_count ?? 0) > 1
  }

  // `target` is an existing account, or { name } for a new one.
  async function moveStatement(target) {
    const s = moveTarget
    setMoveTarget(null)
    try {
      const made = await api.patch(
        `/statements/${s.id}`,
        target.id ? { account_id: target.id } : { new_account: true, name: target.name },
      )
      toast(moveMessage(s, made, target), 'info')
      // The file row of this statement says where it is now, not where it landed.
      setEntries(list => list.map(e => (e.statement?.id === s.id
        ? {
            ...e,
            statement: made.statement_deleted
              ? { ...e.statement, account_id: null }
              : { ...e.statement, account_id: made.account_id, account_label: made.account_label, account_moved: true },
          }
        : e)))
      refresh()
    } catch (e) {
      toast(couldnt('move this statement', e), 'error')
    }
  }

  // Where it went, and what of it the account had already (those payments were
  // dropped, after the user's labels, notes and links on them moved to the
  // account's own copies). Then what could not come along: work the account's own
  // copy kept instead of the moved one's, and payments the statement had skipped
  // when it was added, which stay in the account it left.
  function moveMessage(s, made, target) {
    const where = target.id ? target.account_label : made?.account_label ?? 'a new account'
    const already = made?.rows_already_there ?? 0
    const carried = made?.work_carried_over ?? 0
    const notCarried = made?.work_not_carried ?? 0
    const leftBehind = made?.payments_left_behind ?? 0
    let kept = ''
    if (carried > 0) {
      const of = carried === already ? (already === 1 ? 'it' : 'them') : `${carried} of them`
      kept = `; your labels and notes on ${of} were kept`
    }
    const differed = notCarried > 0
      ? ` ${count(notCarried, 'payment')} had a different label, note or transfer pair in this account, and the account's own was kept.`
      : ''
    if (made?.statement_deleted) {
      const was = already === 1 ? 'the one payment in it was' : `all ${count(already, 'payment')} in it were`
      return `${statementSpan(s)} was not moved: ${was} already in ${where}, so it was removed${kept}.${differed}`
    }
    const dropped = already > 0 ? ` ${count(already, 'payment')} already there, so left out${kept}.` : ''
    const stayed = leftBehind > 0
      ? ` ${count(leftBehind, 'payment')} in its dates ${leftBehind === 1 ? 'stays' : 'stay'} in ${made.left_behind_account_label ?? s.account_label}, where ${leftBehind === 1 ? 'it was' : 'they were'} already.`
      : ''
    return `Moved ${statementSpan(s)} to ${where}.${dropped}${differed}${stayed}`
  }

  const accountLabels = new Map(accounts.map(a => [a.id, a.account_label]))

  // The grid cell the list is narrowed to, and its statements.
  const detailAccount = detail && books.accounts.find(a => a.id === detail.accountId)
  const detailIds = detailAccount?.cells[detail.month]?.statement_ids
  const shownCell = detailIds ? { label: detailAccount.account_label, month: detail.month } : null
  const shownStatements = detailIds ? statements.filter(s => detailIds.includes(s.id)) : statements

  // The account of a drop, once nothing in it is still being read or sorted.
  const sortedEntries = entries.filter(e => e.status === 'sorted')
  const busy = entries.some(e => ['uploading', 'sorting'].includes(e.status))
  const todoCount = entries.filter(e => ['ready', 'failed'].includes(e.status)).length
  const finished = !busy && sortedEntries.length > 0
    ? {
        ...sortedEntries.reduce((t, e) => ({
          sorted: t.sorted + (e.outcome?.sorted ?? 0),
          needYou: t.needYou + (e.outcome?.needYou ?? 0),
        }), { sorted: 0, needYou: 0 }),
        noModel: sortedEntries.some(e => e.result.ai_active === false),
        modelError: sortedEntries.map(e => e.result.llm_error).find(Boolean) ?? null,
      }
    : null

  return (
    <div className="px-6 py-5 space-y-6">
      <h1 className="text-base font-semibold text-[#e2e8f0]">Add Statement</h1>

      {/* The drop zone, what happened to each file, and the lines about what works stay a
          readable width; the books and the statements below use the whole page. */}
      <div className="space-y-6 max-w-5xl">

      {removable && (
        <div className="rounded-xl border border-[#451a03] bg-[#1c1208] px-4 py-3">
          <p className="text-sm text-[#fdba74]">
            Your ledger still has sample data. Use Remove sample data in the bar above first, so it
            doesn&apos;t mix with your own statement.
          </p>
        </div>
      )}

      {/* Drop zone */}
      <div
        // While sample data is in the ledger a statement can't be added. The
        // frame goes quiet but the words stay readable, so it can still say why.
        aria-disabled={removable || undefined}
        onDragOver={e => { e.preventDefault(); if (!removable) setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => { if (removable) e.preventDefault(); else onDrop(e) }}
        onClick={() => { if (!removable) fileRef.current?.click() }}
        className={`border-2 border-dashed rounded-xl p-10 text-center transition-colors ${
          removable
            ? 'cursor-not-allowed border-[#1e2235]'
            : dragOver
              ? 'cursor-pointer border-[#6366f1] bg-[#1e2440]'
              : 'cursor-pointer border-[#2d3148] hover:border-[#3d4158] hover:bg-[#13151f]'
        }`}
      >
        <UploadIcon size={32} className="mx-auto mb-3 text-[#94a3b8]" />
        <p className="text-sm text-[#94a3b8]">Drop your bank statements here, or click to browse</p>
        <p className="text-xs text-[#94a3b8] mt-1">PDF only. You can add several at once.</p>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,application/pdf"
          multiple
          onChange={onFileChange}
          disabled={removable}
          className="hidden"
        />
      </div>

      {refused.length > 0 && (
        <div role="alert" className="rounded-xl border border-[#450a0a] bg-[#1c0f0f] px-4 py-3 space-y-1">
          {refused.map(m => <p key={m} className="text-sm text-[#fca5a5]">{m}</p>)}
        </div>
      )}

      {entries.length === 0 && !hosted && !removable && (
        <ul className="space-y-1.5 text-sm text-[#94a3b8] list-disc pl-5 marker:text-[#94a3b8]">
          <li>
            Works with Kotak statements, and with other banks when the statement has a
            running-balance column.
          </li>
          <li>
            Any date range works, a mid-month download included. Payments you already have are
            skipped, so statements can overlap.
          </li>
          <li>Password-protected PDFs work: enter the password after you choose the file.</li>
          <li>
            Your statement stays on this computer. If you connect an online AI model in Settings,
            each payment&apos;s date, amount, description and note are sent to it for sorting,
            along with a few similar payments you have already sorted and how you sorted them.
          </li>
          <li>
            What happens next: the balance is checked, then sorting starts by itself. Whatever
            it can&apos;t decide waits for you in Teach Me.
          </li>
        </ul>
      )}

      {entries.length > 0 && (
        <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
          <ul>
            {entries.map(entry => (
              <FileRow
                key={entry.id}
                entry={entry}
                accountLabel={accountLabels.get(entry.statement?.account_id)}
                onNameAccount={nameAccount}
                onRemove={entry.status === 'ready' || entry.status === 'failed'
                  ? () => setEntries(list => list.filter(e => e.id !== entry.id))
                  : null}
              />
            ))}
          </ul>
        </div>
      )}

      {entries.some(e => ['ready', 'failed', 'uploading'].includes(e.status)) && (
        <div className="flex items-center gap-3">
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="Password (if a PDF is locked)"
            className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] placeholder:text-[#94a3b8] w-64"
          />
          <button
            onClick={handleUpload}
            disabled={uploading}
            className="flex items-center gap-2 bg-[#4f46e5] text-white px-5 py-2 rounded-md text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            <UploadIcon size={14} />
            {uploading ? 'Adding…' : todoCount > 1 ? `Add ${todoCount} statements` : 'Add statement'}
          </button>
        </div>
      )}

      {finished && (
        <div role="status" className="bg-[#13151f] border border-[#2d3148] rounded-xl px-4 py-3 space-y-2">
          <p className="text-sm text-[#e2e8f0]">{`Finished: ${sortedText(finished.sorted, finished.needYou)}`}</p>
          {finished.needYou > 0 ? (
            <Link to="/review" className="inline-flex items-center gap-1 text-sm text-[#818cf8] hover:underline">
              Go to Teach Me to sort the rest <ArrowRight size={13} />
            </Link>
          ) : (
            <Link to="/transactions" className="inline-flex items-center gap-1 text-sm text-[#818cf8] hover:underline">
              See your payments <ArrowRight size={13} />
            </Link>
          )}
          {finished.modelError && (
            <p className="text-xs text-[#fdba74]">
              Your AI model stopped answering, so what it hadn&apos;t reached was left for you.
              Check it in{' '}
              <Link to="/settings?tab=ai" className="text-[#818cf8] hover:underline">Settings › AI &amp; Model</Link>{' '}
              and use Sort again to try the rest.
            </p>
          )}
          {finished.noModel && (
            <p className="text-xs text-[#94a3b8]">
              No model is connected, so only your rules and remembered payees were used. Connect one in{' '}
              <Link to="/settings?tab=ai" className="text-[#818cf8] hover:underline">Settings › AI &amp; Model</Link>{' '}
              and use Sort again to try the rest.
            </p>
          )}
        </div>
      )}

      {/* Pipeline result */}
      {lastResult && (
        <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-[#94a3b8] mb-3">Sort results</p>
          <div className="flex flex-wrap gap-2">
            {[
              { label: 'Processed', value: lastResult.total_processed, color: 'bg-[#1e2235] text-[#94a3b8]' },
              { label: 'Matched by rules', value: lastResult.rule_matched, color: 'bg-[#1e3a5f] text-[#7dd3fc]' },
              { label: 'Matched from history', value: (lastResult.rag_direct_annotated ?? 0) + (lastResult.rag_prompted_annotated ?? 0), color: 'bg-[#164e63] text-[#67e8f9]' },
              // With no AI configured the LLM stage never ran: the remainder is
              // "waiting for you", not "couldn't figure out".
              ...(lastResult.ai_active === false
                ? [{ label: 'Waiting for you', value: lastResult.llm_failed, color: 'bg-[#451a03] text-[#fdba74]' }]
                : [
                    { label: 'Figured out by AI', value: lastResult.llm_annotated, color: 'bg-[#3b1f5e] text-[#c4b5fd]' },
                    { label: 'Couldn\'t figure out', value: lastResult.llm_failed, color: 'bg-[#450a0a] text-[#fca5a5]' },
                  ]),
              { label: 'Needs you', value: lastResult.low_confidence, color: 'bg-[#451a03] text-[#fdba74]' },
              { label: 'Already done', value: lastResult.already_annotated, color: 'bg-[#14532d] text-[#86efac]' },
            ].map(({ label, value, color }) => (
              <span key={label} className={`text-xs px-3 py-1 rounded-full font-medium ${color}`}>
                {label}: {value ?? 0}
              </span>
            ))}
          </div>
          {lastResult.llm_error && (
            <p className="text-xs text-[#fca5a5] mt-3">
              Your AI model stopped answering, so this run stopped using it.
              Check it in{' '}
              <Link to="/settings?tab=ai" className="text-[#818cf8] hover:underline">Settings › AI &amp; Model</Link>{' '}
              and use Sort again for what&apos;s left.
            </p>
          )}
          {lastResult.ai_active === false && (
            <p className="text-xs text-[#94a3b8] mt-3">
              No AI model is connected, so only your rules and remembered payees did the work above. Connect one in{' '}
              <Link to="/settings?tab=ai" className="text-[#818cf8] hover:underline">Settings › AI &amp; Model</Link>{' '}
              to sort the rest automatically.
            </p>
          )}
        </div>
      )}

      </div>

      <BooksGrid
        books={books}
        selected={detail}
        onShowStatements={(accountId, month) => {
          setDetail(d => (d?.accountId === accountId && d?.month === month ? null : { accountId, month }))
        }}
        onRename={nameAccount}
      />

      {/* Statement history */}
      <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-x-auto">
        <p className="text-xs font-semibold uppercase tracking-wider text-[#94a3b8] px-4 py-3 border-b border-[#2d3148]">
          Your statements
        </p>
        {shownCell && (
          <p className="flex items-center gap-3 px-4 py-2 text-xs text-[#94a3b8] border-b border-[#2d3148]">
            <span>Showing the statements in {shownCell.label}, {dayjs(shownCell.month).format('MMMM YYYY')}</span>
            <button type="button" onClick={() => setDetail(null)} className="text-[#818cf8] hover:underline">Show all</button>
          </p>
        )}
        {listState.error ? (
          <InlineError what="your statements" error={listState.error} onRetry={loadStatements} className="m-3" />
        ) : !listState.loaded ? (
          <p className="px-4 py-5 text-sm text-[#94a3b8]">Loading…</p>
        ) : statements.length === 0 ? (
          <p className="px-4 py-5 text-sm text-[#94a3b8]">No statements yet - drop one above to get started</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr>
                {['Account', 'Period', 'Balanced', 'Sorted', 'Added'].map(h => (
                  <th key={h} className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#94a3b8] border-b border-[#1e2235]">{h}</th>
                ))}
                <th className="px-4 py-2.5 border-b border-[#1e2235]"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {shownStatements.map(s => (
                <tr key={s.id} className="border-b border-[#1a1d27] hover:bg-[#1a1d27] transition-colors">
                  <td className="px-4 py-3 text-[#e2e8f0] min-w-[16rem]">
                    {editingId === s.id ? (
                      <NameField
                        initial={s.account_label}
                        onSave={name => saveEditName(s, name)}
                        onCancel={() => endEditName(s.id)}
                      />
                    ) : (
                      <div className="flex items-center gap-2">
                        <FileText size={14} className="text-[#818cf8] shrink-0" />
                        <span>{s.account_label}</span>
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-[#94a3b8]">{statementSpan(s)}</td>
                  <td className="px-4 py-3">
                    <BalancedBadge check={s.balance_check} />
                  </td>
                  <td className="px-4 py-3">
                    <Tooltip content="Payments sorted without needing you. The rest are waiting for you in Teach Me.">
                      <CoverageBadge done={s.sorted_count} total={s.txn_count} />
                    </Tooltip>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-[#94a3b8]">{formatStoredTime(s.uploaded_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-2">
                      <Tooltip content="Sort this statement again, for example after connecting a model">
                        <button
                          onClick={() => autoAnnotate(s)}
                          disabled={annotatingId === s.id || job != null}
                          className="flex items-center gap-1 text-[#94a3b8] hover:text-[#a5b4fc] text-xs disabled:opacity-50 transition-colors"
                        >
                          <Zap size={13} />
                          {annotatingId === s.id ? 'Sorting…' : 'Sort again'}
                        </button>
                      </Tooltip>
                      <ActionMenu
                        label={`More for ${s.account_label} - ${statementSpan(s)}`}
                        triggerRef={node => { moreButtons.current[s.id] = node }}
                        items={[
                          { label: 'Name this account', onSelect: () => setEditingId(s.id) },
                          // Only when there is somewhere else to put it.
                          ...(canMove(s)
                            ? [{ label: 'Move to account…', onSelect: () => setMoveTarget(s) }]
                            : []),
                          { label: 'Start over', onSelect: () => setResetTarget(s) },
                          { label: 'Delete', onSelect: () => setDeleteTarget(s), danger: true },
                        ]}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Modal open={!!moveTarget} title="Move to account" onClose={() => setMoveTarget(null)}>
        <p className="text-sm text-[#94a3b8] mb-4">
          {moveTarget && `${statementSpan(moveTarget)} is in ${moveTarget.account_label}. Move it to:`}
        </p>
        <ul className="space-y-2">
          {moveTarget && accounts.filter(a => a.id !== moveTarget.account_id).map(a => (
            <li key={a.id}>
              <button
                type="button"
                onClick={() => moveStatement(a)}
                className="w-full text-left px-3 py-2 rounded-md bg-[#1e2235] text-sm text-[#e2e8f0] hover:bg-[#252a40] transition-colors"
              >
                {a.account_label}
              </button>
            </li>
          ))}
        </ul>
        {moveTarget && canSplit(moveTarget) && (
          <NewAccountForm onMove={name => moveStatement({ name })} />
        )}
      </Modal>

      <ConfirmDialog
        open={!!resetTarget}
        title="Start over with this statement?"
        description={`This removes every category and tag on "${resetTarget?.account_label} - ${spanOf(resetTarget)}". Your payments stay, and you can sort them again any time. This can't be undone.`}
        confirmLabel="Start over"
        onConfirm={resetStatementData}
        onCancel={() => setResetTarget(null)}
        danger
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete this statement?"
        description={deleteDescription(deleteTarget)}
        confirmLabel="Delete"
        onConfirm={deleteStatement}
        onCancel={() => setDeleteTarget(null)}
        danger
      />
    </div>
  )
}
