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

describe('ModelSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockImplementation(path => Promise.resolve(
      path === '/config/llm' ? CONFIG : path === '/health' ? { ollama: true } : {}
    ))
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
})
