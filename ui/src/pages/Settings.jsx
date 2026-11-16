import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { X, SlidersHorizontal, Cpu, GraduationCap, BookOpen, Database, FolderTree } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { DeveloperInsights } from '../components/RunSummary.jsx'
import ModelSettings from '../components/ModelSettings.jsx'
import DataSettings from '../components/DataSettings.jsx'
import CategorySettings from '../components/CategorySettings.jsx'
import UserRules from '../components/UserRules.jsx'
import ReviewPolicies from '../components/ReviewPolicies.jsx'
import InlineError from '../components/InlineError.jsx'
import { useLoad } from '../lib/useLoad.js'
import { txnFilterPath } from '../lib/txnLink.js'
import { couldnt } from '../lib/errors.js'

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

const ruleId = r => `${r.counterparty_key}|${r.direction}`

// The payees Finance Copilot sorts without asking, and without an AI model:
// each one was sorted the same way, by you, enough times. The list is computed
// from your own sorting, so sorting a payee differently is how you change one.
function RememberedPayees() {
  const toast = useToast()
  const { data, error, reload, setData: setRules } = useLoad(useCallback(() => api.get('/annotations/learned-rules'), []))
  const rules = data ?? []
  const [dismissing, setDismissing] = useState(null)

  async function dismiss(rule) {
    setDismissing(ruleId(rule))
    try {
      await api.delete(
        `/annotations/learned-rules/${encodeURIComponent(rule.counterparty_key)}?direction=${rule.direction}`
      )
      setRules(rs => rs.filter(r => ruleId(r) !== ruleId(rule)))
      const which = rule.direction === 'credit' ? ' (money in)' : ''
      toast(`Stopped sorting "${rule.payee}"${which} on its own`, 'info')
    } catch (e) {
      toast(couldnt('stop that', e), 'error')
    } finally {
      setDismissing(null)
    }
  }

  return (
    <div>
      <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Payees sorted on their own</h2>
      <p className="text-xs text-[#94a3b8] mb-3 leading-relaxed max-w-xl">
        When you sort a payee the same way enough times, Finance Copilot sorts its
        payments for you, no AI needed. This list follows your sorting: sort a payee
        differently to change one. Remove (×) one to stop it - useful once you've added
        that person to People and they're handled there.
      </p>

      <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
        {error ? (
          <InlineError what="your remembered payees" error={error} onRetry={reload} className="m-3" />
        ) : data == null ? (
          <p className="px-5 py-4 text-sm text-[#94a3b8]">Loading…</p>
        ) : rules.length === 0 ? (
          <p className="px-5 py-4 text-sm text-[#94a3b8]">
            No payees yet. As you sort payments in Teach Me, payees you sort the same way
            again and again will show up here.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-[#94a3b8] border-b border-[#2d3148]">
                <th className="font-medium px-5 py-2.5">Payee</th>
                <th className="font-medium px-5 py-2.5">Sorted as</th>
                <th className="font-medium px-5 py-2.5 text-right">Your sorts that agree</th>
                <th className="font-medium px-5 py-2.5 w-10"><span className="sr-only">Remove</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#2d3148]">
              {rules.map(r => (
                <tr key={ruleId(r)}>
                  <td className="px-5 py-3 text-[#e2e8f0]">
                    <Link
                      to={txnFilterPath({ payeeKeys: [r.counterparty_key] })}
                      className="hover:text-[#a5b4fc] transition-colors"
                    >
                      {r.payee}
                    </Link>
                    {r.direction === 'credit' && (
                      <span className="text-[#94a3b8] ml-2 text-xs">money in</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-[#cbd5e1] whitespace-nowrap">
                    {r.category}
                    {r.subcategory && <span className="text-[#94a3b8]"> › {r.subcategory}</span>}
                  </td>
                  <td className="px-5 py-3 text-right text-[#94a3b8] tabular-nums whitespace-nowrap">
                    {r.support} of {r.total}
                  </td>
                  <td className="px-2 py-3 text-right">
                    <button
                      onClick={() => dismiss(r)}
                      disabled={dismissing === ruleId(r)}
                      title="Stop sorting this payee on its own"
                      aria-label={`Stop sorting ${r.payee} on its own`}
                      className="text-[#94a3b8] hover:text-[#f87171] transition-colors disabled:opacity-40"
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

// Teach Me tab: when Finance Copilot should ask you. Moving the slider
// repreviews the run summary at the candidate value (developer mode) before saving.
function WhenToAskYou({ devMode, threshold, onSave }) {
  const toast = useToast()
  const [value, setValue] = useState(threshold)
  const [saving, setSaving] = useState(false)
  useEffect(() => { setValue(threshold) }, [threshold])

  const dirty = Math.abs(value - threshold) > 1e-9

  async function save() {
    setSaving(true)
    try {
      await onSave(value)
      toast(`You'll be asked about guesses under ${Math.round(value * 100)}% sure`, 'success')
    } catch (e) {
      toast(couldnt('save', e), 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">When to ask you</h2>
        <p className="text-xs text-[#94a3b8] mb-4 leading-relaxed max-w-xl">
          When a guess is less sure than this, the payment waits for you in Teach Me.
          Guesses at or above it are accepted on their own. Raise it to be asked more
          often, lower it to be asked less.
        </p>
        <div className="bg-[#13151f] border border-[#2d3148] rounded-xl px-5 py-5">
          <div className="flex items-center gap-4">
            <input
              type="range"
              min="0.5" max="0.99" step="0.01"
              value={value}
              onChange={e => setValue(parseFloat(e.target.value))}
              aria-label="Ask me below this sureness"
              className="flex-1 accent-[#6366f1]"
            />
            <span className="w-24 text-right tabular-nums text-lg font-semibold text-[#e2e8f0]">
              {Math.round(value * 100)}% sure
            </span>
            <button
              onClick={save}
              disabled={!dirty || saving}
              className="bg-[#4f46e5] text-white px-4 py-1.5 rounded-md text-sm font-medium hover:opacity-90 disabled:opacity-40 transition-opacity"
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
        <DeveloperInsights threshold={value} />
      ) : (
        <p className="text-xs text-[#94a3b8] max-w-xl">
          Turn on Developer mode (General tab) to preview which payments this setting
          would ask you about.
        </p>
      )}
    </div>
  )
}

// The Teach Me tab was called Insights: old links and bookmarks still open it.
const RENAMED_TABS = { insights: 'teach' }

const TABS = [
  { id: 'general', label: 'General', icon: SlidersHorizontal },
  { id: 'ai', label: 'AI & Model', icon: Cpu },
  { id: 'teach', label: 'Teach Me', icon: GraduationCap },
  { id: 'categories', label: 'Categories', icon: FolderTree },
  { id: 'rules', label: 'Rules', icon: BookOpen },
  { id: 'data', label: 'Data', icon: Database },
]

export default function Settings() {
  const toast = useToast()
  // The tab lives in the URL (#/settings?tab=ai) so a link can open it and a
  // reload keeps it. General is the default and leaves the URL bare.
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const wanted = Object.hasOwn(RENAMED_TABS, requested) ? RENAMED_TABS[requested] : requested
  const tab = TABS.some(t => t.id === wanted) ? wanted : 'general'
  const setTab = id => setParams(id === 'general' ? {} : { tab: id }, { replace: true })
  // Rewrite an old tab id in the address, so a reload or a copied link keeps the new one.
  useEffect(() => {
    if (Object.hasOwn(RENAMED_TABS, requested)) setParams({ tab: RENAMED_TABS[requested] }, { replace: true })
  }, [requested, setParams])
  const [devMode, setDevMode] = useState(false)
  const [threshold, setThreshold] = useState(0.85)
  const [loading, setLoading] = useState(true)
  // General and Teach Me show these values: when they did not load, saying so
  // beats showing defaults that look like the saved settings.
  const [configError, setConfigError] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const [saving, setSaving] = useState(false)
  const [version, setVersion] = useState(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    api.get('/health').then(h => setVersion(h.version)).catch(() => {})
    setConfigError(null)
    api.get('/config')
      .then(cfg => {
        setDevMode(!!cfg.dev_mode)
        if (typeof cfg.confidence_threshold === 'number') setThreshold(cfg.confidence_threshold)
      })
      .catch(setConfigError)
      .finally(() => setLoading(false))
    return () => { mounted.current = false }
  }, [attempt])

  const configFailed = (
    <InlineError what="your settings" error={configError} onRetry={() => setAttempt(n => n + 1)} />
  )

  async function setDev(next) {
    setSaving(true)
    try {
      const cfg = await api.put('/config', { dev_mode: next })
      setDevMode(!!cfg.dev_mode)
      toast(next ? 'Developer mode on' : 'Developer mode off', 'success')
    } catch (e) {
      toast(couldnt('save', e), 'error')
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
      <p className="text-sm text-[#94a3b8] mb-5">Choose how payments get sorted, and look after your data.</p>

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
                  : 'border-transparent text-[#94a3b8] hover:text-[#e2e8f0]'
              }`}
            >
              <Icon size={14} />
              {t.label}
            </button>
          )
        })}
      </div>

      {loading ? (
        <p className="text-sm text-[#94a3b8]">Loading…</p>
      ) : (
        <>
          {tab === 'general' && configError && configFailed}
          {tab === 'general' && !configError && (
            <div className="bg-[#13151f] border border-[#2d3148] rounded-xl divide-y divide-[#2d3148]">
              <div className="flex items-center justify-between gap-6 px-5 py-4">
                <div>
                  <p className="text-sm font-medium text-[#e2e8f0]">Developer mode</p>
                  <p className="text-xs text-[#94a3b8] mt-0.5 leading-relaxed">
                    Show how each payment was sorted - the similar payments it was compared
                    with, the scores, the model's explanation, and a summary of recent runs
                    in the Teach Me tab. This is always recorded, so turning it on also
                    explains payments sorted earlier.
                  </p>
                </div>
                <Toggle checked={devMode} disabled={saving} onChange={setDev} />
              </div>
            </div>
          )}

          {tab === 'ai' && <ModelSettings />}

          {tab === 'teach' && configError && configFailed}
          {tab === 'teach' && !configError && (
            <WhenToAskYou devMode={devMode} threshold={threshold} onSave={saveThreshold} />
          )}

          {tab === 'categories' && <CategorySettings />}

          {tab === 'rules' && (
            <div className="space-y-8">
              <UserRules />
              <ReviewPolicies />
              <RememberedPayees />
            </div>
          )}

          {tab === 'data' && <DataSettings />}
        </>
      )}
      {version && (
        <p className="mt-8 text-xs text-[#94a3b8]">Finance Copilot v{version}</p>
      )}
    </div>
  )
}
