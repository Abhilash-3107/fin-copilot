import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle, Loader2, Plug } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'

const PROVIDERS = [
  { id: 'ollama', label: 'Ollama', hint: 'Local models on your machine (default). Private, no API key.' },
  { id: 'openai', label: 'OpenAI-compatible', hint: 'Any /chat/completions endpoint: OpenAI, LM Studio, vLLM, OpenRouter.' },
  { id: 'none', label: 'Disabled', hint: 'No AI. Categorization falls back to rules and your history only.' },
]

const field = 'w-full bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] placeholder:text-[#475569]'
const label = 'block text-xs text-[#94a3b8] mb-1'

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

  async function save() {
    setSaving(true)
    try {
      const payload = {
        llm_provider: cfg.llm_provider,
        ollama_url: cfg.ollama_url,
        ollama_model: cfg.ollama_model,
        llm_base_url: cfg.llm_base_url,
        llm_model: cfg.llm_model,
      }
      // Only send the key when the user typed a new one (blank = keep existing).
      if (cfg.llm_api_key) payload.llm_api_key = cfg.llm_api_key
      const updated = await api.put('/config/llm', payload)
      setCfg({ ...updated, llm_api_key: '' })
      toast('Model settings saved', 'success')
    } catch (e) {
      toast(`Couldn't save - ${e.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function test() {
    setTesting(true)
    setResult(null)
    try {
      // Test hits the *saved* config, so persist first.
      await save()
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
        {health && !health.ai_message && health.embeddings !== 'ok' && (
          <p className="text-xs text-[#94a3b8] bg-[#13151f] border border-[#2d3148] rounded-md px-3 py-2 mb-3 leading-relaxed">
            The AI's memory (similar-transaction matching) needs Ollama running, so it's paused for now.
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
