import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'

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
              Capture and show the reasoning behind each auto-categorization
              (neighbours, similarity math, and the model's explanation) in the review
              queue. Only affects transactions categorized while this is on.
            </p>
          </div>
          <Toggle checked={devMode} disabled={loading || saving} onChange={setDev} />
        </div>
      </div>
    </div>
  )
}
