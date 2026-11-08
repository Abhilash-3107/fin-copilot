import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle, Loader2, Plug } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'

const PROVIDERS = [
  { id: 'ollama', label: 'Ollama', hint: 'Local models on your machine (default). Private, no API key.' },
  { id: 'local', label: 'On this computer', hint: 'Downloads a model once (~2.7GB), then categorization runs fully offline inside the app. Needs the local-llm add-on.' },
  { id: 'openai', label: 'OpenAI-compatible', hint: 'Any /chat/completions endpoint: OpenAI, LM Studio, vLLM, OpenRouter.' },
  { id: 'none', label: 'Disabled', hint: 'No AI. Categorization falls back to rules and your history only.' },
]

const EMBED_PROVIDERS = [
  { id: 'local', label: 'Built-in', hint: 'Runs inside the app - nothing to install. Downloads a small model (~100MB) on first use.' },
  { id: 'ollama', label: 'Ollama', hint: 'nomic-embed-text via your local Ollama.' },
  { id: 'none', label: 'Off', hint: 'No memory: matching from your history is disabled; rules still work.' },
]

const field = 'w-full bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] placeholder:text-[#475569]'
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
        if (j.status === 'failed') { toast(`Download stopped - ${j.error ?? 'unknown error'}`, 'error'); refresh() }
      } catch { /* transient poll error; keep trying */ }
    }, 1000)
    return () => clearInterval(t)
  }, [job, toast])

  async function startDownload() {
    try {
      const j = await api.post('/local-llm/download', {})
      setJob(j)
    } catch (e) {
      toast(e.message, 'error', 8000)
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
      toast(`Couldn't remove - ${e.message}`, 'error')
    }
  }

  if (!status) return <p className="text-sm text-[#475569]">Checking local AI…</p>

  const downloading = job && (job.status === 'queued' || job.status === 'running')
  const pct = downloading && job.total ? Math.round((job.processed / job.total) * 100) : 0

  return (
    <div className="bg-[#13151f] border border-[#2d3148] rounded-lg px-4 py-3 space-y-3">
      {!status.extra_installed ? (
        <>
          <p className="text-sm text-[#e2e8f0]">The local-llm add-on isn't installed.</p>
          <p className="text-xs text-[#64748b]">Restart the app with it included:</p>
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
          <p className="text-xs text-[#64748b]">
            Safe to keep using the app. If the download is interrupted, it resumes where it left off.
          </p>
        </>
      ) : status.model_present ? (
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-[#e2e8f0]">
            Model ready <span className="text-xs text-[#64748b]">({status.quant}, {(status.size / 1024 ** 3).toFixed(1)}GB on disk)</span>
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
              className="bg-[#6366f1] text-white px-4 py-2 rounded-md text-sm font-medium hover:opacity-90"
            >
              {status.download_partial ? 'Resume download' : 'Enable local AI'}
            </button>
            <span className="text-xs text-[#64748b]">
              Downloads {(status.size / 1024 ** 3).toFixed(1)}GB once, into your app data folder.
            </span>
          </div>
        </>
      )}
    </div>
  )
}

// Settings › AI & Model: bring-your-own-model config (provider, endpoint, model,
// key) with a test-connection button - replaces the .env-only setup.
export default function ModelSettings() {
  const toast = useToast()
  const [cfg, setCfg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState(null)
  const [health, setHealth] = useState(null)
  // Missing field (older server) or health not loaded yet: assume available.
  const builtinAvailable = health?.local_embeddings_available !== false

  useEffect(() => {
    api.get('/config/llm')
      .then(c => setCfg({ ...c, llm_api_key: '' }))
      .catch(e => toast(`Couldn't load model settings - ${e.message}`, 'error'))
      .finally(() => setLoading(false))
    api.get('/health').then(setHealth).catch(() => {})
  }, [toast])

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
      toast('Model settings saved', 'success')
      // A different embedding model = a different vector space: everything must
      // be re-embedded before matching from history works again. Kick the
      // background rebuild right away; RAG (not the app) is paused while it runs.
      if (updated.embedding_model_changed) {
        try {
          await api.post('/embeddings/reembed')
          toast('Rebuilding your copilot\'s memory in the background - matching from history resumes when it finishes', 'info', 8000)
        } catch (e) {
          toast(`Couldn't start the memory rebuild - ${e.message}`, 'error')
        }
      }
      return true
    } catch (e) {
      toast(`Couldn't save - ${e.message}`, 'error')
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
      setResult({ ok: false, detail: e.message })
    } finally {
      setTesting(false)
    }
  }

  if (loading || !cfg) return <p className="text-sm text-[#475569]">Loading…</p>

  const provider = cfg.llm_provider

  return (
    <div className="space-y-6 max-w-xl">
      <div>
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">AI provider</h2>
        <p className="text-xs text-[#64748b] mb-3 leading-relaxed">
          Bring your own model. Your copilot uses this to categorize transactions the
          rules and your history can't resolve on their own.
        </p>
        {health?.ai_message && (
          <p className="text-xs text-[#94a3b8] bg-[#13151f] border border-[#2d3148] rounded-md px-3 py-2 mb-3 leading-relaxed">
            {health.ai_message}
          </p>
        )}
        {health && !health.ai_message && health.embeddings === 'unavailable' && (
          <p className="text-xs text-[#94a3b8] bg-[#13151f] border border-[#2d3148] rounded-md px-3 py-2 mb-3 leading-relaxed">
            {health.embedding_provider === 'local'
              ? <>The AI's memory (similar-transaction matching) is paused: built-in embeddings
                  aren't available on this Python version. Switch AI memory to Ollama below, or
                  run <code className="text-[#c7d2fe]">uvx --python 3.14 finance-copilot</code>.</>
              : builtinAvailable
                ? <>The AI's memory (similar-transaction matching) is paused because Ollama isn't
                    reachable. Switch AI memory to Built-in below to run it inside the app instead.</>
                : <>The AI's memory (similar-transaction matching) is paused because Ollama isn't
                    reachable.</>}
          </p>
        )}
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
                <div className="text-xs text-[#64748b] mt-0.5">{p.hint}</div>
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

      <div>
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">AI memory (embeddings)</h2>
        <p className="text-xs text-[#64748b] mb-3 leading-relaxed">
          How your copilot remembers past transactions so similar ones categorize
          themselves. Switching rebuilds the memory from scratch - it runs in the
          background and matching from history pauses until it's done.
        </p>
        <div className="space-y-2">
          {EMBED_PROVIDERS.map(p => {
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
                  <div className="text-xs text-[#64748b] mt-0.5">
                    {disabled
                      ? <>Not available on this Python version. Run <code className="text-[#94a3b8]">uvx --python 3.14 finance-copilot</code> to use it.</>
                      : p.hint}
                  </div>
                </div>
              </label>
            )
          })}
        </div>
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
          className="bg-[#6366f1] text-white px-4 py-2 rounded-md text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
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
