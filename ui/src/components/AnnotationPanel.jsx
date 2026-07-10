import { useEffect, useRef, useState } from 'react'
import { X, ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import CategoryPicker from './CategoryPicker.jsx'
import TagInput from './TagInput.jsx'
import { SOURCE_PILL, sourceLabel } from '../lib/sources.js'
import Amount from './Amount.jsx'
import ReasoningPanel from './ReasoningPanel.jsx'

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
      toast(`Failed: ${e.message}`, 'error')
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
      toast(`Failed: ${e.message}`, 'error')
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
      toast(`Failed: ${e.message}`, 'error')
    }
  }

  const showCreate = search.trim() && !suggestions.some(g => g.name.toLowerCase() === search.toLowerCase())

  return (
    <div className="border-t border-[#2d3148] pt-3 mt-1">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-[#64748b] hover:text-[#94a3b8] w-full mb-2"
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
            <p className="text-xs text-[#475569]">Loading…</p>
          ) : groups.length > 0 ? (
            groups.map(g => (
              <div key={g.id} className="flex items-center justify-between bg-[#13151f] border border-[#2d3148] rounded-md px-3 py-2">
                <span className="text-sm text-[#e2e8f0]">{g.name}</span>
                <button
                  type="button"
                  onClick={() => removeFromGroup(g.id, g.name)}
                  className="text-[#475569] hover:text-red-400 transition-colors"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))
          ) : null}

          {/* Add to group input */}
          <div className="relative" ref={searchRef}>
            <div className="flex items-center gap-1.5 bg-[#13151f] border border-[#2d3148] rounded-md px-2.5 py-2 focus-within:border-[#6366f1] transition-colors">
              <Plus size={13} className="text-[#475569] shrink-0" />
              <input
                value={search}
                onChange={e => searchGroups(e.target.value)}
                onFocus={() => search && setShowSuggestions(true)}
                placeholder="Add to group…"
                className="bg-transparent border-none outline-none text-sm text-[#e2e8f0] flex-1 placeholder:text-[#475569]"
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
                    className="w-full text-left px-3 py-2 text-sm text-[#6366f1] hover:bg-[#1e2440] transition-colors border-t border-[#2d3148]"
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

export default function AnnotationPanel({ txn, annotation, onClose, onSaved }) {
  const toast = useToast()
  const [form, setForm] = useState({ category: '', subcategory: '', merchant: '', tags: [] })
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
      merchant:    annotation?.merchant    ?? '',
      tags: annotation?.tags
        ? (Array.isArray(annotation.tags) ? annotation.tags : annotation.tags.split(',').filter(Boolean))
        : [],
    })
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
        merchant:    form.merchant.trim() || null,
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
      toast(`Save failed: ${e.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!txn) return null

  const isDebit = txn.debit_credit === 'debit'
  let upiMeta = null
  try {
    upiMeta = typeof txn.upi_meta === 'string' ? JSON.parse(txn.upi_meta) : txn.upi_meta
  } catch (_) {}
  const src = annotation?.source

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />
      {/* Panel */}
      <aside className="fixed right-0 top-0 bottom-0 w-[440px] bg-[#1a1d27] border-l border-[#2d3148] z-50 flex flex-col animate-slide-in">
        {/* Header */}
        <div className="px-5 py-3.5 border-b border-[#2d3148] flex items-center justify-between shrink-0">
          <h2 className="text-sm font-semibold text-[#a5b4fc]">
            {annotation ? 'Edit annotation' : 'Annotate transaction'}
          </h2>
          <button onClick={onClose} className="text-[#64748b] hover:text-[#e2e8f0] transition-colors">
            <X size={17} />
          </button>
        </div>

        {/* Transaction summary */}
        <div className="px-5 py-4 bg-[#13151f] border-b border-[#2d3148] shrink-0">
          <div className="flex items-start justify-between gap-4 mb-2">
            <span className="text-xs text-[#64748b]">{dayjs(txn.txn_date).format('DD MMM YYYY')}</span>
            <Amount value={txn.amount} debitCredit={txn.debit_credit} className={`text-lg font-bold tabular-nums ${isDebit ? 'text-red-400' : 'text-green-400'}`} />
          </div>
          <p className="text-sm text-[#cbd5e1] break-words leading-relaxed">{txn.raw_description}</p>
          {upiMeta?.note && (
            <div className="mt-2 px-3 py-1.5 bg-[#1e2440] border-l-2 border-[#6366f1] rounded text-xs text-[#a5b4fc]">
              <span className="text-[#475569] mr-1.5">note</span>{upiMeta.note}
            </div>
          )}
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
          <CategoryPicker
            category={form.category}
            subcategory={form.subcategory}
            onChange={vals => setForm(f => ({ ...f, ...vals }))}
          />

          <div>
            <label className="block text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-1.5">
              Merchant
            </label>
            <input
              value={form.merchant}
              onChange={e => setForm(f => ({ ...f, merchant: e.target.value }))}
              placeholder="e.g. Swiggy"
              className="w-full bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] transition-colors placeholder:text-[#475569]"
            />
          </div>

          <div>
            <label className="block text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-1.5">
              Tags
            </label>
            <TagInput tags={form.tags} onChange={tags => setForm(f => ({ ...f, tags }))} />
          </div>

          <GroupsSection txnId={txn.id} />
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-[#2d3148] flex gap-3 shrink-0">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-md bg-[#1e2235] text-[#94a3b8] text-sm hover:text-[#e2e8f0] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 px-4 py-2 rounded-md bg-[#6366f1] text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {saving ? 'Saving…' : annotation ? 'Update annotation' : 'Save annotation'}
          </button>
        </div>
      </aside>
    </>
  )
}
