import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, XCircle, Loader2, Plug, ChevronDown } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import InlineError from './InlineError.jsx'
import ConfirmDialog from './ConfirmDialog.jsx'
import { count } from '../lib/plural.js'
import { formatMonth } from '../lib/dates.js'
import { couldnt } from '../lib/errors.js'

const PROVIDERS = [
  { id: 'ollama', label: 'Ollama', hint: 'Models running on your computer through Ollama. Private, no key needed.' },
  { id: 'local', label: 'On this computer', hint: 'Downloads a model once (~2.7GB), then sorting runs fully offline in the app. Needs the local-llm add-on.' },
  { id: 'openai', label: 'OpenAI-compatible', hint: 'A model you reach over the internet or your network, such as OpenAI, LM Studio or OpenRouter.' },
  { id: 'none', label: 'Disabled', hint: 'No AI. Sorting uses your rules and remembered payees only.' },
]

// How payments are matched with ones you have already sorted. Developer detail,
// so it lives under Advanced.
const MATCH_PROVIDERS = [
  { id: 'local', label: 'Built-in', hint: 'Runs inside the app - nothing to install. Downloads a small model (~100MB) the first time.' },
  { id: 'ollama', label: 'Ollama', hint: 'Uses the nomic-embed-text model in your local Ollama.' },
  { id: 'none', label: 'Off', hint: 'Similar payments are not matched. Your rules still work.' },
]

const field = 'w-full bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] placeholder:text-[#94a3b8]'
const label = 'block text-xs text-[#94a3b8] mb-1'

// Local-AI management: shows exactly one of (install hint | download button |
// download progress | ready + remove). The model artifact is managed by
// /api/local-llm; inference switches on via the provider radio above.
function LocalAICard() {
  const toast = useToast()
  const [status, setStatus] = useState(null)
  const [job, setJob] = useState(null)

  const refresh = () => api.get('/local-llm/status').then(setStatus).catch(() => {})
  useEffect(() => { refresh() }, [])

  // Poll the download job once a second until it resolves.
  useEffect(() => {
    if (!job || job.status === 'completed' || job.status === 'failed') return undefined
    const t = setInterval(async () => {
      try {
        const j = await api.get(`/annotations/jobs/${job.job_id ?? job.id}`)
        setJob({ ...j, job_id: j.id })
        if (j.status === 'completed') { toast('Local model ready', 'success'); refresh() }
        if (j.status === 'failed') { toast("Couldn't finish the download. Use Resume download to try again.", 'error'); refresh() }
      } catch { /* transient poll error; keep trying */ }
    }, 1000)
    return () => clearInterval(t)
  }, [job, toast])

  async function startDownload() {
    try {
      const j = await api.post('/local-llm/download', {})
      setJob(j)
    } catch (e) {
      toast(couldnt('start the download', e), 'error', 8000)
    }
  }

  async function cancelDownload() {
    try { await api.post('/local-llm/download/cancel') } catch { /* job resolves via poll */ }
  }

  async function removeModel() {
    try {
      await api.delete('/local-llm/model')
      toast('Model removed', 'info')
      refresh()
    } catch (e) {
      toast(couldnt('remove', e), 'error')
    }
  }

  if (!status) return <p className="text-sm text-[#94a3b8]">Checking the model on this computer…</p>

  const downloading = job && (job.status === 'queued' || job.status === 'running')
  const pct = downloading && job.total ? Math.round((job.processed / job.total) * 100) : 0

  return (
    <div className="bg-[#13151f] border border-[#2d3148] rounded-lg px-4 py-3 space-y-3">
      {!status.extra_installed ? (
        <>
          <p className="text-sm text-[#e2e8f0]">The local-llm add-on isn't installed.</p>
          <p className="text-xs text-[#94a3b8]">Restart the app with it included:</p>
          <code className="block text-xs bg-[#0f1117] border border-[#2d3148] rounded-md px-3 py-2 text-[#a5b4fc] overflow-x-auto">
            {status.install_hint.uvx}
          </code>
        </>
      ) : downloading ? (
        <>
          <div className="flex items-center justify-between">
            <p className="text-sm text-[#e2e8f0]">Downloading model… {pct}%</p>
            <button onClick={cancelDownload} className="text-xs text-[#94a3b8] hover:text-[#e2e8f0]">Cancel</button>
          </div>
          <div className="h-1.5 rounded-full bg-[#1e2235] overflow-hidden">
            <div className="h-full bg-[#6366f1] transition-all" style={{ width: `${pct}%` }} />
          </div>
          <p className="text-xs text-[#94a3b8]">
            Safe to keep using the app. If the download is interrupted, it resumes where it left off.
          </p>
        </>
      ) : status.model_present ? (
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-[#e2e8f0]">
            Model ready <span className="text-xs text-[#94a3b8]">({status.quant}, {(status.size / 1024 ** 3).toFixed(1)}GB on disk)</span>
          </p>
          <button onClick={removeModel} className="text-xs text-[#f87171] hover:opacity-80">Remove model</button>
        </div>
      ) : (
        <>
          {!status.preflight.ok && (
            <p className="text-xs text-[#fdba74]">{status.preflight.problems.join(' ')}</p>
          )}
          <div className="flex items-center gap-3">
            <button
              onClick={startDownload}
              className="bg-[#4f46e5] text-white px-4 py-2 rounded-md text-sm font-medium hover:opacity-90"
            >
              {status.download_partial ? 'Resume download' : 'Enable local AI'}
            </button>
            <span className="text-xs text-[#94a3b8]">
              Downloads {(status.size / 1024 ** 3).toFixed(1)}GB once, into your app data folder.
            </span>
          </div>
        </>
      )}
    </div>
  )
}

// A sentence on what is sorting payments right now, from the saved settings
// and what the server can reach. Plain words first; the controls come after it.
function AiStatus({ saved, health, localReady }) {
  let ok = false
  let text = null
  if (saved.llm_provider === 'none') {
    text = health?.ai_message || 'Sorting is using your rules and remembered payees only. Add an AI model to sort the rest.'
  } else if (saved.llm_provider === 'ollama') {
    if (!health) return null
    ok = health.ollama === 'ok'
    text = ok
      ? `Sorting uses ${saved.ollama_model || 'your Ollama model'} through Ollama on your computer. It is reachable.`
      : health.ai_message || "Ollama isn't reachable right now, so sorting is using your rules and remembered payees only."
  } else if (saved.llm_provider === 'local') {
    ok = localReady === true
    text = ok
      ? 'Sorting uses the model on this computer.'
      : "The model on this computer isn't ready yet, so sorting is using your rules and remembered payees only."
  } else {
    text = `Sorting uses ${saved.llm_model || 'your model'}${saved.llm_base_url ? ` at ${saved.llm_base_url}` : ''}. Use Test connection to check that it is reachable.`
  }
  return (
    <p
      role="status"
      className="flex items-start gap-2 rounded-lg border border-[#2d3148] bg-[#13151f] px-4 py-3 text-sm leading-relaxed text-[#e2e8f0]"
    >
      <span
        aria-hidden="true"
        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${ok ? 'bg-[#4ade80]' : 'bg-[#f59e0b]'}`}
      />
      <span>{text}</span>
    </p>
  )
}

// What Finance Copilot remembers from each statement, so it can sort similar
// payments on its own. Update remembers what is missing; Forget drops what was
// remembered from one statement (its payments and categories stay).
function RememberedStatements() {
  const toast = useToast()
  const [statements, setStatements] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [forgetting, setForgetting] = useState(null)

  const load = useCallback(() => {
    setError(null)
    return api.get('/statements').then(setStatements).catch(setError)
  }, [])
  useEffect(() => { load() }, [load])

  const name = s => `${s.bank_name} - ${formatMonth(s.statement_month)}`

  async function update(s) {
    setBusy(s.id)
    try {
      const result = await api.post('/embeddings/generate', { statement_id: s.id })
      toast(result.embedded ? `Remembered ${count(result.embedded, 'more payment')}` : 'Nothing new to remember', 'success')
      await load()
    } catch (e) {
      toast(couldnt('update remembered payees', e), 'error')
    } finally {
      setBusy(null)
    }
  }

  async function forget() {
    const s = forgetting
    setForgetting(null)
    setBusy(s.id)
    try {
      await api.delete(`/embeddings/statement/${s.id}`)
      toast(`Forgot what was remembered from ${name(s)}`, 'info')
      await load()
    } catch (e) {
      toast(couldnt('forget them', e), 'error')
    } finally {
      setBusy(null)
    }
  }

  if (error) return <InlineError what="your statements" error={error} onRetry={load} compact />
  if (statements == null) return <p className="text-xs text-[#94a3b8]">Loading…</p>
  if (statements.length === 0) return <p className="text-xs text-[#94a3b8]">Nothing is remembered yet. Add a statement first.</p>

  return (
    <>
      <ul className="divide-y divide-[#2d3148] rounded-lg border border-[#2d3148] bg-[#13151f]">
        {statements.map(s => (
          <li key={s.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-sm">
            <span className="min-w-0 flex-1 text-[#e2e8f0]">{name(s)}</span>
            <span className="text-xs tabular-nums text-[#94a3b8]">
              {s.embedded_count} of {count(s.txn_count, 'payment')} remembered
            </span>
            <span className="flex items-center gap-3 text-xs">
              <button
                onClick={() => update(s)}
                disabled={busy === s.id || s.embedded_count >= s.txn_count}
                aria-label={`Update remembered payees for ${name(s)}`}
                className="text-[#818cf8] hover:text-[#a5b4fc] disabled:opacity-40"
              >
                {busy === s.id ? 'Working…' : 'Update'}
              </button>
              <button
                onClick={() => setForgetting(s)}
                disabled={busy === s.id || s.embedded_count === 0}
                aria-label={`Forget remembered payees for ${name(s)}`}
                className="text-[#f87171] hover:opacity-80 disabled:opacity-40"
              >
                Forget
              </button>
            </span>
          </li>
        ))}
      </ul>
      <ConfirmDialog
        open={!!forgetting}
        danger
        title="Forget what was remembered?"
        description={`Finance Copilot will stop using the ${count(forgetting?.embedded_count ?? 0, 'payment')} remembered from "${forgetting ? name(forgetting) : ''}" to sort similar payments. Your payments and categories are kept. Use Update to remember them again.`}
        confirmLabel="Forget"
        onConfirm={forget}
        onCancel={() => setForgetting(null)}
      />
    </>
  )
}

// Settings › AI & Model: which AI sorts your payments, in plain words first,
// then the provider choice (bring your own model: provider, endpoint, model,
// key) with a test-connection button. What the machinery does behind it sits
// under Advanced.
export default function ModelSettings() {
  const toast = useToast()
  const [cfg, setCfg] = useState(null)
  // The settings as stored, which the status sentence describes; `cfg` also
  // holds what is typed but not saved yet.
  const [saved, setSaved] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState(null)
  const [health, setHealth] = useState(null)
  const [localReady, setLocalReady] = useState(null)
  const [advanced, setAdvanced] = useState(false)
  // Missing field (older server) or health not loaded yet: assume available.
  const builtinAvailable = health?.local_embeddings_available !== false

  const [loadError, setLoadError] = useState(null)
  const loadConfig = useCallback(() => {
    setLoadError(null)
    api.get('/config/llm')
      .then(c => { setCfg({ ...c, llm_api_key: '' }); setSaved(c) })
      .catch(setLoadError)
      .finally(() => setLoading(false))
    api.get('/health').then(setHealth).catch(() => {})
  }, [])
  useEffect(() => { loadConfig() }, [loadConfig])

  // Whether the model on this computer is there, for the status sentence.
  useEffect(() => {
    if (saved?.llm_provider !== 'local') return
    api.get('/local-llm/status').then(st => setLocalReady(!!st.model_present)).catch(() => setLocalReady(false))
  }, [saved?.llm_provider])

  function set(key, value) {
    setCfg(c => ({ ...c, [key]: value }))
    setResult(null)
  }

  // Resolves true once the config is persisted; false when the save failed
  // (already toasted), so callers that depend on it can stop.
  async function save() {
    setSaving(true)
    try {
      const payload = {
        llm_provider: cfg.llm_provider,
        ollama_url: cfg.ollama_url,
        ollama_model: cfg.ollama_model,
        llm_base_url: cfg.llm_base_url,
        llm_model: cfg.llm_model,
        embedding_provider: cfg.embedding_provider,
      }
      // Only send the key when the user typed a new one (blank = keep existing).
      if (cfg.llm_api_key) payload.llm_api_key = cfg.llm_api_key
      const updated = await api.put('/config/llm', payload)
      setCfg({ ...updated, llm_api_key: '' })
      setSaved(updated)
      api.get('/health').then(setHealth).catch(() => {})
      toast('AI settings saved', 'success')
      // Matching payments a different way means everything remembered must be
      // rebuilt before matching works again. Start it right away, in the
      // background; matching (not the app) is paused while it runs.
      if (updated.embedding_model_changed) {
        try {
          await api.post('/embeddings/reembed')
          toast('Updating remembered payees in the background - matching from history resumes when it finishes', 'info', 8000)
        } catch (e) {
          toast(couldnt('start updating remembered payees', e), 'error')
        }
      }
      return true
    } catch (e) {
      toast(couldnt('save', e), 'error')
      return false
    } finally {
      setSaving(false)
    }
  }

  async function test() {
    setTesting(true)
    setResult(null)
    try {
      // Test hits the *saved* config, so persist first. If that failed, a probe
      // would validate the previous config and could show a misleading green.
      if (!(await save())) {
        setResult({ ok: false, detail: 'Settings were not saved, so nothing was tested.' })
        return
      }
      const r = await api.post('/config/llm/test')
      setResult(r)
    } catch (e) {
      setResult({ ok: false, detail: couldnt('test the connection', e) })
    } finally {
      setTesting(false)
    }
  }

  if (loadError) return <InlineError what="your model settings" error={loadError} onRetry={loadConfig} />
  if (loading || !cfg) return <p className="text-sm text-[#94a3b8]">Loading…</p>

  const provider = cfg.llm_provider

  return (
    <div className="space-y-6 max-w-xl">
      <div>
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">AI model</h2>
        <p className="text-xs text-[#94a3b8] mb-3 leading-relaxed">
          Rules and your remembered payees sort what they can. A model, if you connect
          one, takes the payments they can't resolve on their own.
        </p>
        {saved && <div className="mb-3"><AiStatus saved={saved} health={health} localReady={localReady} /></div>}
        <div className="space-y-2">
          {PROVIDERS.map(p => (
            <label
              key={p.id}
              className={`flex items-start gap-3 px-4 py-3 rounded-lg border cursor-pointer transition-colors ${
                provider === p.id ? 'border-[#6366f1] bg-[#13151f]' : 'border-[#2d3148] hover:border-[#3d4468]'
              }`}
            >
              <input
                type="radio"
                name="provider"
                checked={provider === p.id}
                onChange={() => set('llm_provider', p.id)}
                className="mt-0.5 accent-[#6366f1]"
              />
              <div>
                <div className="text-sm text-[#e2e8f0]">{p.label}</div>
                <div className="text-xs text-[#94a3b8] mt-0.5">{p.hint}</div>
              </div>
            </label>
          ))}
        </div>
      </div>

      {provider === 'ollama' && (
        <div className="space-y-3">
          <div>
            <label className={label}>Ollama URL</label>
            <input className={field} value={cfg.ollama_url ?? ''} onChange={e => set('ollama_url', e.target.value)} placeholder="http://localhost:11434" />
          </div>
          <div>
            <label className={label}>Model</label>
            <input className={field} value={cfg.ollama_model ?? ''} onChange={e => set('ollama_model', e.target.value)} placeholder="qwen3.5:4b" />
          </div>
        </div>
      )}

      {provider === 'local' && <LocalAICard />}

      {provider === 'openai' && (
        <div className="space-y-3">
          <div>
            <label className={label}>Endpoint URL</label>
            <input className={field} value={cfg.llm_base_url ?? ''} onChange={e => set('llm_base_url', e.target.value)} placeholder="https://api.openai.com/v1" />
          </div>
          <div>
            <label className={label}>Model</label>
            <input className={field} value={cfg.llm_model ?? ''} onChange={e => set('llm_model', e.target.value)} placeholder="gpt-4o-mini" />
          </div>
          <div>
            <label className={label}>
              API key {cfg.llm_api_key_set && <span className="text-[#4ade80]">· a key is saved</span>}
            </label>
            <input
              type="password"
              className={field}
              value={cfg.llm_api_key ?? ''}
              onChange={e => set('llm_api_key', e.target.value)}
              placeholder={cfg.llm_api_key_set ? '•••••••• (leave blank to keep)' : 'sk-…'}
            />
          </div>
        </div>
      )}

      {/* Advanced: the machinery behind sorting. */}
      <div className="rounded-lg border border-[#2d3148]">
        <button
          type="button"
          onClick={() => setAdvanced(a => !a)}
          aria-expanded={advanced}
          aria-controls="advanced-model-settings"
          className="flex w-full items-center justify-between px-4 py-3 text-sm text-[#e2e8f0] hover:bg-[#13151f] rounded-lg transition-colors"
        >
          Advanced
          <ChevronDown size={15} className={`text-[#94a3b8] transition-transform ${advanced ? 'rotate-180' : ''}`} />
        </button>
        {advanced && (
          <div id="advanced-model-settings" className="space-y-5 border-t border-[#2d3148] px-4 py-4">
            <div>
              <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Remembered payees</h2>
              <p className="text-xs text-[#94a3b8] leading-relaxed">
                Finance Copilot remembers how you sorted past payments, so it can sort
                similar ones on its own. All of it stays on your computer.
              </p>
            </div>

            {health && !health.ai_message && health.embeddings === 'unavailable' && (
              <p className="text-xs text-[#94a3b8] bg-[#13151f] border border-[#2d3148] rounded-md px-3 py-2 leading-relaxed">
                {health.embedding_provider === 'local'
                  ? <>Matching similar payments is paused: the built-in matcher isn't available on
                      this Python version. Switch to Ollama below, or run{' '}
                      <code className="text-[#c7d2fe]">uvx --python 3.14 finance-copilot</code>.</>
                  : builtinAvailable
                    ? <>Matching similar payments is paused because Ollama isn't reachable. Switch
                        to Built-in below to run it inside the app instead.</>
                    : <>Matching similar payments is paused because Ollama isn't reachable.</>}
              </p>
            )}

            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-[#94a3b8] mb-1.5">How payments are matched</h3>
              <p className="text-xs text-[#94a3b8] mb-2 leading-relaxed">
                Changing this rebuilds what is remembered. It runs in the background and
                matching pauses until it's done.
              </p>
              <div className="space-y-2">
                {MATCH_PROVIDERS.map(p => {
                  const disabled = p.id === 'local' && !builtinAvailable
                  return (
                    <label
                      key={p.id}
                      className={`flex items-start gap-3 px-4 py-3 rounded-lg border transition-colors ${
                        disabled
                          ? 'border-[#2d3148] opacity-60 cursor-not-allowed'
                          : cfg.embedding_provider === p.id
                            ? 'border-[#6366f1] bg-[#13151f] cursor-pointer'
                            : 'border-[#2d3148] hover:border-[#3d4468] cursor-pointer'
                      }`}
                    >
                      <input
                        type="radio"
                        name="embedding_provider"
                        checked={cfg.embedding_provider === p.id}
                        disabled={disabled}
                        onChange={() => set('embedding_provider', p.id)}
                        className="mt-0.5 accent-[#6366f1]"
                      />
                      <div>
                        <div className="text-sm text-[#e2e8f0]">{p.label}</div>
                        <div className="text-xs text-[#94a3b8] mt-0.5">
                          {disabled
                            ? <>Not available on this Python version. Run <code className="text-[#cbd5e1]">uvx --python 3.14 finance-copilot</code> to use it.</>
                            : p.hint}
                        </div>
                      </div>
                    </label>
                  )
                })}
              </div>
            </div>

            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-[#94a3b8] mb-1.5">By statement</h3>
              <RememberedStatements />
            </div>
          </div>
        )}
      </div>

      {/* Test result banner */}
      {result && (
        <div className={`flex items-start gap-2 px-3 py-2.5 rounded-md text-sm ${
          result.ok ? 'bg-[#0f2419] text-[#4ade80] border border-[#1f5138]' : 'bg-[#2a1518] text-[#f87171] border border-[#5b2731]'
        }`}>
          {result.ok ? <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> : <XCircle size={16} className="mt-0.5 shrink-0" />}
          <span>{result.detail}</span>
        </div>
      )}

      <div className="flex gap-3 pt-1">
        <button
          onClick={save}
          disabled={saving}
          className="bg-[#4f46e5] text-white px-4 py-2 rounded-md text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          onClick={test}
          disabled={testing || provider === 'none'}
          className="flex items-center gap-1.5 bg-[#13151f] border border-[#2d3148] text-[#cbd5e1] px-4 py-2 rounded-md text-sm hover:border-[#6366f1] hover:text-[#e2e8f0] disabled:opacity-50 transition-colors"
        >
          {testing ? <Loader2 size={14} className="animate-spin" /> : <Plug size={14} />}
          {testing ? 'Testing…' : 'Test connection'}
        </button>
      </div>
    </div>
  )
}
