import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { X, SlidersHorizontal, Cpu, BarChart3, BookOpen, Database, FolderTree } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import RunSummary from '../components/RunSummary.jsx'
import ModelSettings from '../components/ModelSettings.jsx'
import DataSettings from '../components/DataSettings.jsx'
import CategorySettings from '../components/CategorySettings.jsx'
import UserRules from '../components/UserRules.jsx'
import { txnFilterPath } from '../lib/txnLink.js'

function Toggle({ checked, disabled, onChange }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
        checked ? 'bg-[#6366f1]' : 'bg-[#2d3148]'
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
          checked ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  )
}

// Prefer the human merchant, but fall back to the counterparty key when the
// merchant is empty, a mangled one-character fragment (4.21) - a lone "U" or
// "3" reads as broken - or a whole bank line ("UPI/NAME/1207.../self") that
// was saved as the merchant. The key is at least recognizable.
function ruleDisplayName(r) {
  const m = (r.merchant || '').trim()
  return m.length > 1 && !m.includes('/') ? m : r.counterparty_key
}

function LearnedRules() {
  const toast = useToast()
  const [rules, setRules] = useState([])
  const [loading, setLoading] = useState(true)
  const [dismissing, setDismissing] = useState(null)

  useEffect(() => {
    api.get('/annotations/learned-rules')
      .then(setRules)
      .catch(e => toast(`Couldn't load learned rules - ${e.message}`, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  async function dismiss(rule) {
    setDismissing(rule.counterparty_key)
    try {
      await api.delete(`/annotations/learned-rules/${encodeURIComponent(rule.counterparty_key)}`)
      setRules(rs => rs.filter(r => r.counterparty_key !== rule.counterparty_key))
      toast(`Stopped learning "${rule.merchant || rule.counterparty_key}"`, 'info')
    } catch (e) {
      toast(`Couldn't dismiss - ${e.message}`, 'error')
    } finally {
      setDismissing(null)
    }
  }

  return (
    <div>
      <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Learned from your corrections</h2>
      <p className="text-xs text-[#64748b] mb-3 leading-relaxed max-w-xl">
        When you verify a merchant enough times and agree on a category, your copilot
        starts categorizing it automatically - no AI needed. These rules update as you
        review; correcting a merchant is how you change one. Dismiss (×) a rule to stop
        it - useful once you've added that contact to People and they're handled there.
      </p>

      <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
        {loading ? (
          <p className="px-5 py-4 text-sm text-[#64748b]">Loading…</p>
        ) : rules.length === 0 ? (
          <p className="px-5 py-4 text-sm text-[#64748b]">
            Nothing learned yet. As you verify transactions in the review queue, recurring
            merchants will show up here.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-[#64748b] border-b border-[#2d3148]">
                <th className="font-medium px-5 py-2.5">Merchant</th>
                <th className="font-medium px-5 py-2.5">Categorized as</th>
                <th className="font-medium px-5 py-2.5 text-right">Confidence</th>
                <th className="font-medium px-5 py-2.5 w-10"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#2d3148]">
              {rules.map(r => (
                <tr key={`${r.counterparty_key}|${r.direction ?? ''}`}>
                  <td className="px-5 py-3 text-[#e2e8f0]">
                    <Link
                      to={txnFilterPath(
                        r.merchant ? { merchant: r.merchant } : { category: r.category }
                      )}
                      className="hover:text-[#a5b4fc] transition-colors"
                    >
                      {ruleDisplayName(r)}
                    </Link>
                    {ruleDisplayName(r) !== r.counterparty_key && (
                      <span className="text-[#64748b] ml-2 text-xs">{r.counterparty_key}</span>
                    )}
                    {r.direction === 'credit' && (
                      <span className="text-[#64748b] ml-2 text-xs">money in</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-[#cbd5e1] whitespace-nowrap">
                    {r.category}
                    {r.subcategory && <span className="text-[#64748b]"> › {r.subcategory}</span>}
                  </td>
                  <td className="px-5 py-3 text-right text-[#64748b] tabular-nums whitespace-nowrap">
                    {Math.round(r.purity * 100)}%
                    <span className="ml-1.5 text-[#475569]">({r.support}/{r.total})</span>
                  </td>
                  <td className="px-2 py-3 text-right">
                    <button
                      onClick={() => dismiss(r)}
                      disabled={dismissing === r.counterparty_key}
                      title="Stop learning this merchant"
                      className="text-[#64748b] hover:text-[#f87171] transition-colors disabled:opacity-40"
                    >
                      <X size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// Insights tab: the review-bar slider with a live preview. Moving it repreviews
// the run summary at the candidate threshold (dev mode) before saving.
function InsightsSettings({ devMode, threshold, onSave }) {
  const toast = useToast()
  const [value, setValue] = useState(threshold)
  const [saving, setSaving] = useState(false)
  useEffect(() => { setValue(threshold) }, [threshold])

  const dirty = Math.abs(value - threshold) > 1e-9

  async function save() {
    setSaving(true)
    try {
      await onSave(value)
      toast(`Review bar set to ${Math.round(value * 100)}%`, 'success')
    } catch (e) {
      toast(`Couldn't save - ${e.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Review bar</h2>
        <p className="text-xs text-[#64748b] mb-4 leading-relaxed max-w-xl">
          Auto-categorizations below this confidence go to the review queue for you to
          verify; at or above it, they're accepted automatically. Raise it to catch more
          borderline calls yourself; lower it to review less.
        </p>
        <div className="bg-[#13151f] border border-[#2d3148] rounded-xl px-5 py-5">
          <div className="flex items-center gap-4">
            <input
              type="range"
              min="0.5" max="0.99" step="0.01"
              value={value}
              onChange={e => setValue(parseFloat(e.target.value))}
              className="flex-1 accent-[#6366f1]"
            />
            <span className="w-16 text-right tabular-nums text-lg font-semibold text-[#e2e8f0]">
              {Math.round(value * 100)}%
            </span>
            <button
              onClick={save}
              disabled={!dirty || saving}
              className="bg-[#6366f1] text-white px-4 py-1.5 rounded-md text-sm font-medium hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
          {dirty && (
            <p className="text-[11px] text-[#f59e0b] mt-2">
              Preview below reflects {Math.round(value * 100)}% - not saved yet.
            </p>
          )}
        </div>
      </div>

      {devMode ? (
        <div>
          <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Auto-categorization insights</h2>
          <p className="text-xs text-[#64748b] mb-3 leading-relaxed max-w-xl">
            Where your annotated transactions sit relative to the pipeline's thresholds:
            which stage decided each, how the similarity and confidence scores are
            distributed against their cutoffs, and the transactions sitting just below the
            review bar. The lists and review rate update live as you drag the slider.
          </p>
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
            <RunSummary threshold={value} />
          </div>
        </div>
      ) : (
        <p className="text-xs text-[#64748b] max-w-xl">
          Turn on Developer mode (General tab) to preview which transactions the current
          review bar catches or lets through.
        </p>
      )}
    </div>
  )
}

const TABS = [
  { id: 'general', label: 'General', icon: SlidersHorizontal },
  { id: 'model', label: 'AI & Model', icon: Cpu },
  { id: 'insights', label: 'Insights', icon: BarChart3 },
  { id: 'categories', label: 'Categories', icon: FolderTree },
  { id: 'rules', label: 'Rules', icon: BookOpen },
  { id: 'data', label: 'Data', icon: Database },
]

export default function Settings() {
  const toast = useToast()
  const [tab, setTab] = useState('general')
  const [devMode, setDevMode] = useState(false)
  const [threshold, setThreshold] = useState(0.85)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [version, setVersion] = useState(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    api.get('/health').then(h => setVersion(h.version)).catch(() => {})
    api.get('/config')
      .then(cfg => {
        setDevMode(!!cfg.dev_mode)
        if (typeof cfg.confidence_threshold === 'number') setThreshold(cfg.confidence_threshold)
      })
      .catch(e => toast(`Couldn't load settings - ${e.message}`, 'error'))
      .finally(() => setLoading(false))
    return () => { mounted.current = false }
  }, [toast])

  async function setDev(next) {
    setSaving(true)
    try {
      const cfg = await api.put('/config', { dev_mode: next })
      setDevMode(!!cfg.dev_mode)
      toast(next ? 'Developer mode on' : 'Developer mode off', 'success')
    } catch (e) {
      toast(`Couldn't save - ${e.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function saveThreshold(value) {
    const cfg = await api.put('/config', { confidence_threshold: value })
    if (typeof cfg.confidence_threshold === 'number') setThreshold(cfg.confidence_threshold)
  }

  return (
    <div className="max-w-3xl mx-auto w-full px-4 py-6">
      <h1 className="text-lg font-semibold text-[#e2e8f0] mb-1">Settings</h1>
      <p className="text-sm text-[#64748b] mb-5">Configure how your copilot behaves.</p>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-[#2d3148] mb-6 overflow-x-auto">
        {TABS.map(t => {
          const Icon = t.icon
          const active = tab === t.id
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 -mb-px transition-colors whitespace-nowrap ${
                active
                  ? 'border-[#6366f1] text-[#e2e8f0]'
                  : 'border-transparent text-[#64748b] hover:text-[#94a3b8]'
              }`}
            >
              <Icon size={14} />
              {t.label}
            </button>
          )
        })}
      </div>

      {loading ? (
        <p className="text-sm text-[#475569]">Loading…</p>
      ) : (
        <>
          {tab === 'general' && (
            <div className="bg-[#13151f] border border-[#2d3148] rounded-xl divide-y divide-[#2d3148]">
              <div className="flex items-center justify-between gap-6 px-5 py-4">
                <div>
                  <p className="text-sm font-medium text-[#e2e8f0]">Developer mode</p>
                  <p className="text-xs text-[#64748b] mt-0.5 leading-relaxed">
                    Show the reasoning behind each auto-categorization - the neighbours,
                    similarity math against each threshold, the model's explanation, and a
                    run-level summary in the Insights tab. The reasoning is always recorded,
                    so turning this on also explains transactions categorized earlier.
                  </p>
                </div>
                <Toggle checked={devMode} disabled={saving} onChange={setDev} />
              </div>
            </div>
          )}

          {tab === 'model' && <ModelSettings />}

          {tab === 'insights' && (
            <InsightsSettings devMode={devMode} threshold={threshold} onSave={saveThreshold} />
          )}

          {tab === 'categories' && <CategorySettings />}

          {tab === 'rules' && (
            <div className="space-y-8">
              <UserRules />
              <LearnedRules />
            </div>
          )}

          {tab === 'data' && <DataSettings />}
        </>
      )}
      {version && (
        <p className="mt-8 text-xs text-[#475569]">Finance Copilot v{version}</p>
      )}
    </div>
  )
}
