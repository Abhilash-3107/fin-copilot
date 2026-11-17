import { useEffect, useRef, useState } from 'react'
import { X, ChevronDown, ChevronRight, Plus, Trash2, Pencil } from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import CategoryPicker from './CategoryPicker.jsx'
import TagInput from './TagInput.jsx'
import { SOURCE_PILL, sourceLabel } from '../lib/sources.js'
import Amount from './Amount.jsx'
import ReasoningPanel from './ReasoningPanel.jsx'
import InlineError from './InlineError.jsx'
import { couldnt } from '../lib/errors.js'
import { noteLabel } from './PayeeHeading.jsx'
import { capCharacters } from '../lib/text.js'

// Same limits as the API, counted in characters the same way.
const MAX_NAME = 80
const MAX_NOTE = 500

// ─── GroupsSection ────────────────────────────────────────────────────────────

function GroupsSection({ txnId }) {
  const toast = useToast()
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState(true)
  const [search, setSearch] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const searchRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.get(`/groups/for-transaction/${txnId}`).then(data => {
      if (!cancelled) { setGroups(data); setLoading(false) }
    }).catch(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [txnId])

  async function searchGroups(q) {
    setSearch(q)
    if (!q.trim()) { setSuggestions([]); return }
    try {
      const data = await api.get(`/groups?q=${encodeURIComponent(q)}`)
      const existing = new Set(groups.map(g => g.id))
      setSuggestions(data.filter(g => !existing.has(g.id)))
      setShowSuggestions(true)
    } catch (_) {}
  }

  async function addToGroup(group) {
    try {
      await api.post(`/groups/${group.id}/members`, { transaction_id: txnId })
      setGroups(prev => [...prev, group])
      toast(`Added to "${group.name}"`, 'success')
    } catch (e) {
      toast(couldnt('add it to the group', e), 'error')
    }
    setSearch('')
    setSuggestions([])
    setShowSuggestions(false)
  }

  async function createAndAdd(name) {
    try {
      const group = await api.post('/groups', { name })
      await api.post(`/groups/${group.id}/members`, { transaction_id: txnId })
      setGroups(prev => [...prev, group])
      toast(`Created and added to "${group.name}"`, 'success')
    } catch (e) {
      toast(couldnt('create the group', e), 'error')
    }
    setSearch('')
    setSuggestions([])
    setShowSuggestions(false)
  }

  async function removeFromGroup(groupId, groupName) {
    try {
      await api.delete(`/groups/${groupId}/members/${txnId}`)
      setGroups(prev => prev.filter(g => g.id !== groupId))
      toast(`Removed from "${groupName}"`, 'info')
    } catch (e) {
      toast(couldnt('remove it from the group', e), 'error')
    }
  }

  const showCreate = search.trim() && !suggestions.some(g => g.name.toLowerCase() === search.toLowerCase())

  return (
    <div className="border-t border-[#2d3148] pt-3 mt-1">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-[#94a3b8] hover:text-[#e2e8f0] w-full mb-2"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Groups
        {groups.length > 0 && (
          <span className="bg-[#1e2440] text-[#a5b4fc] text-[10px] px-1.5 rounded-full">{groups.length}</span>
        )}
      </button>

      {open && (
        <div className="flex flex-col gap-2">
          {loading ? (
            <p className="text-xs text-[#94a3b8]">Loading…</p>
          ) : groups.length > 0 ? (
            groups.map(g => (
              <div key={g.id} className="flex items-center justify-between bg-[#13151f] border border-[#2d3148] rounded-md px-3 py-2">
                <span className="text-sm text-[#e2e8f0]">{g.name}</span>
                <button
                  type="button"
                  onClick={() => removeFromGroup(g.id, g.name)}
                  className="text-[#94a3b8] hover:text-red-400 transition-colors"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))
          ) : null}

          {/* Add to group input */}
          <div className="relative" ref={searchRef}>
            <div className="flex items-center gap-1.5 bg-[#13151f] border border-[#2d3148] rounded-md px-2.5 py-2 focus-within:border-[#6366f1] transition-colors">
              <Plus size={13} className="text-[#94a3b8] shrink-0" />
              <input
                value={search}
                onChange={e => searchGroups(e.target.value)}
                onFocus={() => search && setShowSuggestions(true)}
                placeholder="Add to group…"
                className="bg-transparent border-none outline-none text-sm text-[#e2e8f0] flex-1 placeholder:text-[#94a3b8]"
              />
            </div>
            {showSuggestions && (suggestions.length > 0 || showCreate) && (
              <div className="absolute z-20 top-full left-0 right-0 mt-1 bg-[#1a1d27] border border-[#2d3148] rounded-md shadow-xl overflow-hidden">
                {suggestions.slice(0, 6).map(g => (
                  <button
                    key={g.id}
                    type="button"
                    onMouseDown={() => addToGroup(g)}
                    className="w-full text-left px-3 py-2 text-sm text-[#e2e8f0] hover:bg-[#1e2440] transition-colors"
                  >
                    {g.name}
                  </button>
                ))}
                {showCreate && (
                  <button
                    type="button"
                    onMouseDown={() => createAndAdd(search.trim())}
                    className="w-full text-left px-3 py-2 text-sm text-[#818cf8] hover:bg-[#1e2440] transition-colors border-t border-[#2d3148]"
                  >
                    + Create group "{search.trim()}"
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── RenameForm ───────────────────────────────────────────────────────────────

// Give the payee a name for every payment to it, or this one payment a name of
// its own. A name is only a label: the printed line, category and learned
// rules are untouched. Clearing a name falls back to the next one down
// (payment, payee, merchant, the payee as printed).
function RenameForm({ txn, onRenamed, onCancel }) {
  const toast = useToast()
  const canRenamePayee = Boolean(txn.counterparty_key)
  const [scope, setScope] = useState(canRenamePayee && !txn.display_name ? 'payee' : 'payment')
  // What the field starts with: the payment's current name, or for the payee
  // the name every payment to it goes by (not this payment's own).
  const prefill = s => (s === 'payee' ? txn.payee_all : txn.payee) ?? ''
  const [name, setName] = useState(prefill(scope))
  const [saving, setSaving] = useState(false)
  // A payee name never clears a payment's own name, so say when this one keeps its.
  const keepsOwnName = scope === 'payee' && Boolean(txn.display_name)
  const hasName = scope === 'payee' ? Boolean(txn.payee_name) : Boolean(txn.display_name)

  function pick(next) {
    if (name === prefill(scope)) setName(prefill(next))
    setScope(next)
  }

  async function save(value, to = scope) {
    setSaving(true)
    try {
      const path = to === 'payee' ? 'payee-name' : 'name'
      const updated = await api.put(`/transactions/${txn.id}/${path}`, { name: value })
      onRenamed(updated, to)
    } catch (e) {
      toast(couldnt('rename', e), 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      className="mt-3 flex flex-col gap-2.5 bg-[#1a1d27] border border-[#2d3148] rounded-md p-3"
      onSubmit={e => { e.preventDefault(); if (name.trim()) save(name.trim()) }}
    >
      <label className="text-xs text-[#94a3b8]" htmlFor="rename-input">Name</label>
      <input
        id="rename-input"
        autoFocus
        value={name}
        onChange={e => setName(capCharacters(e.target.value, MAX_NAME))}
        onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); onCancel() } }}
        className="w-full bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1]"
      />
      <div className="flex flex-col gap-1.5 text-sm text-[#cbd5e1]">
        {canRenamePayee && (
          <label className="flex items-center gap-2">
            <input type="radio" name="rename-scope" checked={scope === 'payee'} onChange={() => pick('payee')} className="accent-[#6366f1]" />
            Every payment to {txn.payee_all}
          </label>
        )}
        <label className="flex items-center gap-2">
          <input type="radio" name="rename-scope" checked={scope === 'payment'} onChange={() => pick('payment')} className="accent-[#6366f1]" />
          Just this payment
        </label>
      </div>
      {keepsOwnName && (
        <p className="text-xs text-[#94a3b8] flex items-center gap-2">
          <span>This payment keeps its own name, {txn.display_name}</span>
          <button type="button" disabled={saving} onClick={() => save(null, 'payment')} className="text-[#a5b4fc] hover:text-[#e2e8f0] underline">
            Clear name
          </button>
        </p>
      )}
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={saving || !name.trim()}
          className="px-3 py-1.5 rounded-md bg-[#4f46e5] text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
        >
          Save name
        </button>
        <button type="button" onClick={onCancel} className="px-3 py-1.5 rounded-md bg-[#1e2235] text-[#94a3b8] text-xs hover:text-[#e2e8f0]">
          Cancel
        </button>
        {hasName && (
          <button type="button" disabled={saving} onClick={() => save(null)} className="ml-auto text-xs text-[#94a3b8] hover:text-[#e2e8f0] underline">
            {keepsOwnName ? 'Clear payee name' : 'Clear name'}
          </button>
        )}
      </div>
    </form>
  )
}

// ─── NoteForm ─────────────────────────────────────────────────────────────────

// A note of the user's own on this one payment. It starts from the note the row
// shows (the UPI note when nothing of theirs is there yet), replaces it, and a
// save with the field emptied clears it so the UPI note shows again. Like a
// name it is a label: the printed line, category and learned rules are untouched.
function NoteForm({ txn, onNoted, onCancel }) {
  const toast = useToast()
  const [note, setNote] = useState(txn.note ?? '')
  const [saving, setSaving] = useState(false)

  async function save(value) {
    const text = value.trim()
    // Nothing to change: the text is what the row shows already, or there is no
    // note of the user's to clear.
    if (text === (txn.note ?? '') || (!text && !txn.note_is_yours)) { onCancel(); return }
    setSaving(true)
    try {
      onNoted(await api.put(`/transactions/${txn.id}/note`, { note: text || null }))
    } catch (e) {
      toast(couldnt('save the note', e), 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      className="mt-3 flex flex-col gap-2.5 bg-[#1a1d27] border border-[#2d3148] rounded-md p-3"
      onSubmit={e => { e.preventDefault(); save(note) }}
    >
      <label className="text-xs text-[#94a3b8]" htmlFor="note-input">Your note</label>
      <input
        id="note-input"
        autoFocus
        value={note}
        onChange={e => setNote(capCharacters(e.target.value, MAX_NOTE))}
        onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); onCancel() } }}
        className="w-full bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1]"
      />
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={saving}
          className="px-3 py-1.5 rounded-md bg-[#4f46e5] text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
        >
          Save note
        </button>
        <button type="button" onClick={onCancel} className="px-3 py-1.5 rounded-md bg-[#1e2235] text-[#94a3b8] text-xs hover:text-[#e2e8f0]">
          Cancel
        </button>
        {txn.note_is_yours && (
          <button type="button" disabled={saving} onClick={() => save('')} className="ml-auto text-xs text-[#94a3b8] hover:text-[#e2e8f0] underline">
            Clear note
          </button>
        )}
      </div>
    </form>
  )
}

// ─── Confidence bar ───────────────────────────────────────────────────────────

function ConfidenceBar({ confidence }) {
  const pct = Math.round((confidence ?? 0) * 100)
  const color = confidence >= 0.85 ? 'bg-green-500' : confidence >= 0.6 ? 'bg-amber-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-[#1e2235] rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-[#94a3b8]">{pct}%</span>
    </div>
  )
}

// ─── AnnotationPanel ──────────────────────────────────────────────────────────

// `loadError` means the payment's own record could not be read, so whether it
// already has an annotation is unknown: the form is replaced by the error and
// saving waits for a successful Retry.
export default function AnnotationPanel(props) {
  // Everything the panel holds belongs to one payment: a half-typed name or note,
  // the loaded groups, the scroll position. Keying on the id starts it fresh when
  // another payment opens (the palette can jump to one while a form is open), so
  // text typed for one payment can never be saved onto another.
  return <PaymentPanel key={props.txn?.id} {...props} />
}

function PaymentPanel({ txn, annotation, onClose, onSaved, onRenamed, onNoted, loadError = null, onRetryLoad }) {
  const toast = useToast()
  const [renaming, setRenaming] = useState(false)
  const [noting, setNoting] = useState(false)
  const renameButton = useRef(null)
  const noteButton = useRef(null)
  const openForm = renaming ? 'rename' : noting ? 'note' : null
  const lastForm = useRef(null)

  // A form takes its button's place while open; when the last one closes, focus
  // goes back to that form's button so a keyboard user is not left at the top
  // of the page. Switching straight from one form to the other leaves focus
  // where the newly opened form put it.
  useEffect(() => {
    if (!openForm && lastForm.current) {
      (lastForm.current === 'rename' ? renameButton : noteButton).current?.focus()
    }
    lastForm.current = openForm
  }, [openForm])
  const [form, setForm] = useState({ category: '', subcategory: '', tags: [] })
  const [saving, setSaving] = useState(false)

  // Reset form when transaction or annotation changes.
  // We depend on txn?.id and annotation (by reference) so that:
  // - switching between two unannotated transactions (both annotation=null) still resets
  // - the undefined sentinel from the parent (annotation loading) also triggers a reset
  useEffect(() => {
    if (!txn) return
    if (annotation === undefined) return // still loading; wait for real value
    setForm({
      category:    annotation?.category    ?? '',
      subcategory: annotation?.subcategory ?? '',
      tags: annotation?.tags
        ? (Array.isArray(annotation.tags) ? annotation.tags : annotation.tags.split(',').filter(Boolean))
        : [],
    })
    // Keyed on the transaction id, not the txn object: re-syncing on every new
    // txn reference would discard an in-progress edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [txn?.id, annotation])

  async function handleSave() {
    if (!form.category) {
      toast('Category is required', 'error')
      return
    }
    setSaving(true)
    try {
      const payload = {
        category:    form.category,
        subcategory: form.subcategory || null,
        tags:        form.tags,
      }
      let saved
      if (annotation?.id) {
        saved = await api.patch(`/annotations/${annotation.id}`, payload)
      } else {
        saved = await api.post('/annotations', {
          ...payload,
          transaction_id: txn.id,
          confidence: 1.0,
          source: 'manual',
        })
      }
      toast('Saved', 'success')
      onSaved?.(txn.id, saved)
      onClose?.()
    } catch (e) {
      toast(couldnt('save', e), 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!txn) return null

  const src = annotation?.source

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />
      {/* Panel */}
      <aside className="fixed right-0 top-0 bottom-0 w-full max-w-[440px] bg-[#1a1d27] border-l border-[#2d3148] z-50 flex flex-col animate-slide-in">
        {/* Header */}
        <div className="px-5 py-3.5 border-b border-[#2d3148] flex items-center justify-between shrink-0">
          <h2 className="text-sm font-semibold text-[#a5b4fc]">
            {loadError ? "Couldn't load this payment" : annotation ? 'Edit this payment' : 'Sort this payment'}
          </h2>
          <button onClick={onClose} className="text-[#94a3b8] hover:text-[#e2e8f0] transition-colors">
            <X size={17} />
          </button>
        </div>

        {/* Transaction summary */}
        <div className="px-5 py-4 bg-[#13151f] border-b border-[#2d3148] shrink-0">
          <div className="flex items-start justify-between gap-4 mb-2">
            <span className="text-xs text-[#94a3b8]">{dayjs(txn.txn_date).format('DD MMM YYYY')}</span>
            <Amount value={txn.amount} debitCredit={txn.debit_credit} className="text-lg font-bold tabular-nums" />
          </div>
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-base font-semibold text-[#e2e8f0] break-words min-w-0">{txn.payee || txn.raw_description}</h3>
            {!renaming && (
              <button
                type="button"
                ref={renameButton}
                onClick={() => { setNoting(false); setRenaming(true) }}
                className="shrink-0 inline-flex items-center gap-1 text-xs text-[#94a3b8] hover:text-[#e2e8f0] transition-colors"
              >
                <Pencil size={12} />Rename
              </button>
            )}
          </div>
          {renaming && (
            <RenameForm
              txn={txn}
              onRenamed={(updated, scope) => { setRenaming(false); onRenamed?.(updated, scope) }}
              onCancel={() => setRenaming(false)}
            />
          )}
          {noting ? (
            <NoteForm
              txn={txn}
              onNoted={updated => { setNoting(false); onNoted?.(updated) }}
              onCancel={() => setNoting(false)}
            />
          ) : (
            <div className="mt-2 flex items-start gap-3">
              {txn.note && (
                <div className="flex-1 min-w-0 px-3 py-1.5 bg-[#1e2440] border-l-2 border-[#6366f1] rounded text-xs text-[#a5b4fc] break-words">
                  <span className="text-[#94a3b8] mr-1.5 whitespace-nowrap">{noteLabel(txn)}</span>{txn.note}
                </div>
              )}
              <button
                type="button"
                ref={noteButton}
                onClick={() => { setRenaming(false); setNoting(true) }}
                className={`inline-flex items-center gap-1 text-xs text-[#94a3b8] hover:text-[#e2e8f0] transition-colors ${txn.note ? 'shrink-0 mt-1.5' : ''}`}
              >
                <Pencil size={12} />{txn.note ? 'Edit note' : 'Add a note'}
              </button>
            </div>
          )}
          <p className="mt-3 text-[10px] font-semibold uppercase tracking-wider text-[#94a3b8]">As printed on the statement</p>
          <p className="mt-1 text-xs text-[#94a3b8] break-words leading-relaxed">{txn.raw_description}</p>
          {annotation && (
            <div className="mt-3 flex items-center gap-2">
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${SOURCE_PILL[src] ?? SOURCE_PILL.pending}`}>
                {sourceLabel(src)}
              </span>
              {/* Manual annotations are human truth; a leftover model
                  confidence next to "manual" reads as a contradiction. */}
              {src !== 'manual' && (
                <div className="flex-1">
                  <ConfidenceBar confidence={annotation.confidence} />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Dev mode: collapsible reasoning trace (backend only sends it when DEV_MODE is on) */}
        {annotation?.reasoning && <ReasoningPanel reasoning={annotation.reasoning} />}

        {/* Form */}
        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-4">
          {loadError ? (
            <InlineError what="this payment's details" error={loadError} onRetry={onRetryLoad} />
          ) : (
          <>
          <CategoryPicker
            category={form.category}
            subcategory={form.subcategory}
            onChange={vals => setForm(f => ({ ...f, ...vals }))}
          />

          <div>
            <label className="block text-[10px] font-semibold uppercase tracking-wider text-[#94a3b8] mb-1.5">
              Tags
            </label>
            <TagInput tags={form.tags} onChange={tags => setForm(f => ({ ...f, tags }))} />
          </div>

          <GroupsSection txnId={txn.id} />
          </>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-[#2d3148] flex gap-3 shrink-0">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-md bg-[#1e2235] text-[#94a3b8] text-sm hover:text-[#e2e8f0] transition-colors"
          >
            {loadError ? 'Close' : 'Cancel'}
          </button>
          {!loadError && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex-1 px-4 py-2 rounded-md bg-[#4f46e5] text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {saving ? 'Saving…' : annotation ? 'Update' : 'Save'}
            </button>
          )}
        </div>
      </aside>
    </>
  )
}
