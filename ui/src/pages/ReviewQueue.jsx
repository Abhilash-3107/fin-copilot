import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle, SkipForward, Tag, ChevronDown, ChevronUp, Undo2 } from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import TagInput from '../components/TagInput.jsx'
import { SOURCE_PILL, formatAmount } from '../components/TransactionTable.jsx'
import Tooltip from '../components/Tooltip.jsx'
import ReasoningPanel from '../components/ReasoningPanel.jsx'

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

function CategoryChips({ tree, category, subcategory, onChange }) {
  const roots = useMemo(() => tree.filter(c => !c.parent_id), [tree])
  const children = useMemo(() => {
    if (!category) return []
    const parent = roots.find(r => r.name === category)
    if (!parent) return []
    return tree.filter(c => c.parent_id === parent.id)
  }, [tree, category, roots])

  return (
    <div className="space-y-2.5">
      {/* Category chips */}
      <div className="flex flex-wrap gap-1.5">
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
      </div>

      {/* Subcategory chips — appear inline when a category with subs is selected */}
      {children.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pl-1 border-l-2 border-[#6366f1]/30">
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
        </div>
      )}
    </div>
  )
}

export default function ReviewQueue() {
  const toast = useToast()
  const [queue, setQueue] = useState([])
  const [idx, setIdx] = useState(0)
  const [loading, setLoading] = useState(true)
  const [tree, setTree] = useState(_catCache ?? [])
  const [form, setForm] = useState({ category: '', subcategory: '', merchant: '', tags: [] })
  const [showTags, setShowTags] = useState(false)
  const [saving, setSaving] = useState(false)
  const [stats, setStats] = useState({ confirmed: 0, edited: 0, skipped: 0 })
  const [history, setHistory] = useState([]) // stack of {idx, stats} snapshots
  const [devMode, setDevMode] = useState(false)

  useEffect(() => {
    api.get('/annotations/review-queue').then(data => {
      setQueue(data)
      setLoading(false)
    }).catch(e => {
      toast(`Couldn't load the review list — ${e.message}`, 'error')
      setLoading(false)
    })

    if (!_catCache) {
      api.get('/categories').then(data => {
        _catCache = data
        setTree(data)
      }).catch(() => {})
    }

    // Fetch fresh each mount so a toggle on the Settings page takes effect.
    api.get('/config').then(cfg => setDevMode(!!cfg.dev_mode)).catch(() => {})
  }, [])

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
  }, [idx, current?.annotation_id])

  function pushHistory() {
    setHistory(h => [...h, { idx, stats }])
  }

  function advance() {
    setIdx(i => i + 1)
  }

  function goBack() {
    if (history.length === 0) return
    const prev = history[history.length - 1]
    setHistory(h => h.slice(0, -1))
    setIdx(prev.idx)
    setStats(prev.stats)
  }

  function skip() {
    pushHistory()
    setStats(s => ({ ...s, skipped: s.skipped + 1 }))
    advance()
  }

  async function confirm() {
    if (!current) return
    setSaving(true)
    try {
      await api.post(`/annotations/${current.annotation_id}/confirm`, {})
      pushHistory()
      setStats(s => ({ ...s, confirmed: s.confirmed + 1 }))
      toast('Got it, thanks!', 'success')
      advance()
    } catch (e) {
      toast(`Something went wrong — ${e.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function saveEdit(overrides = {}) {
    const payload = { ...form, ...overrides }
    if (!payload.category) { toast('Pick a category first', 'error'); return }
    setSaving(true)
    try {
      await api.patch(`/annotations/${current.annotation_id}`, {
        category: payload.category,
        subcategory: payload.subcategory || null,
        merchant: payload.merchant?.trim() || null,
        tags: payload.tags,
      })
      pushHistory()
      setStats(s => ({ ...s, edited: s.edited + 1 }))
      toast("Noted — I'll remember that", 'success')
      advance()
    } catch (e) {
      toast(`Something went wrong — ${e.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  function handleCategoryChange({ category, subcategory }) {
    setForm(f => ({ ...f, category, subcategory, tags: [] }))
  }

  // Keyboard shortcuts
  useEffect(() => {
    function handler(e) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (e.key === 'b') goBack()
      else if (e.key === 'c') confirm()
      else if (e.key === 's') skip()
      else if (e.key === 'Enter') {
        const isDirty =
          form.category !== (current?.category ?? '') ||
          form.subcategory !== (current?.subcategory ?? '')
        if (isDirty) saveEdit()
        else confirm()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [idx, queue, form, current])

  if (loading) {
    return <div className="flex items-center justify-center h-full text-[#475569]">Getting your review list ready…</div>
  }

  const total = queue.length
  const done = idx >= total

  if (done) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <div className="text-4xl">✓</div>
        <h2 className="text-lg font-semibold text-[#e2e8f0]">You're all done!</h2>
        <p className="text-sm text-[#94a3b8]">
          {stats.confirmed + stats.edited > 0
            ? `Thanks! I learned from ${stats.confirmed + stats.edited} transaction${stats.confirmed + stats.edited !== 1 ? 's' : ''}.`
            : 'Nothing to review right now.'}
        </p>
        <p className="text-xs text-[#64748b] mt-1">
          {stats.confirmed} confirmed · {stats.edited} corrected · {stats.skipped} skipped
        </p>
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

  const isDirty =
    form.category !== (item.category ?? '') ||
    form.subcategory !== (item.subcategory ?? '') ||
    form.merchant !== (item.merchant ?? '') ||
    JSON.stringify(form.tags) !== JSON.stringify(
      Array.isArray(item.tags) ? item.tags : (item.tags ? item.tags.split(',').filter(Boolean) : [])
    )

  return (
    <div className="flex flex-col h-full px-4 py-5">
      {/* Progress */}
      <div className="max-w-2xl mx-auto w-full mb-5">
        <div className="flex justify-between text-xs text-[#64748b] mb-1.5">
          <span>Teaching your copilot</span>
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
        </p>
      </div>

      {/* Card */}
      <div className="max-w-2xl mx-auto w-full bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
        {/* Transaction header */}
        <div className="px-6 py-5 border-b border-[#2d3148]">
          <div className="flex items-start justify-between gap-4 mb-2">
            <span className="text-sm text-[#64748b]">{dayjs(item.txn_date).format('DD MMM YYYY')}</span>
            <span className={`text-xl font-bold tabular-nums ${isDebit ? 'text-red-400' : 'text-green-400'}`}>
              {formatAmount(item.amount, item.debit_credit)}
            </span>
          </div>
          <p className="text-sm text-[#cbd5e1] leading-relaxed">{item.raw_description}</p>
        </div>

        {/* Model's guess + confidence */}
        <div className="px-6 py-4 border-b border-[#2d3148]">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-3">My best guess</p>
          <div className="flex items-center gap-3 flex-wrap mb-3">
            {item.category && (
              <span className="bg-[#1e1b4b] text-[#a5b4fc] text-sm px-3 py-1 rounded-full">{item.category}</span>
            )}
            {item.subcategory && (
              <span className="bg-[#1e1b4b] text-[#818cf8] text-xs px-2.5 py-1 rounded-full">{item.subcategory}</span>
            )}
            {item.merchant && (
              <span className="text-sm text-[#94a3b8]">{item.merchant}</span>
            )}
            <span className={`text-xs px-2 py-0.5 rounded-full ${SOURCE_PILL[src] ?? SOURCE_PILL.pending}`}>
              {src === 'rag_direct' || src === 'rag_prompted' ? 'from history'
                : src === 'llm' ? 'AI guess'
                : src === 'rule' ? 'rule match'
                : src === 'manual' ? 'you set this'
                : src}
            </span>
          </div>
          <Tooltip content="How sure the AI is about this guess. Below 60% means it's mostly guessing." position="bottom">
            <ConfidenceBar confidence={item.confidence} />
          </Tooltip>
        </div>

        {/* Dev mode: collapsible reasoning trace */}
        {devMode && <ReasoningPanel reasoning={item.reasoning} />}

        {/* Inline category picker — always visible */}
        <div className="px-6 py-4 border-b border-[#2d3148]">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-3">
            Correct it <span className="normal-case font-normal text-[#475569]">— click to change</span>
          </p>
          <CategoryChips
            tree={tree}
            category={form.category}
            subcategory={form.subcategory}
            onChange={handleCategoryChange}
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
          {isDirty ? (
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
      </p>
    </div>
  )
}
