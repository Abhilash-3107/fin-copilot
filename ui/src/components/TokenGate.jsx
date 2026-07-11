import { useEffect, useState } from 'react'
import { Lock } from 'lucide-react'
import { api, getApiToken, setApiToken } from '../lib/api.js'

// Opt-in token gate (4.9). When the server has an api_token configured it
// reports auth_required on /api/health (which is itself unauthenticated). If we
// don't yet hold a working token, block the app on a single password prompt and
// verify the entry against a real authenticated call before letting through.
// When no token is configured, this renders children immediately.
export default function TokenGate({ children }) {
  const [state, setState] = useState('checking') // 'checking' | 'ok' | 'locked'
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function verify() {
    // /api/config is authenticated; a 200 means our stored token works.
    try {
      await api.get('/config')
      return true
    } catch {
      return false
    }
  }

  useEffect(() => {
    let alive = true
    ;(async () => {
      let health
      try {
        health = await api.get('/health')
      } catch {
        // Health is unauthenticated; a failure here is a server problem, not an
        // auth one - let the app render and surface its own errors.
        if (alive) setState('ok')
        return
      }
      if (!health?.auth_required) {
        if (alive) setState('ok')
        return
      }
      const ok = getApiToken() ? await verify() : false
      if (alive) setState(ok ? 'ok' : 'locked')
    })()
    return () => { alive = false }
  }, [])

  async function submit(e) {
    e.preventDefault()
    setSubmitting(true)
    setError('')
    setApiToken(value.trim())
    const ok = await verify()
    setSubmitting(false)
    if (ok) {
      setState('ok')
    } else {
      setApiToken('')
      setError('That token was not accepted.')
    }
  }

  if (state === 'checking') {
    return <div className="min-h-screen bg-[#0a0b10] flex items-center justify-center text-sm text-[#475569]">Loading…</div>
  }

  if (state === 'locked') {
    return (
      <div className="min-h-screen bg-[#0a0b10] flex items-center justify-center px-4">
        <form onSubmit={submit} className="w-full max-w-sm bg-[#13151f] border border-[#2d3148] rounded-xl p-6 space-y-4">
          <div className="flex items-center gap-2 text-[#e2e8f0]">
            <Lock size={16} className="text-[#6366f1]" />
            <h1 className="text-sm font-semibold">Access token required</h1>
          </div>
          <p className="text-xs text-[#64748b] leading-relaxed">
            This copy is protected with a shared token. Enter it to continue - it
            is stored only in this browser.
          </p>
          <input
            type="password"
            autoFocus
            value={value}
            onChange={e => setValue(e.target.value)}
            placeholder="Access token"
            className="w-full bg-[#0a0b10] border border-[#2d3148] text-[#e2e8f0] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1]"
          />
          {error && <p className="text-xs text-[#f87171]">{error}</p>}
          <button
            type="submit"
            disabled={submitting || !value.trim()}
            className="w-full bg-[#6366f1] text-white px-4 py-2 rounded-md text-sm font-medium hover:opacity-90 disabled:opacity-40 transition-opacity"
          >
            {submitting ? 'Checking…' : 'Unlock'}
          </button>
        </form>
      </div>
    )
  }

  return children
}
