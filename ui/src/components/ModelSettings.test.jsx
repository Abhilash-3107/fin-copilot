import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

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
const serve = ({ cfg = CONFIG, health = { ollama: 'ok' }, statements = [] } = {}) =>
  mockApi.get.mockImplementation(path => Promise.resolve(
    path === '/config/llm' ? cfg : path === '/health' ? health : path === '/statements' ? statements : {}
  ))

describe('ModelSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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
  // Opening it starts the statement list loading: wait for that, so no update lands after the test.
  const openAdvanced = async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Advanced' }))
    await screen.findAllByText(/Nothing is remembered yet|payments? remembered/)
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
    expect(screen.queryByText('Remembered payees')).not.toBeInTheDocument()
    expect(screen.queryByText(/chat\/completions|vLLM|embedding/i)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Advanced' })).toHaveAttribute('aria-expanded', 'false')
    await openAdvanced()
    expect(screen.getByRole('button', { name: 'Advanced' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Remembered payees')).toBeInTheDocument()
    expect(screen.getByText(/sort similar ones on its own/)).toBeInTheDocument()
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

  describe('remembered payees by statement', () => {
    const STATEMENTS = [
      { id: 's1', bank_name: 'Kotak', statement_month: '2026-10', txn_count: 40, embedded_count: 25 },
      { id: 's2', bank_name: 'Kotak', statement_month: '2026-09', txn_count: 30, embedded_count: 0 },
    ]
    beforeEach(() => serve({ statements: STATEMENTS }))

    it('updates one statement, and only offers it where something is missing', async () => {
      mockApi.post.mockResolvedValue({ embedded: 15 })
      render(<ModelSettings />)
      await screen.findByText('AI model')
      await openAdvanced()
      expect(await screen.findByText('25 of 40 payments remembered')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Forget remembered payees for Kotak - Sep 2026' })).toBeDisabled()
      fireEvent.click(screen.getByRole('button', { name: 'Update remembered payees for Kotak - Oct 2026' }))
      await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith('/embeddings/generate', { statement_id: 's1' }))
    })

    it('names what is lost before it forgets, and does nothing on Cancel', async () => {
      render(<ModelSettings />)
      await screen.findByText('AI model')
      await openAdvanced()
      fireEvent.click(await screen.findByRole('button', { name: 'Forget remembered payees for Kotak - Oct 2026' }))
      expect(screen.getByRole('dialog')).toHaveTextContent('Your payments and categories are kept')
      expect(screen.getByRole('dialog')).toHaveTextContent('25 payments remembered from "Kotak - Oct 2026"')
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
      expect(mockApi.delete).not.toHaveBeenCalled()
    })

    it('forgets one statement after the dialog is confirmed', async () => {
      mockApi.delete.mockResolvedValue({ cleared: 25 })
      render(<ModelSettings />)
      await screen.findByText('AI model')
      await openAdvanced()
      fireEvent.click(await screen.findByRole('button', { name: 'Forget remembered payees for Kotak - Oct 2026' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Forget' }))
      await waitFor(() => expect(mockApi.delete).toHaveBeenCalledWith('/embeddings/statement/s1'))
    })
  })
})
