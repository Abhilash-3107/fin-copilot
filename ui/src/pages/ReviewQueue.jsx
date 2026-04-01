import { useCallback, useEffect, useState } from 'react'
import { CheckCircle, Edit3, SkipForward } from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import CategoryPicker from '../components/CategoryPicker.jsx'
import TagInput from '../components/TagInput.jsx'
import { SOURCE_PILL, formatAmount } from '../components/TransactionTable.jsx'

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

export default function ReviewQueue() {
  const toast = useToast()
  const [queue, setQueue] = useState([])
  const [idx, setIdx] = useState(0)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({ category: '', subcategory: '', merchant: '', tags: [] })
  const [saving, setSaving] = useState(false)
  const [stats, setStats] = useState({ confirmed: 0, edited: 0, skipped: 0 })

  useEffect(() => {
    api.get('/annotations/review-queue').then(data => {
      setQueue(data)
      setLoading(false)
    }).catch(e => {
      toast(`Couldn't load the review list — ${e.message}`, 'error')
      setLoading(false)
    })
  }, [])

  // Keyboard shortcuts
  useEffect(() => {
    function handler(e) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return
      if (e.key === 'c') confirm()
      else if (e.key === 'e') { setEditing(true); prefillForm() }
      else if (e.key === 's') skip()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [idx, queue, editing])

  const current = queue[idx]

  function prefillForm() {
    if (!current) return
    setForm({
      category: current.category ?? '',
      subcategory: current.subcategory ?? '',
      merchant: current.merchant ?? '',
      tags: Array.isArray(current.tags)
        ? current.tags
        : (current.tags ? current.tags.split(',').filter(Boolean) : []),
    })
  }

  function advance() {
    setEditing(false)
    setIdx(i => i + 1)
  }

  function skip() {
    setStats(s => ({ ...s, skipped: s.skipped + 1 }))
    advance()
  }

  async function confirm() {
    if (!current) return
    setSaving(true)
    try {
      await api.post(`/annotations/${current.annotation_id}/confirm`, {})
      setStats(s => ({ ...s, confirmed: s.confirmed + 1 }))
      toast('Got it, thanks!', 'success')
      advance()
    } catch (e) {
      toast(`Something went wrong — ${e.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function saveEdit() {
    if (!form.category) { toast('Pick a category first', 'error'); return }
    setSaving(true)
    try {
      await api.patch(`/annotations/${current.annotation_id}`, {
        category: form.category,
        subcategory: form.subcategory || null,
        merchant: form.merchant.trim() || null,
        tags: form.tags,
      })
      setStats(s => ({ ...s, edited: s.edited + 1 }))
      toast('Noted — I\'ll remember that', 'success')
      advance()
    } catch (e) {
      toast(`Something went wrong — ${e.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

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
      </div>
    )
  }

  const item = current
  const isDebit = item.debit_credit === 'debit'
  const src = item.source ?? 'pending'

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

        {/* Model's guess */}
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
          <ConfidenceBar confidence={item.confidence} />
        </div>

        {/* Edit form */}
        {editing && (
          <div className="px-6 py-4 border-b border-[#2d3148] space-y-4">
            <CategoryPicker
              category={form.category}
              subcategory={form.subcategory}
              onChange={vals => setForm(f => ({ ...f, ...vals }))}
            />
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

        {/* Actions */}
        <div className="px-6 py-4 flex gap-3">
          {!editing ? (
            <>
              <button
                onClick={confirm}
                disabled={saving}
                className="flex-1 flex items-center justify-center gap-2 bg-green-800/50 border border-green-700 text-green-300 py-2.5 rounded-lg text-sm font-medium hover:bg-green-800 disabled:opacity-50 transition-colors"
              >
                <CheckCircle size={16} /> Confirm <span className="text-xs opacity-60">[c]</span>
              </button>
              <button
                onClick={() => { setEditing(true); prefillForm() }}
                className="flex-1 flex items-center justify-center gap-2 bg-[#1e2440] border border-[#3b4570] text-[#a5b4fc] py-2.5 rounded-lg text-sm font-medium hover:bg-[#252b55] transition-colors"
              >
                <Edit3 size={16} /> Edit <span className="text-xs opacity-60">[e]</span>
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
                onClick={() => setEditing(false)}
                className="px-4 py-2.5 rounded-lg bg-[#13151f] border border-[#2d3148] text-[#94a3b8] text-sm hover:text-[#e2e8f0] transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={saveEdit}
                disabled={saving}
                className="flex-1 py-2.5 rounded-lg bg-[#6366f1] text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {saving ? 'Saving…' : 'Save & Next'}
              </button>
            </>
          )}
        </div>
      </div>

      <p className="text-center text-xs text-[#475569] mt-4">
        Keyboard: <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded">c</kbd> confirm ·
        <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded mx-1">e</kbd> edit ·
        <kbd className="bg-[#1e2235] px-1.5 py-0.5 rounded">s</kbd> skip
      </p>
    </div>
  )
}
