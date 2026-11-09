import { useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import CategoryPicker from './CategoryPicker.jsx'

const field = 'w-full bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] placeholder:text-[#475569] transition-colors'
const label = 'block text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-1.5'
const EMPTY = { max_amount: '500', category: '', subcategory: '' }

const rupees = n => `₹${Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`

function sortedMessage(resolved) {
  return resolved
    ? ` It sorted ${resolved} waiting transaction${resolved === 1 ? '' : 's'}.`
    : ''
}

// Standing answers to a whole class of review questions (POST /policies). The
// one kind today: small UPI payments to people who aren't on the People page.
export default function ReviewPolicies() {
  const toast = useToast()
  const [policies, setPolicies] = useState([])
  const [loading, setLoading] = useState(true)
  const [draft, setDraft] = useState(EMPTY)
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState(null)

  useEffect(() => {
    api.get('/policies')
      .then(setPolicies)
      .catch(e => toast(`Couldn't load your policies - ${e.message}`, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  const amount = Number(draft.max_amount)
  const ready = draft.category && amount > 0

  async function save(e) {
    e.preventDefault()
    if (!ready) return
    setSaving(true)
    try {
      const created = await api.post('/policies', {
        max_amount: amount, category: draft.category, subcategory: draft.subcategory || null,
      })
      setPolicies(ps => [...ps, created])
      setDraft(EMPTY)
      toast(`Policy added.${sortedMessage(created.resolved)}`, 'success')
    } catch (err) {
      toast(`Couldn't add policy - ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function toggle(p) {
    setBusyId(p.id)
    try {
      const updated = await api.patch(`/policies/${p.id}`, { enabled: !p.enabled })
      setPolicies(ps => ps.map(x => (x.id === p.id ? updated : x)))
      if (updated.enabled) toast(`Policy resumed.${sortedMessage(updated.resolved)}`, 'success')
    } catch (err) {
      toast(`Couldn't update policy - ${err.message}`, 'error')
    } finally {
      setBusyId(null)
    }
  }

  async function saveLimit(p, value) {
    const amount = Number(value)
    if (!(amount > 0) || amount === p.max_amount) return
    setBusyId(p.id)
    try {
      const updated = await api.patch(`/policies/${p.id}`, { max_amount: amount })
      setPolicies(ps => ps.map(x => (x.id === p.id ? updated : x)))
      toast(`Limit set to ${rupees(amount)}.${sortedMessage(updated.resolved)}`, 'success')
    } catch (err) {
      toast(`Couldn't update policy - ${err.message}`, 'error')
    } finally {
      setBusyId(null)
    }
  }

  async function remove(p) {
    setBusyId(p.id)
    try {
      const { returned_to_review: back } = await api.delete(`/policies/${p.id}`)
      setPolicies(ps => ps.filter(x => x.id !== p.id))
      toast(
        back
          ? `Policy removed. ${back} transaction${back === 1 ? '' : 's'} it sorted ${back === 1 ? 'is' : 'are'} back in Teach Me.`
          : 'Policy removed.',
        'info',
      )
    } catch (err) {
      toast(`Couldn't remove policy - ${err.message}`, 'error')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div>
      <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Policies</h2>
      <p className="text-xs text-[#64748b] mb-3 leading-relaxed max-w-xl">
        One answer for a kind of transaction you'd otherwise review one at a time. Small UPI
        payments to people who aren't in your People list (autos, street vendors) get this
        category automatically. Businesses, people you've added, and payees you've labeled
        differently before are never affected.
      </p>

      <form onSubmit={save} className="bg-[#13151f] border border-[#2d3148] rounded-xl p-4 mb-4 space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className={label} htmlFor="policy-max">
              Payments to people up to (₹) <span className="text-red-400">*</span>
            </label>
            <input
              id="policy-max"
              type="number"
              min="1"
              inputMode="numeric"
              value={draft.max_amount}
              onChange={e => setDraft(d => ({ ...d, max_amount: e.target.value }))}
              className={field}
            />
          </div>
          <CategoryPicker
            category={draft.category}
            subcategory={draft.subcategory}
            onChange={({ category, subcategory }) => setDraft(d => ({ ...d, category, subcategory }))}
          />
        </div>
        <div className="flex justify-end">
          <button
            type="submit"
            disabled={!ready || saving}
            className="flex items-center gap-1.5 bg-[#6366f1] text-white px-4 py-2 rounded-md text-sm hover:bg-[#5558e3] disabled:opacity-40 transition-colors"
          >
            <Plus size={14} /> {saving ? 'Adding…' : 'Add policy'}
          </button>
        </div>
      </form>

      <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
        {loading ? (
          <p className="px-5 py-4 text-sm text-[#64748b]">Loading…</p>
        ) : policies.length === 0 ? (
          <p className="px-5 py-4 text-sm text-[#64748b]">No policies yet.</p>
        ) : (
          <ul className="divide-y divide-[#2d3148]">
            {policies.map(p => (
              <li key={p.id} className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3 text-sm ${p.enabled ? '' : 'opacity-50'}`}>
                <label className="flex items-center gap-2 text-[#e2e8f0]">
                  Payments to people up to ₹
                  <input
                    type="number"
                    min="1"
                    defaultValue={p.max_amount}
                    aria-label="Amount limit"
                    disabled={busyId === p.id}
                    onBlur={e => saveLimit(p, e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && e.currentTarget.blur()}
                    className="w-20 bg-[#0f1117] border border-[#2d3148] text-[#e2e8f0] px-2 py-1 rounded-md text-sm tabular-nums focus:outline-none focus:border-[#6366f1]"
                  />
                </label>
                <span className="text-[#cbd5e1] flex-1 min-w-[10rem]">
                  → {p.category}
                  {p.subcategory && <span className="text-[#64748b]"> › {p.subcategory}</span>}
                </span>
                <div className="flex items-center gap-3 ml-auto">
                  <button
                    role="switch"
                    aria-checked={p.enabled}
                    aria-label={`${p.enabled ? 'Pause' : 'Resume'} policy`}
                    onClick={() => toggle(p)}
                    disabled={busyId === p.id}
                    className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${p.enabled ? 'bg-[#6366f1]' : 'bg-[#2d3148]'}`}
                  >
                    <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${p.enabled ? 'translate-x-[18px]' : 'translate-x-[3px]'}`} />
                  </button>
                  <button
                    onClick={() => remove(p)}
                    disabled={busyId === p.id}
                    aria-label="Remove policy"
                    title="Remove policy"
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
