import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { X } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import RunSummary from '../components/RunSummary.jsx'
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
  }, [])

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
    <div className="mt-8">
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
                <tr key={r.counterparty_key}>
                  <td className="px-5 py-3 text-[#e2e8f0]">
                    <Link
                      to={txnFilterPath(
                        r.merchant ? { merchant: r.merchant } : { category: r.category }
                      )}
                      className="hover:text-[#a5b4fc] transition-colors"
                    >
                      {r.merchant || r.counterparty_key}
                    </Link>
                    {r.merchant && (
                      <span className="text-[#64748b] ml-2 text-xs">{r.counterparty_key}</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-[#cbd5e1]">
                    {r.category}
                    {r.subcategory && <span className="text-[#64748b]"> › {r.subcategory}</span>}
                  </td>
                  <td className="px-5 py-3 text-right text-[#64748b] tabular-nums">
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

export default function Settings() {
  const toast = useToast()
  const [devMode, setDevMode] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.get('/config')
      .then(cfg => setDevMode(!!cfg.dev_mode))
      .catch(e => toast(`Couldn't load settings — ${e.message}`, 'error'))
      .finally(() => setLoading(false))
  }, [])

  async function setDev(next) {
    setSaving(true)
    try {
      const cfg = await api.put('/config', { dev_mode: next })
      setDevMode(!!cfg.dev_mode)
      toast(next ? 'Developer mode on' : 'Developer mode off', 'success')
    } catch (e) {
      toast(`Couldn't save — ${e.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto w-full px-4 py-6">
      <h1 className="text-lg font-semibold text-[#e2e8f0] mb-1">Settings</h1>
      <p className="text-sm text-[#64748b] mb-6">Configure how your copilot behaves.</p>

      <div className="bg-[#13151f] border border-[#2d3148] rounded-xl divide-y divide-[#2d3148]">
        <div className="flex items-center justify-between gap-6 px-5 py-4">
          <div>
            <p className="text-sm font-medium text-[#e2e8f0]">Developer mode</p>
            <p className="text-xs text-[#64748b] mt-0.5 leading-relaxed">
              Show the reasoning behind each auto-categorization — the neighbours,
              similarity math against each threshold, the model's explanation, and a
              run-level summary below. The reasoning is always recorded, so turning
              this on also explains transactions categorized earlier.
            </p>
          </div>
          <Toggle checked={devMode} disabled={loading || saving} onChange={setDev} />
        </div>
      </div>

      {devMode && (
        <div className="mt-8">
          <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Auto-categorization insights</h2>
          <p className="text-xs text-[#64748b] mb-3 leading-relaxed max-w-xl">
            Where your annotated transactions sit relative to the pipeline's thresholds:
            which stage decided each, how the similarity and confidence scores are
            distributed against their cutoffs, and the transactions sitting just below a
            gate. This is what to look at when deciding how to tune those thresholds.
          </p>
          <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
            <RunSummary />
          </div>
        </div>
      )}

      <LearnedRules />
    </div>
  )
}
