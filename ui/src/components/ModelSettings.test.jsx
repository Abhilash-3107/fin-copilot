import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {} }))
// Stable toast, like the real useCallback one (see ToastContext test-mock gotcha).
const noopToast = () => {}
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => noopToast,
}))

// The running job, as the app-wide tracker reports it.
const jobState = vi.hoisted(() => ({ job: null }))
vi.mock('../contexts/AnnotationJobContext.jsx', () => ({
  useAnnotationJob: () => ({ job: jobState.job }),
}))

import ModelSettings from './ModelSettings.jsx'

const CONFIG = {
  llm_provider: 'ollama',
  ollama_url: 'http://localhost:11434',
  ollama_model: 'qwen3.5:4b',
  llm_base_url: '',
  llm_model: '',
  llm_api_key_set: false,
  embedding_provider: 'ollama',
}

// Every route the screen reads; a test overrides the ones it cares about.
const READY = { ready: 36, total: 44, unavailable: null }
const serve = ({ cfg = CONFIG, health = { ollama: 'ok' }, coverage = READY } = {}) =>
  mockApi.get.mockImplementation(path => Promise.resolve(
    path === '/config/llm' ? cfg : path === '/health' ? health : path === '/embeddings/coverage' ? coverage : {}
  ))

describe('ModelSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    jobState.job = null
    serve()
  })

  it('does not probe the old config when saving fails', async () => {
    // Review L3: save() swallowed its error, so the probe then validated the
    // previously saved config and could report green for settings never stored.
    mockApi.put.mockRejectedValue(new Error('disk full'))
    render(<ModelSettings />)
    fireEvent.click(await screen.findByText('Test connection'))
    await waitFor(() =>
      expect(screen.getByText('Settings were not saved, so nothing was tested.')).toBeInTheDocument())
    expect(mockApi.post).not.toHaveBeenCalledWith('/config/llm/test')
  })

  it('saves, then probes, when the save succeeds', async () => {
    mockApi.put.mockResolvedValue(CONFIG)
    mockApi.post.mockResolvedValue({ ok: true, detail: 'qwen3.5:4b responded' })
    render(<ModelSettings />)
    fireEvent.click(await screen.findByText('Test connection'))
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith('/config/llm/test'))
    expect(mockApi.put.mock.invocationCallOrder[0])
      .toBeLessThan(mockApi.post.mock.invocationCallOrder[0])
  })

  const builtinRadio = () => screen.getByText('Built-in').closest('label').querySelector('input')
  // Opening it starts the coverage loading: wait for that, so no update lands after the test.
  const openAdvanced = async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Advanced' }))
    await screen.findByText(/payments? ready to match|No sorted payments/)
  }

  it('disables Built-in matching where the server cannot run it', async () => {
    serve({ health: { ollama: 'ok', local_embeddings_available: false } })
    render(<ModelSettings />)
    await screen.findByText('AI model')
    await openAdvanced()
    await screen.findByText(/Not available on this Python version/)
    expect(builtinRadio()).toBeDisabled()
  })

  it('keeps Built-in matching selectable when the server reports it available', async () => {
    serve({ health: { ollama: 'ok', local_embeddings_available: true } })
    render(<ModelSettings />)
    await screen.findByText('AI model')
    await openAdvanced()
    await screen.findByText('Built-in')
    expect(builtinRadio()).toBeEnabled()
    expect(screen.queryByText(/Not available on this Python version/)).not.toBeInTheDocument()
  })

  it('explains paused matching instead of suggesting Built-in', async () => {
    serve({
      cfg: { ...CONFIG, embedding_provider: 'local' },
      health: { embedding_provider: 'local', embeddings: 'unavailable', local_embeddings_available: false },
    })
    render(<ModelSettings />)
    await screen.findByText('AI model')
    await openAdvanced()
    await screen.findByText(/the built-in matcher isn't available\s+on this Python version/)
    expect(screen.queryByText(/Switch to Built-in/)).not.toBeInTheDocument()
  })

  it('keeps the machinery behind Advanced, in plain words', async () => {
    render(<ModelSettings />)
    await screen.findByText('AI model')
    expect(screen.queryByText('Matching similar payments')).not.toBeInTheDocument()
    expect(screen.queryByText(/chat\/completions|vLLM|embedding/i)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Advanced' })).toHaveAttribute('aria-expanded', 'false')
    await openAdvanced()
    expect(screen.getByRole('button', { name: 'Advanced' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Matching similar payments')).toBeInTheDocument()
    expect(screen.getByText(/sort similar ones on its own/)).toBeInTheDocument()
    // Remembered payees are the rules list in Settings > Rules, a different thing.
    expect(screen.queryByText(/Remembered payees/)).not.toBeInTheDocument()
  })

  describe('status', () => {
    it('leads with which model is sorting and that it is reachable', async () => {
      serve({ health: { ollama: 'ok' } })
      render(<ModelSettings />)
      expect(await screen.findByRole('status')).toHaveTextContent(
        'Sorting uses qwen3.5:4b through Ollama on your computer. It is reachable.')
    })

    it('says sorting uses rules and remembered payees only when Ollama is down', async () => {
      serve({ health: { ollama: 'unavailable', ai_message: "Ollama isn't reachable right now, so sorting is using your rules and remembered payees only." } })
      render(<ModelSettings />)
      expect(await screen.findByRole('status')).toHaveTextContent('rules and remembered payees only')
    })

    it('says the same when no model is chosen', async () => {
      serve({ cfg: { ...CONFIG, llm_provider: 'none' }, health: { ollama: 'ok', ai_message: '' } })
      render(<ModelSettings />)
      expect(await screen.findByRole('status')).toHaveTextContent('Sorting is using your rules and remembered payees only')
    })

    it('does not claim a hosted model is reachable before it is tested', async () => {
      serve({ cfg: { ...CONFIG, llm_provider: 'openai', llm_model: 'gpt-4o-mini', llm_base_url: 'https://api.example.com/v1' }, health: { ollama: 'ok' } })
      render(<ModelSettings />)
      expect(await screen.findByRole('status')).toHaveTextContent('Use Test connection to check that it is reachable')
    })
  })

  describe('ready to match', () => {
    const open = async (opts) => {
      serve(opts)
      render(<ModelSettings />)
      await screen.findByText('AI model')
      await openAdvanced()
    }
    const update = () => screen.queryByRole('button', { name: 'Update now' })

    it('says how many sorted payments are ready, with one Update now when some are missing', async () => {
      mockApi.post.mockResolvedValue({ embedded: 8 })
      await open()
      expect(screen.getByText('36 of 44 payments ready to match')).toBeInTheDocument()
      expect(screen.queryByText(/Forget/)).not.toBeInTheDocument()
      fireEvent.click(update())
      await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith('/embeddings/generate', {}))
      await waitFor(() => expect(update()).toBeEnabled())
    })

    it('has no Update now when everything sortable is ready', async () => {
      await open({ coverage: { ready: 44, total: 44, unavailable: null } })
      expect(screen.getByText('44 of 44 payments ready to match')).toBeInTheDocument()
      expect(update()).not.toBeInTheDocument()
    })

    it('is disabled with the reason when matching is off', async () => {
      await open({ coverage: { ready: 0, total: 44, unavailable: 'Matching similar payments is turned off. Turn it on under Advanced in Settings > AI & Model.' } })
      expect(update()).toBeDisabled()
      expect(update()).toHaveAccessibleDescription(/turned off/)
    })

    it('is disabled with the reason when Ollama cannot be reached', async () => {
      await open({ health: { ollama: 'unavailable', embeddings: 'unavailable', embedding_provider: 'ollama' } })
      expect(update()).toBeDisabled()
      expect(update()).toHaveAccessibleDescription("Ollama isn't reachable right now.")
    })

    it('is disabled while a job runs, and says why', async () => {
      jobState.job = { id: 'j', status: 'running', kind: 'annotate' }
      await open()
      expect(update()).toBeDisabled()
      expect(update()).toHaveAccessibleDescription('Finance Copilot is busy sorting. Try again when it finishes.')
    })

    it('says so when there is nothing sorted to match yet', async () => {
      await open({ coverage: { ready: 0, total: 0, unavailable: null } })
      expect(screen.getByText(/No sorted payments to match yet/)).toBeInTheDocument()
      expect(update()).not.toBeInTheDocument()
    })
  })
})
