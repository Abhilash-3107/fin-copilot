import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Copy, Sparkles, Undo2 } from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { annotationSummary } from '../lib/annotationSummary.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { useAnnotationJob } from '../contexts/AnnotationJobContext.jsx'
import Amount, { formatRupees } from '../components/Amount.jsx'
import SortCard from '../components/SortCard.jsx'
import { groupName } from '../components/PayeeHeading.jsx'
import { resetCategoryPickerCache } from '../components/CategoryPicker.jsx'
import { labelOf } from '../lib/categoryOptions.js'
import { count } from '../lib/plural.js'

// Module-level category cache
let _catCache = null

function PropagateDialog({ propagate, onApply, onDismiss, applying }) {
  const { label, items } = propagate
  const [selected, setSelected] = useState(() => new Set(items.map(i => i.transaction_id)))

  function toggle(id) {
    setSelected(s => {
      const next = new Set(s)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 px-4">
      <div className="bg-[#13151f] border border-[#2d3148] rounded-xl w-full max-w-lg max-h-[80vh] flex flex-col">
        <div className="px-5 py-4 border-b border-[#2d3148]">
          <div className="flex items-center gap-2 text-[#e2e8f0] text-sm font-semibold">
            <Copy size={15} className="text-[#818cf8]" />
            Apply "{label}" to similar transactions?
          </div>
          <p className="text-xs text-[#94a3b8] mt-1">
            These look like the same counterparty and were labeled by the AI. Untick any that are different.
          </p>
        </div>
        <div className="overflow-y-auto px-5 py-3 space-y-2 flex-1">
          {items.map(it => (
            <label
              key={it.transaction_id}
              className="flex items-start gap-2.5 text-xs cursor-pointer group"
            >
              <input
                type="checkbox"
                checked={selected.has(it.transaction_id)}
                onChange={() => toggle(it.transaction_id)}
                className="mt-0.5 accent-[#6366f1]"
              />
              <span className="flex-1 min-w-0">
                <span className="block text-[#cbd5e1] truncate">{it.payee}</span>
                <span className="text-[#64748b]">
                  {dayjs(it.txn_date).format('DD MMM')} · <Amount value={it.amount} debitCredit={it.debit_credit} /> · currently {it.category}
                  {it.similarity != null && ` · ${Math.round(it.similarity * 100)}% similar`}
                </span>
              </span>
            </label>
          ))}
        </div>
        <div className="px-5 py-4 border-t border-[#2d3148] flex gap-3">
          <button
            onClick={() => onApply([...selected])}
            disabled={applying || selected.size === 0}
            className="flex-1 py-2 rounded-lg bg-[#6366f1] text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {applying ? 'Applying…' : `Apply to ${selected.size} transaction${selected.size !== 1 ? 's' : ''}`}
          </button>
          <button
            onClick={onDismiss}
            className="px-4 py-2 rounded-lg border border-[#2d3148] text-[#94a3b8] text-sm hover:text-[#e2e8f0] transition-colors"
          >
            No thanks
          </button>
        </div>
      </div>
    </div>
  )
}

// The amount ceiling a suggested policy starts with; editable in Settings > Rules.
const POLICY_DEFAULT_MAX = 500

// What the server says is waiting: payments with no real category, guesses to
// check, and payments the user set aside (of which set_aside_unsorted have no
// real category either).
const NO_COUNTS = { unsorted: 0, guesses: 0, set_aside: 0, set_aside_unsorted: 0 }

const UNDO_URL = '/annotations/review-queue/undo'

function statsLine(stats) {
  return [
    `${stats.confirmed} confirmed`,
    `${stats.edited} corrected`,
    stats.sorted > 0 && `${stats.sorted} sorted by hand`,
    `${stats.skipped} skipped`,
  ].filter(Boolean).join(' · ')
}

// A guess of Uncategorized, or no guess at all, is not an answer to vouch for.
function isRealCategory(category) {
  return !!category && category !== 'Uncategorized'
}

const tagsOf = item => (Array.isArray(item.tags) ? item.tags : (item.tags ? item.tags.split(',').filter(Boolean) : []))
const sameLabel = (category, subcategory, item) => category === (item.category ?? '') && (subcategory || '') === (item.subcategory ?? '')
const ruleId = rule => `${rule.counterparty_key}|${rule.direction}`
// Keys of an id-keyed map, minus these ids (ids are numbers or strings; object keys are always strings).
function without(map, ids) {
  const drop = new Set(ids.map(String))
  return Object.fromEntries(Object.entries(map).filter(([id]) => !drop.has(id)))
}

// A key typed into a field is text, not a shortcut. The group checkbox is the
// one field that leaves the shortcuts live.
function isTyping(target) {
  const tag = target.tagName
  return (tag === 'INPUT' && target.type !== 'checkbox') || tag === 'SELECT' || tag === 'TEXTAREA' || target.isContentEditable
}

function sortedMessage(group, category, subcategory, alsoSorted) {
  const where = labelOf({ category, subcategory })
  const base = group.length > 1
    ? `Sorted as ${where}, and ${group.length - 1} more from ${groupName(group[0])}`
    : `Sorted as ${where}`
  if (!alsoSorted) return base
  return `${base}. ${count(alsoSorted, 'similar payment')} ${alsoSorted === 1 ? 'was' : 'were'} sorted too.`
}

export default function ReviewQueue() {
  const toast = useToast()
  const { job, startAnnotation } = useAnnotationJob()
  // What is still waiting, first card first. Answered and skipped cards leave it.
  const [queue, setQueue] = useState([])
  const [loading, setLoading] = useState(true)
  // True once the server has confirmed the queue is empty; the done states are
  // only shown then, never from the local list alone.
  const [verified, setVerified] = useState(false)
  const [hasTransactions, setHasTransactions] = useState(true)
  // Set when loading the queue fails, so a failed load is never read as "done".
  const [loadError, setLoadError] = useState(null)
  const [retrying, setRetrying] = useState(false)
  const [sorting, setSorting] = useState(false)
  // True once an automatic sorting run has finished while this page was open:
  // payments still bare after it are beyond what it can do.
  const [sortRan, setSortRan] = useState(false)
  const [aiActive, setAiActive] = useState(false)
  const [tree, setTree] = useState(_catCache ?? [])
  const [form, setForm] = useState({ category: '', subcategory: '', tags: [] })
  const [saving, setSaving] = useState(false)
  // The keyboard can fire again before `saving` re-renders (a quick double
  // press of Enter), so the in-flight check reads a ref, not the state.
  const savingRef = useRef(false)
  function beginSave() {
    if (savingRef.current) return false
    savingRef.current = true
    setSaving(true)
    return true
  }
  function endSave() {
    savingRef.current = false
    setSaving(false)
  }
  // Per-card outcome keyed by transaction_id ('confirmed' | 'edited' | 'sorted'
  // | 'skipped'). Stats and progress are derived from this, so a payment answered
  // twice (after an undo) is counted once, in its latest bucket.
  const [outcomes, setOutcomes] = useState({})
  // The answers that can still be taken back, latest last. A ref, because the
  // toast's Undo button and the Z key must see the current stack, not the one
  // from the render they were made in.
  const undoRef = useRef([])
  const [undoCount, setUndoCount] = useState(0)
  const [devMode, setDevMode] = useState(false)
  const [propagate, setPropagate] = useState(null) // {annotationId, label, items}
  const [applying, setApplying] = useState(false)
  const [counts, setCounts] = useState(NO_COUNTS)
  const snoozedCount = counts.set_aside
  // One answer for every queued payment to the same payee: on for businesses,
  // off for people (a person is paid for different things).
  const [applyToGroup, setApplyToGroup] = useState(true)
  // Learned rules when the page opened, so the finish line can say how many
  // payees this session taught the app to sort on its own.
  const baselineRules = useRef(null)
  const [newRules, setNewRules] = useState(null)
  // Policy suggestion: after the user labels a few small payments to people the
  // same way, offer to make it a standing policy.
  const [hasPolicy, setHasPolicy] = useState(true)
  const [personalLabels, setPersonalLabels] = useState({})
  const [policySuggestion, setPolicySuggestion] = useState(null)
  const [policyDismissed, setPolicyDismissed] = useState(false)
  const cardRef = useRef(null)
  const typeaheadRef = useRef(null)

  // Skips are written in order and the done check waits for them, so a card
  // skipped a moment ago never comes back from the server's list.
  const writesRef = useRef(Promise.resolve())

  const current = queue[0]

  // Everything the page shows about the queue comes from the server: the list,
  // how many payments are set aside, and whether there is anything to sort at
  // all. `first` names payments to put at the front (the ones an undo brought back).
  // Resolves to null when the server answered, or to the error message when it
  // did not, so a caller never reports success over a list that did not reload.
  const refresh = useCallback(async ({ first } = {}) => {
    await writesRef.current
    try {
      const [data, nextCounts, anyTxn] = await Promise.all([
        api.get('/annotations/review-queue'),
        api.get('/annotations/review-queue/counts'),
        api.get('/transactions?limit=1'),
      ])
      const front = first ? new Set(first) : null
      setQueue(front ? [...data.filter(it => front.has(it.transaction_id)), ...data.filter(it => !front.has(it.transaction_id))] : data)
      setCounts({ ...NO_COUNTS, ...nextCounts })
      setHasTransactions(!Array.isArray(anyTxn) || anyTxn.length > 0)
      setVerified(data.length === 0)
      setLoadError(null)
      return null
    } catch (e) {
      // Never "done": the server was not asked successfully, so nothing is known.
      toast(`Couldn't load the review list - ${e.message}`, 'error')
      setLoadError(e.message || 'Something went wrong')
      return e.message || 'Something went wrong'
    }
  }, [toast])

  useEffect(() => {
    refresh().finally(() => setLoading(false))

    if (!_catCache) {
      api.get('/categories').then(data => {
        _catCache = data
        setTree(data)
      }).catch(() => {})
    }

    // Fetch fresh each mount so a toggle on the Settings page takes effect.
    api.get('/config').then(cfg => {
      setDevMode(!!cfg.dev_mode)
      setAiActive(!!cfg.ai_active)
    }).catch(() => {})

    api.get('/policies')
      .then(ps => setHasPolicy(Array.isArray(ps) && ps.some(p => p.enabled)))
      .catch(() => {})

    api.get('/annotations/learned-rules')
      .then(rules => { if (Array.isArray(rules)) baselineRules.current = new Set(rules.map(ruleId)) })
      .catch(() => {})
  }, [refresh])

  // The counts (set aside, still waiting) follow the server after every answer
  // and skip, never a local tally that an answer to a skipped card could leave stale.
  const syncCounts = useCallback(() => {
    return api.get('/annotations/review-queue/counts')
      .then(r => setCounts({ ...NO_COUNTS, ...r }))
      .catch(() => {})
  }, [])

  // Out of cards locally: ask the server before saying so. Anything still
  // waiting there (new rows, a re-decided label, the next page of a long queue)
  // continues the session. A failed ask stops here instead of looping; Retry asks again.
  const outOfCards = queue.length === 0
  useEffect(() => {
    if (loading) return
    if (!outOfCards) setVerified(false)
    else if (!verified && !loadError) refresh()
  }, [loading, outOfCards, verified, loadError, refresh])

  // A sorting run that finishes while this page is open (started here or on
  // another page) changes what is waiting: start again from the server's list.
  const jobRunning = job != null
  const jobWasRunning = useRef(false)
  useEffect(() => {
    if (jobRunning) { jobWasRunning.current = true; return }
    if (!jobWasRunning.current) return
    jobWasRunning.current = false
    setSortRan(true)
    refresh()
  }, [jobRunning, refresh])

  // Sync the form when the card changes, and decide whether an answer covers
  // the whole payee: yes for a business, no for a person.
  useEffect(() => {
    if (!current) return
    setForm({
      category: current.category ?? '',
      subcategory: current.subcategory ?? '',
      tags: tagsOf(current),
    })
    setApplyToGroup(current.payee_kind !== 'person')
    // Re-sync the form only when the card identity changes, not on every render
    // where `current` is a fresh object from a new queue array - otherwise an
    // in-progress edit would be clobbered mid-keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.transaction_id])

  // A new card takes the keyboard: no chip or button keeps focus from the last
  // card, so Enter cannot activate something the user never chose.
  useEffect(() => {
    if (!loading) cardRef.current?.focus({ preventScroll: true })
  }, [loading, current?.transaction_id])

  // Confirm vouches for the machine's label, so it needs a real one. Anything
  // else (no guess, Uncategorized) must be answered by choosing a category.
  const needsPick = !isRealCategory(form.category)
  const chosen = needsPick ? null : { category: form.category, subcategory: form.subcategory || null }
  // Single source of truth for "has the user changed this card?", for the
  // keyboard and the button alike (a tags-only edit still counts).
  const isDirty = useMemo(() => {
    if (!current) return false
    return (
      form.category !== (current.category ?? '') ||
      form.subcategory !== (current.subcategory ?? '') ||
      JSON.stringify(form.tags) !== JSON.stringify(tagsOf(current))
    )
  }, [current, form])

  // One tally per resolved card: a card re-decided after an undo moves between
  // buckets instead of being counted twice.
  const stats = useMemo(() => {
    const s = { confirmed: 0, edited: 0, sorted: 0, skipped: 0 }
    for (const outcome of Object.values(outcomes)) s[outcome] += 1
    return s
  }, [outcomes])
  const handled = Object.keys(outcomes).length

  // 'edited' corrected a guess; 'sorted' answered a payment that had no real guess.
  function answeredAs(item) {
    return isRealCategory(item.category) ? 'edited' : 'sorted'
  }

  function record(group, outcomeOf) {
    setOutcomes(o => ({ ...o, ...Object.fromEntries(group.map(it => [it.transaction_id, outcomeOf(it)])) }))
  }

  function pushUndo(entry) {
    undoRef.current = [...undoRef.current, entry]
    setUndoCount(undoRef.current.length)
  }

  function popUndo() {
    undoRef.current = undoRef.current.slice(0, -1)
    setUndoCount(undoRef.current.length)
  }

  // The toast that follows an answer: what happened, and a way to take it back.
  function announce(message, res) {
    toast(message, 'success', res.undo_token ? { action: { label: 'Undo', onClick: undoLast } } : undefined)
  }

  // After an answer, cards ahead follow the server: the answered group leaves,
  // and every machine label the server re-decided (same payee, near
  // neighbours) leaves when resolved or shows its new label otherwise, so a
  // later confirm vouches for what is actually stored. Returns how many
  // re-decided cards left (the answered group isn't counted).
  function settle(answered, reevaluated) {
    const done = new Set(answered.map(it => it.transaction_id))
    const byId = new Map((reevaluated ?? []).map(r => [r.transaction_id, r]))
    let left = 0
    const next = queue.flatMap(it => {
      if (done.has(it.transaction_id)) return []
      const r = byId.get(it.transaction_id)
      if (!r) return [it]
      if (r.resolved) { left += 1; return [] }
      const { transaction_id: _id, resolved: _resolved, ...label } = r
      return [{ ...it, ...label }]
    })
    setQueue(next)
    return left
  }

  // The current card plus every later queued payment to the same payee.
  function groupFromHere() {
    return queue.filter(it => it.group_key === current.group_key)
  }

  // Count the distinct people whose small payments the user labels the same
  // way; at three, offer a standing policy instead of asking again. One payee
  // answered for several payments is still one piece of evidence.
  function notePersonalLabel(items, category, subcategory) {
    if (hasPolicy || policyDismissed) return
    const payees = items
      .filter(it => it.payee_kind === 'person' && it.debit_credit === 'debit' && Math.abs(it.amount) <= POLICY_DEFAULT_MAX)
      .map(it => it.group_key)
    if (!payees.length) return
    const key = `${category}|${subcategory || ''}`
    const seen = new Set([...(personalLabels[key] ?? []), ...payees])
    setPersonalLabels(m => ({ ...m, [key]: [...seen] }))
    if (seen.size >= 3) {
      const draft = { max_amount: POLICY_DEFAULT_MAX, category, subcategory: subcategory || null }
      api.post('/policies/preview', draft).then(({ matches }) => setPolicySuggestion({ ...draft, matches }))
    }
  }

  async function createPolicy() {
    const { matches: _matches, ...draft } = policySuggestion
    try {
      const res = await api.post('/policies', draft)
      setHasPolicy(true)
      setPolicySuggestion(null)
      const reloadError = await refresh()
      if (reloadError) {
        toast(`Policy saved, but the list could not be reloaded - ${reloadError}`, 'error')
        return
      }
      toast(
        res.resolved
          ? `Policy saved. It sorted ${res.resolved} waiting transaction${res.resolved === 1 ? '' : 's'}.`
          : 'Policy saved. New small payments to people will be sorted automatically.',
        'success',
      )
    } catch (e) {
      toast(`Couldn't save the policy - ${e.message}`, 'error')
    }
  }

  function skip() {
    if (!current || savingRef.current) return
    const item = current
    record([item], () => 'skipped')
    setQueue(q => q.filter(it => it.transaction_id !== item.transaction_id))
    // Persist the snooze so the skip sticks across sessions (4.15), then take
    // the set-aside count from the server. The card has already left the list;
    // if the write fails it goes back.
    writesRef.current = writesRef.current
      .then(() => api.post('/annotations/review-queue/skip', { transaction_id: item.transaction_id }))
      .then(res => {
        if (res?.undo_token) pushUndo({ tokens: [res.undo_token], ids: [item.transaction_id] })
        toast('Set aside for later', 'info', res?.undo_token ? { action: { label: 'Undo', onClick: undoLast } } : undefined)
      })
      .catch(e => {
        toast(`Couldn't set that aside - ${e.message}`, 'error')
        setOutcomes(o => without(o, [item.transaction_id]))
        setQueue(q => [item, ...q])
      })
      .then(syncCounts)
  }

  // Take back the latest step on the server. A step is one answer, or an answer
  // and the similar payments it was applied to (undone last to first). The card
  // (or cards) come back first, exactly as they were; the progress tally forgets
  // them. When a part is refused the step is final, and the message says what
  // was and was not undone instead of "Undone".
  async function undoLast() {
    if (!beginSave()) return
    try {
      await writesRef.current
      const entry = undoRef.current.at(-1)
      if (!entry) return
      let undone = 0
      let refusal = null
      for (const token of [...entry.tokens].reverse()) {
        try {
          await api.post(UNDO_URL, { token })
          undone += 1
        } catch (e) {
          // Too old, or the payments changed since: this answer is final now.
          refusal = e.message
          break
        }
      }
      popUndo()
      if (undone === 0) {
        toast(refusal, 'error')
        return
      }
      setOutcomes(o => without(o, entry.ids))
      setPropagate(null)
      const reloadError = await refresh({ first: entry.ids })
      if (refusal) toast(`Only part of that was undone - ${refusal}`, 'error')
      else if (reloadError) toast(`Undone, but the list could not be reloaded - ${reloadError}`, 'error')
      else toast('Undone', 'info')
    } finally {
      endSave()
    }
  }

  async function restoreSnoozed() {
    try {
      const { restored } = await api.post('/annotations/review-queue/unsnooze', {})
      // They are back in play, so they no longer count as skipped this session.
      setOutcomes(o => Object.fromEntries(Object.entries(o).filter(([, outcome]) => outcome !== 'skipped')))
      await refresh()
      toast(`Restored ${restored} skipped ${restored === 1 ? 'item' : 'items'}`, 'info')
    } catch (e) {
      toast(`Couldn't restore skipped items - ${e.message}`, 'error')
    }
  }

  async function confirm() {
    if (!current || !isRealCategory(current.category) || !beginSave()) return
    try {
      await writesRef.current // a skip still being written is part of the order of events
      const group = applyToGroup ? groupFromHere() : [current]
      const label = { category: current.category, subcategory: current.subcategory || null }
      const res = group.length > 1
        // One answer for the whole group: the label on this card, as seen.
        ? await api.post('/annotations/bulk-label', { transaction_ids: group.map(it => it.transaction_id), label })
        : await api.post(`/annotations/${current.annotation_id}/confirm`, { category: current.category })
      record(group, () => 'confirmed')
      if (res.undo_token) pushUndo({ tokens: [res.undo_token], ids: group.map(it => it.transaction_id) })
      const alsoSorted = settle(group, res.reevaluated)
      announce(sortedMessage(group, label.category, label.subcategory, alsoSorted), res)
      notePersonalLabel(group, label.category, label.subcategory)
      syncCounts()
    } catch (e) {
      if (e.status === 409) {
        // The label changed under this card (new evidence settled it); show
        // the current queue instead of confirming something unseen.
        toast(`${e.message}. Here's the updated list.`, 'info')
        await refresh()
        return
      }
      toast(`Something went wrong - ${e.message}`, 'error')
    } finally {
      endSave()
    }
  }

  async function saveEdit() {
    if (needsPick) { toast('Pick a category first', 'error'); return }
    if (!beginSave()) return
    try {
      await writesRef.current
      const label = {
        category: form.category,
        subcategory: form.subcategory || null,
        tags: form.tags,
      }
      const group = applyToGroup ? groupFromHere() : [current]
      // The server decides how each payment is labelled: a plain manual label
      // where nothing has labelled it, a correction where a sorting run got
      // there first. So a bare card never trips over an annotation that
      // appeared since the list loaded.
      const res = await api.post('/annotations/bulk-label', { transaction_ids: group.map(it => it.transaction_id), label })
      record(group, answeredAs)
      if (res.undo_token) pushUndo({ tokens: [res.undo_token], ids: group.map(it => it.transaction_id) })
      const alsoSorted = settle(group, res.reevaluated)
      announce(sortedMessage(group, label.category, label.subcategory, alsoSorted), res)
      notePersonalLabel(group, label.category, label.subcategory)
      syncCounts()
      // One correction can fix many: offer the same label for similar
      // machine-annotated transactions (best-effort; never blocks the save).
      // A payment with no annotation has no neighbours to compare with; its
      // payee group already covers that need.
      if (!current.annotation_id) return
      try {
        const similar = await api.get(`/annotations/${current.annotation_id}/similar`)
        const fixable = similar.filter(s => s.differs)
        if (fixable.length > 0) {
          setPropagate({
            answerToken: res.undo_token ?? null,
            annotationId: current.annotation_id,
            label: labelOf({ category: label.category, subcategory: label.subcategory }),
            items: fixable,
          })
        }
      } catch { /* similar lookup is optional */ }
    } catch (e) {
      toast(`Something went wrong - ${e.message}`, 'error')
    } finally {
      endSave()
    }
  }

  // Choose a category for the current card: a suggestion, or a typed pick. The
  // keyboard is handed back to the card so Enter answers it.
  function choose({ category, subcategory }, event) {
    if (!current) return
    const own = sameLabel(category, subcategory, current)
    setForm(f => ({ ...f, category, subcategory: subcategory || '', tags: own ? tagsOf(current) : [] }))
    if (event?.detail > 0) cardRef.current?.focus({ preventScroll: true })
  }

  // Create a category (parentId null) or subcategory, add it to the shared
  // trees, and choose it for the current card. Returns the node, or null on failure.
  async function handleCreateCategory(parentId, name) {
    try {
      const created = parentId
        ? await api.post('/categories/subcategories', { parent_id: parentId, name })
        : await api.post('/categories', { name })
      const next = [...(_catCache ?? tree), created]
      _catCache = next
      resetCategoryPickerCache()
      setTree(next)
      const parentName = parentId ? next.find(c => c.id === parentId)?.name : created.name
      choose({ category: parentName, subcategory: parentId ? created.name : '' })
      return created
    } catch (e) {
      toast(`Couldn't add "${name}" - ${e.message}`, 'error')
      return null
    }
  }

  // Keyboard shortcuts. The handler closes over queue/form state, so instead
  // of a hand-maintained dep list (which has silently gone stale in this file
  // before) keep the latest action in a ref and bind the listener exactly once.
  const keyActionRef = useRef(null)
  keyActionRef.current = e => {
    // Letters typed into the category filter or any other field are text.
    if (isTyping(e.target)) return
    // "/" is the one key some layouts type with Shift; every other shortcut is a bare key.
    if (e.ctrlKey || e.metaKey || e.altKey || (e.shiftKey && e.key !== '/')) return
    // A held key must not skip, sort or undo a card a repeat at a time.
    if (e.repeat) return
    // Enter on a focused button or link is that control's own activation: the
    // browser clicks it, so the shortcut must not also act (AUDIT B13).
    if (e.key === 'Enter' && e.target.closest?.('button, a')) return
    if (propagate) return // dialog owns the keyboard
    const key = e.key.toLowerCase()
    if (key === 'z') { undoLast(); return }
    if (!current) return
    if (key === 's') skip()
    else if (key === 'a') setApplyToGroup(v => !v)
    else if (e.key === '/') { e.preventDefault(); typeaheadRef.current?.focus() }
    else if (['1', '2', '3'].includes(e.key)) {
      const suggestion = current.suggestions?.[Number(e.key) - 1]
      if (suggestion && !savingRef.current) choose(suggestion)
    } else if (e.key === 'Enter') {
      // With no real category on the card there is nothing to confirm or save.
      if (needsPick) return
      isDirty ? saveEdit() : confirm()
    }
  }
  useEffect(() => {
    const handler = e => keyActionRef.current?.(e)
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // How many payees this session taught the app to sort on its own: the
  // learned rules there are now that there were not when the page opened.
  useEffect(() => {
    if (!verified || handled === 0 || !baselineRules.current) return undefined
    let stale = false
    api.get('/annotations/learned-rules')
      .then(rules => {
        if (!stale && Array.isArray(rules)) setNewRules(rules.filter(r => !baselineRules.current.has(ruleId(r))).length)
      })
      .catch(() => {})
    return () => { stale = true }
  }, [verified, handled])

  // Payments no sorting run has touched yet (a statement added since, or a run
  // that failed): they are in the queue and can be sorted by hand, or the
  // automatic pass can take a first look at them. The queue reloads when the
  // run ends, whoever started it (the job effect above).
  async function sortAutomatically() {
    setSorting(true)
    try {
      const result = await startAnnotation({})
      const summary = annotationSummary(result)
      toast(summary.message, summary.type)
      setSortRan(true)
    } catch (e) {
      toast(`Sorting failed - ${e.message}`, 'error')
    } finally {
      setSorting(false)
    }
  }

  async function applyPropagation(transactionIds) {
    setApplying(true)
    try {
      const res = await api.post(`/annotations/${propagate.annotationId}/apply-to-similar`, {
        transaction_ids: transactionIds,
      })
      // Z undoes the answer and these together: one step, not two.
      if (res.undo_token) {
        const last = undoRef.current.at(-1)
        if (propagate.answerToken && last?.tokens.includes(propagate.answerToken)) {
          undoRef.current = [...undoRef.current.slice(0, -1), {
            tokens: [...last.tokens, res.undo_token], ids: [...new Set([...last.ids, ...transactionIds])],
          }]
          setUndoCount(undoRef.current.length)
        } else {
          pushUndo({ tokens: [res.undo_token], ids: [...transactionIds] })
        }
      }
      toast(
        `Fixed ${res.applied} similar transaction${res.applied !== 1 ? 's' : ''}`,
        'success',
        res.undo_token ? { action: { label: 'Undo', onClick: undoLast } } : undefined,
      )
      // Applied rows are now human-labeled - drop any still ahead in the queue.
      const appliedSet = new Set(transactionIds)
      setQueue(q => q.filter(it => !appliedSet.has(it.transaction_id)))
    } catch (e) {
      toast(`Couldn't apply - ${e.message}`, 'error')
    } finally {
      setApplying(false)
      setPropagate(null)
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-full text-[#475569]">Getting your review list ready…</div>
  }

  const propagateDialog = propagate && (
    <PropagateDialog
      propagate={propagate}
      onApply={applyPropagation}
      onDismiss={() => setPropagate(null)}
      applying={applying}
    />
  )

  if (outOfCards && loadError) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-6" role="alert">
        <h2 className="text-lg font-semibold text-[#e2e8f0]">Couldn't load your review list</h2>
        <p className="text-sm text-[#94a3b8] max-w-sm">{loadError}</p>
        <button
          disabled={retrying}
          onClick={async () => {
            setRetrying(true)
            await refresh()
            setRetrying(false)
          }}
          className="rounded-lg bg-[#6366f1] px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {retrying ? 'Retrying…' : 'Retry'}
        </button>
      </div>
    )
  }

  if (outOfCards && !verified) {
    return (
      <div className="flex items-center justify-center h-full text-[#475569]">
        {propagateDialog}
        Checking for anything left…
      </div>
    )
  }

  if (outOfCards) {
    const sortedCount = stats.confirmed + stats.edited + stats.sorted
    const awayUnsorted = counts.set_aside_unsorted
    const awayAll = snoozedCount
    const setAside = (
      snoozedCount > 0 && (
        <button
          onClick={restoreSnoozed}
          className="text-sm text-[#a5b4fc] hover:text-[#c7d2fe] underline underline-offset-2 transition-colors"
        >
          Review {snoozedCount === 1 ? 'it' : 'them'} again
        </button>
      )
    )
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-6">
        {propagateDialog}
        {!hasTransactions ? (
          <>
            <h2 className="text-lg font-semibold text-[#e2e8f0]">Nothing to sort yet</h2>
            <p className="text-sm text-[#94a3b8] max-w-sm">
              Once you add a statement, any payment that needs a decision shows up here, one card at a time.
            </p>
            <Link
              to="/upload"
              className="rounded-lg bg-[#6366f1] px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90"
            >
              Add a statement
            </Link>
          </>
        ) : snoozedCount > 0 ? (
          <>
            <h2 className="text-lg font-semibold text-[#e2e8f0]">
              {snoozedCount} {snoozedCount === 1 ? 'payment is' : 'payments are'} set aside
            </h2>
            <p className="text-sm text-[#94a3b8]">
              {awayUnsorted === awayAll
                ? `You skipped ${awayAll === 1 ? 'it' : 'them'}, so ${awayAll === 1 ? 'it is' : 'they are'} still not sorted.`
                : awayUnsorted === 0
                  ? `You skipped ${awayAll === 1 ? 'it' : 'them'}, so ${awayAll === 1 ? 'it is' : 'they are'} still unchecked.`
                  : `You skipped ${awayAll === 1 ? 'it' : 'them'}: ${awayUnsorted} still not sorted, the rest unchecked.`}
            </p>
            {setAside}
          </>
        ) : (
          <>
            <div className="text-4xl">✓</div>
            <h2 className="text-lg font-semibold text-[#e2e8f0]">You're all done!</h2>
            <p className="text-sm text-[#94a3b8]">
              {sortedCount > 0
                ? `You sorted ${count(sortedCount, 'payment')}.`
                : 'Everything is sorted. Nothing to review right now.'}
            </p>
            {newRules > 0 && (
              <p className="text-sm text-[#94a3b8]">
                {count(newRules, 'payee')} will now be sorted {newRules === 1 ? 'on its own' : 'on their own'} from here.
              </p>
            )}
          </>
        )}
        {handled > 0 && <p className="text-xs text-[#64748b] mt-1">{statsLine(stats)}</p>}
        <div className="mt-2 flex items-center gap-3">
          {hasTransactions && (
            <Link
              to="/dashboard"
              className="rounded-lg bg-[#6366f1] px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90"
            >
              Back to the Dashboard
            </Link>
          )}
          {undoCount > 0 && (
            <button
              onClick={undoLast}
              disabled={saving}
              className="flex items-center gap-2 text-sm text-[#94a3b8] hover:text-[#e2e8f0] border border-[#2d3148] px-4 py-2 rounded-lg transition-colors disabled:opacity-50"
            >
              <Undo2 size={15} /> Undo <kbd className="text-[10px] opacity-70">Z</kbd>
            </button>
          )}
        </div>
      </div>
    )
  }

  const total = handled + queue.length
  const groupSize = groupFromHere().length
  const payeesLeft = new Set(queue.map(it => it.group_key)).size
  const unannotatedLeft = queue.filter(it => !it.annotation_id).length
  // The server's count of everything waiting; the list it sent may be shorter.
  const waiting = counts.unsorted + counts.guesses
  // The run can only help when a model is there to ask, and not after one has
  // already left these payments bare.
  const canSort = aiActive && !sortRan
  const suggestionKeys = Math.min(current.suggestions?.length ?? 0, 3)

  return (
    <div className="flex flex-col h-full px-4 py-5">
      {propagateDialog}
      {/* Progress */}
      <div className="max-w-3xl mx-auto w-full mb-5">
        <div className="flex justify-between text-xs text-[#64748b] mb-1.5">
          <span>Teach Me</span>
          <span>{handled} / {total}</span>
        </div>
        <div className="h-1.5 bg-[#1e2235] rounded-full overflow-hidden">
          <div
            className="h-full bg-[#6366f1] rounded-full transition-all"
            style={{ width: `${(handled / total) * 100}%` }}
          />
        </div>
        <p className="text-[11px] text-[#64748b] mt-1">
          {statsLine(stats)}
          {payeesLeft > 0 && ` · ${payeesLeft} payee${payeesLeft === 1 ? '' : 's'} left`}
          {waiting > queue.length && ` · showing the first ${queue.length} of ${waiting} waiting`}
        </p>
      </div>

      {unannotatedLeft > 0 && (
        <div className="max-w-3xl mx-auto w-full mb-4 flex items-center gap-4 rounded-xl border border-[#2d3148] bg-[#13151f] px-5 py-3">
          <p className="flex-1 text-sm text-[#cbd5e1]">
            {job != null || sorting
              ? 'Automatic sorting is running. Payments it settles will leave this list.'
              : `${unannotatedLeft} ${unannotatedLeft === 1 ? 'payment has' : 'payments have'} no guess yet. Pick a category for ${unannotatedLeft === 1 ? 'it' : 'each'} below${canSort ? ', or run automatic sorting.' : '.'}`}
          </p>
          {canSort && job == null && !sorting && (
            <button
              onClick={sortAutomatically}
              className="shrink-0 inline-flex items-center gap-1.5 rounded-lg border border-[#2d3148] px-3 py-1.5 text-xs font-medium text-[#e2e8f0] transition-colors hover:border-[#6366f1]"
            >
              <Sparkles size={13} /> Sort my transactions
            </button>
          )}
        </div>
      )}

      {policySuggestion && (
        <div className="max-w-3xl mx-auto w-full mb-4 bg-[#1e1b4b]/60 border border-[#4f46e5]/40 rounded-xl px-5 py-3.5 flex items-center gap-4">
          <p className="flex-1 text-sm text-[#c7d2fe]">
            Sort small payments (up to {formatRupees(POLICY_DEFAULT_MAX, { decimals: 0 })}) to people who aren't in your People list as{' '}
            <span className="font-semibold text-[#e0e7ff]">
              {policySuggestion.subcategory ? `${policySuggestion.category} › ${policySuggestion.subcategory}` : policySuggestion.category}
            </span>{' '}
            automatically?
            {policySuggestion.matches > 0 && (
              <span className="text-[#a5b4fc]">
                {' '}It would sort {policySuggestion.matches} waiting transaction{policySuggestion.matches === 1 ? '' : 's'} now.
              </span>
            )}
          </p>
          <button
            onClick={createPolicy}
            className="shrink-0 px-3 py-1.5 rounded-lg bg-[#6366f1] text-white text-xs font-medium hover:opacity-90 transition-opacity"
          >
            Yes, always
          </button>
          <button
            onClick={() => { setPolicySuggestion(null); setPolicyDismissed(true) }}
            className="shrink-0 text-xs text-[#94a3b8] hover:text-[#e2e8f0] transition-colors"
          >
            Not now
          </button>
        </div>
      )}

      <SortCard
        item={current}
        form={form}
        setForm={setForm}
        tree={tree}
        chosen={chosen}
        needsPick={needsPick}
        saving={saving}
        canUndo={undoCount > 0}
        devMode={devMode}
        groupSize={groupSize}
        applyToGroup={applyToGroup}
        onApplyToGroup={setApplyToGroup}
        onChoose={choose}
        onCreateCategory={handleCreateCategory}
        onConfirm={() => (isDirty ? saveEdit() : confirm())}
        onSkip={skip}
        onUndo={undoLast}
        cardRef={cardRef}
        typeaheadRef={typeaheadRef}
      />

      <p className="text-center text-xs text-[#64748b] mt-4">
        Keyboard:
        {suggestionKeys > 0 && (
          <>
            {' '}{Array.from({ length: suggestionKeys }, (_, i) => (
              <kbd key={i} className="bg-[#1e2235] px-1.5 py-0.5 rounded mr-1">{i + 1}</kbd>
            ))}
            choose ·
          </>
        )}
        {!needsPick && (
          <>
            <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded mx-1">↵</kbd> confirm ·
          </>
        )}
        <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded mx-1">S</kbd> skip ·
        <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded mx-1">Z</kbd> undo ·
        <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded mx-1">/</kbd> another category
        {groupSize > 1 && (
          <> · <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded">A</kbd> all from this payee</>
        )}
      </p>
    </div>
  )
}
