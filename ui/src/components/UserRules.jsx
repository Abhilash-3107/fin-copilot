import { useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import Amount from './Amount.jsx'
import CategoryPicker from './CategoryPicker.jsx'

const field = 'w-full bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] placeholder:text-[#475569] transition-colors'
const label = 'block text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-1.5'
const EMPTY = { pattern: '', category: '', subcategory: '', merchant: '' }

function plural(n, word, many = `${word}s`) {
  return `${n} ${n === 1 ? word : many}`
}

// What saving + applying this rule would do, from POST /rules/preview.
function PreviewSummary({ preview }) {
  if (preview.matches === 0) {
    return <p className="text-xs text-[#64748b]">No transactions match yet - the rule will catch future ones.</p>
  }
  const parts = [
    preview.relabel && `${preview.relabel} would be recategorized`,
    preview.unlabeled && `${preview.unlabeled} not categorized yet`,
    preview.unchanged && `${preview.unchanged} already match`,
    preview.protected && `${preview.protected} you labeled yourself (kept)`,
  ].filter(Boolean)
  return (
    <div>
      <p className="text-xs text-[#94a3b8]">
        Matches {plural(preview.matches, 'transaction')}: {parts.join(' · ')}
      </p>
      <ul className="mt-2 space-y-1">
        {preview.sample.map(t => (
          <li key={t.id} className="flex items-center gap-3 text-xs">
            <span className="text-[#64748b] w-16 shrink-0">{dayjs(t.txn_date).format('DD MMM YY')}</span>
            <span className="text-[#cbd5e1] truncate flex-1 min-w-0">{t.raw_description}</span>
            <span className="text-[#64748b] shrink-0 truncate max-w-[45%]">
              {t.category ? `${t.category}${t.subcategory ? ` › ${t.subcategory}` : ''}` : 'uncategorized'}
            </span>
            <span className="tabular-nums shrink-0 w-24 text-right text-[#94a3b8]">
              <Amount value={t.amount} debitCredit={t.debit_credit} />
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function UserRules() {
  const toast = useToast()
  const [rules, setRules] = useState([])
  const [loading, setLoading] = useState(true)
  const [draft, setDraft] = useState(EMPTY)
  const [preview, setPreview] = useState(null)
  const [applyNow, setApplyNow] = useState(false)
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState(null)

  useEffect(() => {
    api.get('/rules')
      .then(setRules)
      .catch(e => toast(`Couldn't load your rules - ${e.message}`, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  // Live preview while typing, debounced so each keystroke isn't a scan.
  const ready = draft.pattern.trim() && draft.category
  useEffect(() => {
    if (!ready) { setPreview(null); return }
    let cancelled = false
    const timer = setTimeout(() => {
      api.post('/rules/preview', {
        pattern: draft.pattern, category: draft.category, subcategory: draft.subcategory || null,
      })
        .then(p => { if (!cancelled) setPreview(p) })
        .catch(() => { if (!cancelled) setPreview(null) })
    }, 300)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [ready, draft.pattern, draft.category, draft.subcategory])

  const applicable = preview ? preview.relabel + preview.unlabeled : 0

  async function save(e) {
    e.preventDefault()
    setSaving(true)
    try {
      const rule = await api.post('/rules', {
        pattern: draft.pattern,
        category: draft.category,
        subcategory: draft.subcategory || null,
        merchant: draft.merchant || null,
      })
      setRules(rs => [...rs, rule])
      let msg = `Rule added: "${rule.pattern}" → ${rule.category}${rule.subcategory ? ` › ${rule.subcategory}` : ''}`
      if (applyNow && applicable > 0) {
        const r = await api.post(`/rules/${rule.id}/apply`)
        msg += `. Categorized ${plural(r.relabeled + r.labeled, 'existing transaction')}`
      }
      toast(msg, 'success')
      setDraft(EMPTY)
      setApplyNow(false)
    } catch (err) {
      toast(`Couldn't add rule - ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function toggle(rule) {
    setBusyId(rule.id)
    try {
      const updated = await api.patch(`/rules/${rule.id}`, { enabled: !rule.enabled })
      setRules(rs => rs.map(r => (r.id === rule.id ? updated : r)))
    } catch (err) {
      toast(`Couldn't update rule - ${err.message}`, 'error')
    } finally {
      setBusyId(null)
    }
  }

  async function applyExisting(rule) {
    setBusyId(rule.id)
    try {
      const r = await api.post(`/rules/${rule.id}/apply`)
      const n = r.relabeled + r.labeled
      toast(n ? `Categorized ${plural(n, 'transaction')} with "${rule.pattern}"` : 'Nothing to change - matching transactions already agree', n ? 'success' : 'info')
    } catch (err) {
      toast(`Couldn't apply rule - ${err.message}`, 'error')
    } finally {
      setBusyId(null)
    }
  }

  async function remove(rule) {
    setBusyId(rule.id)
    try {
      await api.delete(`/rules/${rule.id}`)
      setRules(rs => rs.filter(r => r.id !== rule.id))
      toast(`Removed "${rule.pattern}". Transactions it already categorized keep their category.`, 'info')
    } catch (err) {
      toast(`Couldn't remove rule - ${err.message}`, 'error')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div>
      <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Your rules</h2>
      <p className="text-xs text-[#64748b] mb-3 leading-relaxed max-w-xl">
        Categorize every transaction that mentions a word or phrase. Your rules run first,
        ahead of the built-in ones and anything learned, and match whole words in the
        description or UPI note.
      </p>

      <form onSubmit={save} className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4 mb-4 space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-3">
            <div>
              <label className={label} htmlFor="rule-pattern">
                When the transaction mentions <span className="text-red-400">*</span>
              </label>
              <input
                id="rule-pattern"
                value={draft.pattern}
                onChange={e => setDraft(d => ({ ...d, pattern: e.target.value }))}
                placeholder="e.g. zepto, landlord, cult fit"
                className={field}
              />
            </div>
            <div>
              <label className={label} htmlFor="rule-merchant">Merchant name (optional)</label>
              <input
                id="rule-merchant"
                value={draft.merchant}
                onChange={e => setDraft(d => ({ ...d, merchant: e.target.value }))}
                placeholder="How it should be shown"
                className={field}
              />
            </div>
          </div>
          <CategoryPicker
            category={draft.category}
            subcategory={draft.subcategory}
            onChange={({ category, subcategory }) => setDraft(d => ({ ...d, category, subcategory }))}
          />
        </div>

        {preview && <PreviewSummary preview={preview} />}

        <div className="flex flex-wrap items-center justify-end gap-3">
          {applicable > 0 && (
            <label className="flex items-center gap-2 text-xs text-[#cbd5e1] mr-auto">
              <input
                type="checkbox"
                checked={applyNow}
                onChange={e => setApplyNow(e.target.checked)}
                className="accent-[#6366f1]"
              />
              Also categorize the {plural(applicable, 'existing match', 'existing matches')} now
            </label>
          )}
          <button
            type="submit"
            disabled={!ready || saving}
            className="flex items-center gap-1.5 bg-[#6366f1] text-white px-4 py-2 rounded-md text-sm hover:bg-[#5558e3] disabled:opacity-40 transition-colors"
          >
            <Plus size={14} /> {saving ? 'Adding…' : 'Add rule'}
          </button>
        </div>
      </form>

      <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
        {loading ? (
          <p className="px-5 py-4 text-sm text-[#64748b]">Loading…</p>
        ) : rules.length === 0 ? (
          <p className="px-5 py-4 text-sm text-[#64748b]">No rules yet. Add one above.</p>
        ) : (
          <ul className="divide-y divide-[#2d3148]">
            {rules.map(r => (
              <li key={r.id} className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3 text-sm ${r.enabled ? '' : 'opacity-50'}`}>
                <span className="font-mono text-[#e2e8f0] min-w-0 break-all">"{r.pattern}"</span>
                <span className="text-[#cbd5e1] flex-1 min-w-[10rem]">
                  → {r.category}
                  {r.subcategory && <span className="text-[#64748b]"> › {r.subcategory}</span>}
                  {r.merchant && <span className="text-[#64748b] ml-2 text-xs">as {r.merchant}</span>}
                </span>
                <div className="flex items-center gap-3 ml-auto">
                  <button
                    onClick={() => applyExisting(r)}
                    disabled={busyId === r.id || !r.enabled}
                    className="text-xs text-[#a5b4fc] hover:text-white disabled:opacity-40 transition-colors"
                  >
                    Apply to existing
                  </button>
                  <button
                    role="switch"
                    aria-checked={r.enabled}
                    aria-label={`${r.enabled ? 'Pause' : 'Resume'} rule "${r.pattern}"`}
                    onClick={() => toggle(r)}
                    disabled={busyId === r.id}
                    className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${r.enabled ? 'bg-[#6366f1]' : 'bg-[#2d3148]'}`}
                  >
                    <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${r.enabled ? 'translate-x-[18px]' : 'translate-x-[3px]'}`} />
                  </button>
                  <button
                    onClick={() => remove(r)}
                    disabled={busyId === r.id}
                    aria-label={`Remove rule "${r.pattern}"`}
                    title="Remove rule"
                    className="text-[#64748b] hover:text-[#f87171] transition-colors disabled:opacity-40"
                  >
                    <X size={15} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
