import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle, SkipForward, Tag, ChevronDown, ChevronUp, Undo2, Copy, Plus, Sparkles } from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { annotationSummary } from '../lib/annotationSummary.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { useAnnotationJob } from '../contexts/AnnotationJobContext.jsx'
import TagInput from '../components/TagInput.jsx'
import { SOURCE_PILL, sourceLabel } from '../lib/sources.js'
import Amount from '../components/Amount.jsx'
import Tooltip from '../components/Tooltip.jsx'
import ReasoningPanel from '../components/ReasoningPanel.jsx'
import { resetCategoryPickerCache } from '../components/CategoryPicker.jsx'

// Module-level category cache
let _catCache = null

function ConfidenceBar({ confidence }) {
  const pct = Math.round((confidence ?? 0) * 100)
  const color = confidence >= 0.85 ? 'bg-green-500' : confidence >= 0.6 ? 'bg-amber-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 bg-[#1e2235] rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-[#94a3b8] w-8 text-right">{pct}%</span>
    </div>
  )
}

// Inline "name it" field for a new category or subcategory. Enter saves, Escape cancels.
function NewNameInput({ placeholder, onSubmit, onCancel }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit() {
    const value = name.trim()
    if (!value || busy) return
    setBusy(true)
    const ok = await onSubmit(value)
    if (!ok) setBusy(false)
  }

  return (
    <input
      autoFocus
      value={name}
      disabled={busy}
      placeholder={placeholder}
      aria-label={placeholder}
      maxLength={60}
      onChange={e => setName(e.target.value)}
      onKeyDown={e => {
        if (e.key === 'Enter') { e.preventDefault(); submit() }
        if (e.key === 'Escape') { e.preventDefault(); onCancel() }
      }}
      onBlur={() => { if (!name.trim() && !busy) onCancel() }}
      className="w-40 bg-[#0f1117] border border-[#6366f1] text-[#e2e8f0] px-2.5 py-1 rounded-full text-xs focus:outline-none placeholder:text-[#475569]"
    />
  )
}

const NEW_CHIP_CLASS =
  'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs border border-dashed border-[#3d4468] text-[#64748b] hover:border-[#6366f1] hover:text-[#a5b4fc] transition-colors'

function CategoryChips({ tree, category, subcategory, onChange, onCreate }) {
  const [adding, setAdding] = useState(null) // 'category' | 'subcategory' | null
  const roots = useMemo(() => tree.filter(c => !c.parent_id), [tree])
  const parent = useMemo(() => (category ? roots.find(r => r.name === category) : null), [roots, category])
  const children = useMemo(() => (parent ? tree.filter(c => c.parent_id === parent.id) : []), [tree, parent])

  async function create(kind, name) {
    const created = await onCreate(kind === 'category' ? null : parent.id, name)
    if (created) setAdding(null)
    return !!created
  }

  return (
    <div className="space-y-2.5">
      {/* Category chips */}
      <div className="flex flex-wrap items-center gap-1.5">
        {roots.map(r => (
          <button
            key={r.id}
            onClick={() => onChange({ category: r.name, subcategory: '' })}
            className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
              category === r.name
                ? 'bg-[#6366f1] text-white'
                : 'bg-[#1e2235] text-[#94a3b8] hover:bg-[#2d3148] hover:text-[#e2e8f0]'
            }`}
          >
            {r.name}
          </button>
        ))}
        {adding === 'category' ? (
          <NewNameInput
            placeholder="New category name"
            onSubmit={name => create('category', name)}
            onCancel={() => setAdding(null)}
          />
        ) : (
          <button onClick={() => setAdding('category')} className={NEW_CHIP_CLASS}>
            <Plus size={11} /> New category
          </button>
        )}
      </div>

      {/* Subcategory chips — appear inline when a category is selected */}
      {parent && (
        <div className="flex flex-wrap items-center gap-1.5 pl-1 border-l-2 border-[#6366f1]/30">
          {children.map(c => (
            <button
              key={c.id}
              onClick={() => onChange({ category, subcategory: c.name })}
              className={`px-2 py-0.5 rounded-full text-xs transition-colors ${
                subcategory === c.name
                  ? 'bg-[#4f46e5] text-white'
                  : 'bg-[#1e1b4b] text-[#818cf8] hover:bg-[#2d2b6b] hover:text-[#a5b4fc]'
              }`}
            >
              {c.name}
            </button>
          ))}
          {adding === 'subcategory' ? (
            <NewNameInput
              placeholder={`New subcategory in ${category}`}
              onSubmit={name => create('subcategory', name)}
              onCancel={() => setAdding(null)}
            />
          ) : (
            <button onClick={() => setAdding('subcategory')} className={NEW_CHIP_CLASS}>
              <Plus size={11} /> New subcategory
            </button>
          )}
        </div>
      )}
    </div>
  )
}

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
                <span className="block text-[#cbd5e1] truncate">{it.raw_description}</span>
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

// A guess of Uncategorized, or no guess at all, is not an answer to vouch for.
function isRealCategory(category) {
  return !!category && category !== 'Uncategorized'
}

function withResolved(message, sorted) {
  if (!sorted) return message
  return `${message} Also sorted ${sorted} similar transaction${sorted === 1 ? '' : 's'}.`
}

export default function ReviewQueue() {
  const toast = useToast()
  const { job, startAnnotation } = useAnnotationJob()
  const [queue, setQueue] = useState([])
  const [idx, setIdx] = useState(0)
  const [loading, setLoading] = useState(true)
  // True once the server has confirmed the queue is empty; the done states are
  // only shown then, never from the local list alone.
  const [verified, setVerified] = useState(false)
  const [hasTransactions, setHasTransactions] = useState(true)
  const [sorting, setSorting] = useState(false)
  const [tree, setTree] = useState(_catCache ?? [])
  const [form, setForm] = useState({ category: '', subcategory: '', merchant: '', tags: [] })
  const [showTags, setShowTags] = useState(false)
  const [saving, setSaving] = useState(false)
  // The keyboard can fire again before `saving` re-renders (a quick double
  // press of c or Enter), so the in-flight check reads a ref, not the state.
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
  // Per-card outcome keyed by transaction_id ('confirmed' | 'edited' | 'skipped').
  // Stats are derived from this, so going back to a card and changing your mind
  // re-keys the same entry instead of double-counting — the server already
  // no-ops a repeat confirm (source flips to 'manual', out of _MODEL_SOURCES),
  // and the local counters must match that.
  const [outcomes, setOutcomes] = useState({})
  const [history, setHistory] = useState([]) // stack of visited idx values
  const [devMode, setDevMode] = useState(false)
  const [propagate, setPropagate] = useState(null) // {annotationId, label, items}
  const [applying, setApplying] = useState(false)
  const [snoozedCount, setSnoozedCount] = useState(0)
  // One answer for every queued payment to the same payee (on by default).
  const [applyToGroup, setApplyToGroup] = useState(true)
  // Policy suggestion: after the user labels a few small payments to people the
  // same way, offer to make it a standing policy.
  const [hasPolicy, setHasPolicy] = useState(true)
  const [personalLabels, setPersonalLabels] = useState({})
  const [policySuggestion, setPolicySuggestion] = useState(null)
  const [policyDismissed, setPolicyDismissed] = useState(false)
  const idxRef = useRef(0)
  useEffect(() => { idxRef.current = idx }, [idx])

  // Skips are written in order and the done check waits for them, so a card
  // skipped a moment ago never comes back from the server's list.
  const writesRef = useRef(Promise.resolve())

  // Everything the page shows about the queue comes from the server: the list,
  // how many payments are set aside, and whether there is anything to sort at
  // all. A non-empty list starts the session over from its first card; an empty
  // one keeps the cards already passed (keepCards) so the done screen can still
  // go back and undo the last answer.
  const refresh = useCallback(async ({ keepCards = false } = {}) => {
    await writesRef.current
    try {
      const [data, snoozed, anyTxn] = await Promise.all([
        api.get('/annotations/review-queue'),
        api.get('/annotations/review-queue/snoozed-count'),
        api.get('/transactions?limit=1'),
      ])
      if (data.length > 0 || !keepCards) {
        setQueue(data)
        setIdx(0)
        setHistory([])
      }
      setSnoozedCount(snoozed?.count ?? 0)
      setHasTransactions(!Array.isArray(anyTxn) || anyTxn.length > 0)
      setVerified(data.length === 0)
    } catch (e) {
      toast(`Couldn't load the review list - ${e.message}`, 'error')
      setVerified(true)
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
    }).catch(() => {})

    api.get('/policies')
      .then(ps => setHasPolicy(Array.isArray(ps) && ps.some(p => p.enabled)))
      .catch(() => {})
  }, [refresh])

  // The set-aside count follows the server after every answer and skip, never a
  // local tally that an answer to a skipped card could leave stale.
  const syncSnoozed = useCallback(() => {
    return api.get('/annotations/review-queue/snoozed-count')
      .then(r => setSnoozedCount(r?.count ?? 0))
      .catch(() => {})
  }, [])

  // Out of cards locally: ask the server before saying so. Anything still
  // waiting there (new rows, a re-decided label) continues the session.
  const outOfCards = idx >= queue.length
  useEffect(() => {
    if (loading) return
    if (!outOfCards) setVerified(false)
    else if (!verified) refresh({ keepCards: true })
  }, [loading, outOfCards, verified, refresh])

  const current = queue[idx]

  // Sync form when card changes
  useEffect(() => {
    if (!current) return
    setForm({
      category: current.category ?? '',
      subcategory: current.subcategory ?? '',
      merchant: current.merchant ?? '',
      tags: Array.isArray(current.tags)
        ? current.tags
        : (current.tags ? current.tags.split(',').filter(Boolean) : []),
    })
    setShowTags(false)
    setApplyToGroup(true)
    // Re-sync the form only when the card identity changes, not on every render
    // where `current` is a fresh object from a new queue array — otherwise an
    // in-progress edit would be clobbered mid-keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idx, current?.transaction_id])

  // Single source of truth for "has the user changed this card?"
  // Used by both the keyboard handler and the render body so Enter/c never
  // disagree with the buttons (a merchant- or tags-only edit still counts).
  // Confirm vouches for the machine's label, so it needs a real one. Anything
  // else (no guess, Uncategorized) must be answered by picking a category.
  const needsPick = !isRealCategory(form.category)
  const isDirty = useMemo(() => {
    if (!current) return false
    return (
      form.category !== (current.category ?? '') ||
      form.subcategory !== (current.subcategory ?? '') ||
      form.merchant !== (current.merchant ?? '') ||
      JSON.stringify(form.tags) !== JSON.stringify(
        Array.isArray(current.tags) ? current.tags : (current.tags ? current.tags.split(',').filter(Boolean) : [])
      )
    )
  }, [current, form])

  // Derived stats: one tally per resolved card, so a card revisited via "go
  // back" and re-decided moves between buckets instead of being counted twice.
  const stats = useMemo(() => {
    const s = { confirmed: 0, edited: 0, skipped: 0 }
    for (const outcome of Object.values(outcomes)) s[outcome] += 1
    return s
  }, [outcomes])

  function record(transactionId, outcome) {
    setOutcomes(o => ({ ...o, [transactionId]: outcome }))
  }

  function pushHistory() {
    setHistory(h => [...h, idx])
  }

  function advance() {
    setIdx(i => i + 1)
  }

  // After an answer, cards ahead follow the server: the rest of the answered
  // group leaves, and every machine label the server re-decided (same payee,
  // near neighbours) leaves when resolved or shows its new label otherwise, so
  // a later confirm vouches for what is actually stored. Cards already passed
  // stay put so the back button keeps working. Returns how many re-decided
  // cards left (the answered group isn't counted).
  function settle(answered, reevaluated) {
    const done = new Set(answered.map(it => it.transaction_id))
    const byId = new Map(reevaluated.map(r => [r.transaction_id, r]))
    const here = idxRef.current
    let left = 0
    const next = queue.flatMap((it, i) => {
      if (i <= here) return [it]
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
    return queue.filter((it, i) => i >= idx && it.group_key === current.group_key)
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
      await refresh()
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

  function goBack() {
    // Moving while a save is in flight would let its advance() land on a card
    // the user never saw.
    if (history.length === 0 || savingRef.current) return
    const prevIdx = history[history.length - 1]
    setHistory(h => h.slice(0, -1))
    setIdx(prevIdx)
  }

  function skip() {
    if (!current || savingRef.current) return
    const transactionId = current.transaction_id
    pushHistory()
    record(transactionId, 'skipped')
    advance()
    // Persist the snooze so the skip sticks across sessions (4.15), then take
    // the set-aside count from the server. Best-effort: the card has already
    // advanced; a failure just means it reappears next load.
    writesRef.current = writesRef.current
      .then(() => api.post('/annotations/review-queue/skip', { transaction_id: transactionId }))
      .catch(() => {})
      .then(syncSnoozed)
  }

  async function restoreSnoozed() {
    try {
      const { restored } = await api.post('/annotations/review-queue/unsnooze', {})
      await refresh()
      toast(`Restored ${restored} skipped ${restored === 1 ? 'item' : 'items'}`, 'info')
    } catch (e) {
      toast(`Couldn't restore skipped items - ${e.message}`, 'error')
    }
  }

  async function confirm() {
    if (!current || !isRealCategory(current.category) || !beginSave()) return
    try {
      const group = applyToGroup ? groupFromHere() : [current]
      const label = { category: current.category, subcategory: current.subcategory || null }
      const res = group.length > 1
        // One answer for the whole group: the label on this card, as seen.
        ? await api.post('/annotations/bulk-label', { transaction_ids: group.map(it => it.transaction_id), label })
        : await api.post(`/annotations/${current.annotation_id}/confirm`, { category: current.category })
      pushHistory()
      group.forEach(it => record(it.transaction_id, 'confirmed'))
      const sorted = settle(group, res.reevaluated)
      const message = group.length > 1 ? `Got it, all ${group.length} from ${current.payee} confirmed.` : 'Got it, thanks!'
      toast(withResolved(message, sorted), 'success')
      notePersonalLabel(group, current.category, current.subcategory)
      advance()
      syncSnoozed()
    } catch (e) {
      if (e.status === 409) {
        // The label changed under this card (new evidence settled it); show
        // the current queue instead of confirming something unseen.
        toast(`${e.message}. Here's the updated list.`, 'info')
        await refresh()
        return
      }
      toast(`Something went wrong — ${e.message}`, 'error')
    } finally {
      endSave()
    }
  }

  async function saveEdit(overrides = {}) {
    const payload = { ...form, ...overrides }
    if (!isRealCategory(payload.category)) { toast('Pick a category first', 'error'); return }
    if (!beginSave()) return
    try {
      const label = {
        category: payload.category,
        subcategory: payload.subcategory || null,
        merchant: payload.merchant?.trim() || null,
        tags: payload.tags,
      }
      const group = applyToGroup ? groupFromHere() : [current]
      let res
      if (group.length > 1) {
        res = await api.post('/annotations/bulk-label', { transaction_ids: group.map(it => it.transaction_id), label })
      } else if (current.annotation_id) {
        res = await api.patch(`/annotations/${current.annotation_id}`, label)
      } else {
        // Nothing has labelled this payment yet: a plain manual annotation.
        res = await api.post('/annotations', {
          transaction_id: current.transaction_id, ...label, confidence: 1.0, source: 'manual',
        })
      }
      pushHistory()
      group.forEach(it => record(it.transaction_id, 'edited'))
      const sorted = settle(group, res.reevaluated)
      const message = group.length > 1 ? `Noted, all ${group.length} from ${current.payee} updated.` : "Noted, I'll remember that."
      toast(withResolved(message, sorted), 'success')
      notePersonalLabel(group, payload.category, payload.subcategory)
      advance()
      syncSnoozed()
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
            annotationId: current.annotation_id,
            label: payload.subcategory ? `${payload.category} > ${payload.subcategory}` : payload.category,
            items: fixable,
          })
        }
      } catch { /* similar lookup is optional */ }
    } catch (e) {
      toast(`Something went wrong — ${e.message}`, 'error')
    } finally {
      endSave()
    }
  }

  // Create a category (parentId null) or subcategory, add it to the shared
  // trees, and select it on the current card. Returns the node, or null on failure.
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
      handleCategoryChange({ category: parentName, subcategory: parentId ? created.name : '' })
      return created
    } catch (e) {
      toast(`Couldn't add "${name}" - ${e.message}`, 'error')
      return null
    }
  }

  function handleCategoryChange({ category, subcategory }) {
    setForm(f => ({ ...f, category, subcategory, tags: [] }))
  }

  // Keyboard shortcuts. The handler closes over queue/form/idx state, so instead
  // of a hand-maintained dep list (which has silently gone stale in this file
  // before) keep the latest action in a ref and bind the listener exactly once.
  const keyActionRef = useRef(null)
  keyActionRef.current = e => {
    // Shortcuts stay live on the group checkbox; text fields keep their keys.
    if ((e.target.tagName === 'INPUT' && e.target.type !== 'checkbox') || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return
    if (e.ctrlKey || e.metaKey || e.altKey) return
    if (propagate) return // dialog owns the keyboard
    if (e.key === 'b') goBack()
    // Both c and Enter save when the card is dirty; confirming a stale
    // machine label would discard the edit and poison calibration.
    else if (e.key === 's') skip()
    else if (e.key === 'a') setApplyToGroup(v => !v)
    else if (e.key === 'c' || e.key === 'Enter') {
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

  // Payments no sorting run has touched yet (AI off, a run that failed, a
  // statement added since): they are in the queue and can be sorted by hand, or
  // the automatic pass can take a first look at them.
  async function sortAutomatically() {
    setSorting(true)
    try {
      const result = await startAnnotation({})
      const summary = annotationSummary(result)
      toast(summary.message, summary.type)
      await refresh()
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
      toast(`Fixed ${res.applied} similar transaction${res.applied !== 1 ? 's' : ''}`, 'success')
      // Applied rows are now human-labeled — drop any still ahead in the queue.
      const appliedSet = new Set(transactionIds)
      setQueue(q => q.filter((it, i) => i < idxRef.current || !appliedSet.has(it.transaction_id)))
    } catch (e) {
      toast(`Couldn't apply — ${e.message}`, 'error')
    } finally {
      setApplying(false)
      setPropagate(null)
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-full text-[#475569]">Getting your review list ready…</div>
  }

  const total = queue.length
  const done = idx >= total

  const propagateDialog = propagate && (
    <PropagateDialog
      propagate={propagate}
      onApply={applyPropagation}
      onDismiss={() => setPropagate(null)}
      applying={applying}
    />
  )

  if (done && !verified) {
    return (
      <div className="flex items-center justify-center h-full text-[#475569]">
        {propagateDialog}
        Checking for anything left…
      </div>
    )
  }

  if (done) {
    const learned = stats.confirmed + stats.edited
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
              You skipped {snoozedCount === 1 ? 'it' : 'them'}, so {snoozedCount === 1 ? 'it is' : 'they are'} still not sorted.
            </p>
            {setAside}
          </>
        ) : (
          <>
            <div className="text-4xl">✓</div>
            <h2 className="text-lg font-semibold text-[#e2e8f0]">You're all done!</h2>
            <p className="text-sm text-[#94a3b8]">
              {learned > 0
                ? `Thanks! I learned from ${learned} transaction${learned !== 1 ? 's' : ''}.`
                : 'Everything is sorted. Nothing to review right now.'}
            </p>
          </>
        )}
        {(stats.confirmed + stats.edited + stats.skipped > 0) && (
          <p className="text-xs text-[#64748b] mt-1">
            {stats.confirmed} confirmed · {stats.edited} corrected · {stats.skipped} skipped
          </p>
        )}
        {history.length > 0 && (
          <button
            onClick={goBack}
            className="mt-2 flex items-center gap-2 text-sm text-[#94a3b8] hover:text-[#e2e8f0] border border-[#2d3148] px-4 py-2 rounded-lg transition-colors"
          >
            <Undo2 size={15} /> Go back
          </button>
        )}
      </div>
    )
  }

  const item = current
  const isDebit = item.debit_credit === 'debit'
  const src = item.source ?? 'pending'
  const groupSize = groupFromHere().length
  const payeesLeft = new Set(queue.slice(idx).map(it => it.group_key)).size
  const unannotatedLeft = queue.slice(idx).filter(it => !it.annotation_id).length

  return (
    <div className="flex flex-col h-full px-4 py-5">
      {propagateDialog}
      {/* Progress */}
      <div className="max-w-2xl mx-auto w-full mb-5">
        <div className="flex justify-between text-xs text-[#64748b] mb-1.5">
          <span>Teach Me</span>
          <span>{idx} / {total}</span>
        </div>
        <div className="h-1.5 bg-[#1e2235] rounded-full overflow-hidden">
          <div
            className="h-full bg-[#6366f1] rounded-full transition-all"
            style={{ width: `${(idx / total) * 100}%` }}
          />
        </div>
        <p className="text-[10px] text-[#475569] mt-1">
          {stats.confirmed} confirmed · {stats.edited} corrected · {stats.skipped} skipped
          {payeesLeft > 0 && ` · ${payeesLeft} payee${payeesLeft === 1 ? '' : 's'} left`}
        </p>
      </div>

      {unannotatedLeft > 0 && (
        <div className="max-w-2xl mx-auto w-full mb-4 flex items-center gap-4 rounded-xl border border-[#2d3148] bg-[#13151f] px-5 py-3">
          <p className="flex-1 text-sm text-[#cbd5e1]">
            {job != null || sorting
              ? 'Automatic sorting is running. Payments it settles will leave this list.'
              : `${unannotatedLeft} payment${unannotatedLeft === 1 ? ' hasn\'t' : 's haven\'t'} been through automatic sorting yet. You can sort ${unannotatedLeft === 1 ? 'it' : 'them'} by hand here.`}
          </p>
          {job == null && !sorting && (
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
        <div className="max-w-2xl mx-auto w-full mb-4 bg-[#1e1b4b]/60 border border-[#4f46e5]/40 rounded-xl px-5 py-3.5 flex items-center gap-4">
          <p className="flex-1 text-sm text-[#c7d2fe]">
            Sort small payments (up to ₹{POLICY_DEFAULT_MAX}) to people who aren't in your People list as{' '}
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

      {/* Card */}
      <div className="max-w-2xl mx-auto w-full bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
        {/* Transaction header */}
        <div className="px-6 py-5 border-b border-[#2d3148]">
          <div className="flex items-start justify-between gap-4 mb-2">
            <span className="text-sm text-[#64748b]">{dayjs(item.txn_date).format('DD MMM YYYY')}</span>
            <Amount value={item.amount} debitCredit={item.debit_credit} className={`text-xl font-bold tabular-nums ${isDebit ? 'text-red-400' : 'text-green-400'}`} />
          </div>
          <p className="text-sm text-[#cbd5e1] leading-relaxed">{item.raw_description}</p>
          {groupSize > 1 && (
            <label className="mt-3 flex items-center gap-2 text-xs text-[#94a3b8] cursor-pointer select-none">
              <input
                type="checkbox"
                checked={applyToGroup}
                onChange={e => setApplyToGroup(e.target.checked)}
                className="accent-[#6366f1]"
              />
              <span>
                Apply my answer to all {groupSize} payments to{' '}
                <span className="text-[#cbd5e1]">{item.payee}</span>
              </span>
              <kbd className="text-[10px] text-[#64748b] bg-[#1e2235] px-1.5 py-0.5 rounded">a</kbd>
            </label>
          )}
        </div>

        {/* Model's guess + confidence */}
        <div className="px-6 py-4 border-b border-[#2d3148]">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-3">My best guess</p>
          <div className="flex items-center gap-3 flex-wrap mb-3">
            {item.category ? (
              <span className="bg-[#1e1b4b] text-[#a5b4fc] text-sm px-3 py-1 rounded-full">{item.category}</span>
            ) : (
              <span className="text-sm text-[#94a3b8]">No guess yet</span>
            )}
            {item.subcategory && (
              <span className="bg-[#1e1b4b] text-[#818cf8] text-xs px-2.5 py-1 rounded-full">{item.subcategory}</span>
            )}
            {item.merchant && (
              <span className="text-sm text-[#94a3b8]">{item.merchant}</span>
            )}
            <span className={`text-xs px-2 py-0.5 rounded-full ${SOURCE_PILL[src] ?? SOURCE_PILL.pending}`}>
              {/* Review-card voice: two contextual overrides, shared labels otherwise */}
              {src === 'manual' ? 'you set this' : src === 'rule' ? 'rule match' : sourceLabel(src)}
            </span>
          </div>
          {item.annotation_id && (
            <>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b]">Confidence</span>
              </div>
              <Tooltip
                content="How sure the AI is about this guess. Below 60% means it's mostly guessing."
                position="bottom"
                className="block"
              >
                <ConfidenceBar confidence={item.confidence} />
              </Tooltip>
            </>
          )}
        </div>

        {/* Dev mode: collapsible reasoning trace */}
        {devMode && <ReasoningPanel reasoning={item.reasoning} />}

        {/* Inline category picker — always visible */}
        <div className="px-6 py-4 border-b border-[#2d3148]">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-3">
            {item.category
              ? <>Correct it <span className="normal-case font-normal text-[#475569]">- click to change</span></>
              : 'Pick a category'}
          </p>
          <CategoryChips
            tree={tree}
            category={form.category}
            subcategory={form.subcategory}
            onChange={handleCategoryChange}
            onCreate={handleCreateCategory}
          />

          {/* Optional: merchant + tags collapsible */}
          <button
            onClick={() => setShowTags(v => !v)}
            className="mt-3 flex items-center gap-1 text-[10px] text-[#475569] hover:text-[#94a3b8] transition-colors"
          >
            <Tag size={11} />
            {showTags ? 'Hide' : 'Edit'} merchant & tags
            {showTags ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
          </button>

          {showTags && (
            <div className="mt-3 space-y-3">
              <div>
                <label className="block text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-1.5">Merchant</label>
                <input
                  value={form.merchant}
                  onChange={e => setForm(f => ({ ...f, merchant: e.target.value }))}
                  className="w-full bg-[#0f1117] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1]"
                />
              </div>
              <div>
                <label className="block text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-1.5">Tags</label>
                <TagInput tags={form.tags} onChange={tags => setForm(f => ({ ...f, tags }))} />
              </div>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="px-6 py-4 flex gap-3">
          <button
            onClick={goBack}
            disabled={history.length === 0}
            className="flex items-center justify-center gap-1.5 bg-[#13151f] border border-[#2d3148] text-[#64748b] px-3 py-2.5 rounded-lg text-sm hover:text-[#94a3b8] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            title="Go back [b]"
          >
            <Undo2 size={15} /> <span className="text-xs opacity-60">[b]</span>
          </button>
          {needsPick ? (
            <>
              <button
                type="button"
                aria-disabled="true"
                onClick={() => toast('Pick a category below first', 'info')}
                className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg border border-[#2d3148] bg-[#1e2235] text-sm font-medium text-[#94a3b8] cursor-not-allowed"
              >
                Pick a category
              </button>
              <button
                onClick={skip}
                className="flex items-center justify-center gap-2 bg-[#13151f] border border-[#2d3148] text-[#94a3b8] px-4 py-2.5 rounded-lg text-sm hover:text-[#e2e8f0] transition-colors"
              >
                <SkipForward size={16} /> <span className="text-xs opacity-60">[s]</span>
              </button>
            </>
          ) : isDirty ? (
            <>
              <button
                onClick={() => saveEdit()}
                disabled={saving}
                className="flex-1 py-2.5 rounded-lg bg-[#6366f1] text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {saving ? 'Saving…' : 'Save & Next'} <span className="text-xs opacity-60">[↵]</span>
              </button>
              <button
                onClick={skip}
                className="flex items-center justify-center gap-2 bg-[#13151f] border border-[#2d3148] text-[#94a3b8] px-4 py-2.5 rounded-lg text-sm hover:text-[#e2e8f0] transition-colors"
              >
                <SkipForward size={16} /> <span className="text-xs opacity-60">[s]</span>
              </button>
            </>
          ) : (
            <>
              <button
                onClick={confirm}
                disabled={saving}
                className="flex-1 flex items-center justify-center gap-2 bg-green-800/50 border border-green-700 text-green-300 py-2.5 rounded-lg text-sm font-medium hover:bg-green-800 disabled:opacity-50 transition-colors"
              >
                <CheckCircle size={16} /> Confirm <span className="text-xs opacity-60">[c] or [↵]</span>
              </button>
              <button
                onClick={skip}
                className="flex items-center justify-center gap-2 bg-[#13151f] border border-[#2d3148] text-[#94a3b8] px-4 py-2.5 rounded-lg text-sm hover:text-[#e2e8f0] transition-colors"
              >
                <SkipForward size={16} /> <span className="text-xs opacity-60">[s]</span>
              </button>
            </>
          )}
        </div>
      </div>

      <p className="text-center text-xs text-[#475569] mt-4">
        Keyboard: <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded">b</kbd> back ·
        <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded mx-1">c</kbd> confirm ·
        <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded mx-1">↵</kbd> confirm or save ·
        <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded">s</kbd> skip
        {groupSize > 1 && (
          <> · <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded">a</kbd> all from this payee</>
        )}
      </p>
    </div>
  )
}
