import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {} }))
// The real useToast returns a useCallback-stable function; mirror that here so
// components with a [toast]-dependent effect don't loop under test.
const noopToast = () => {}
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => noopToast,
}))

import Settings from './Settings.jsx'

function renderSettings() {
  return render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>
  )
}

describe('Settings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockImplementation(path => {
      if (path === '/config') return Promise.resolve({ dev_mode: false, confidence_threshold: 0.85 })
      if (path === '/annotations/learned-rules') return Promise.resolve([])
      if (path === '/config/llm') return Promise.resolve({ llm_provider: 'ollama', ollama_url: 'http://localhost:11434', ollama_model: 'qwen3.5:4b', llm_base_url: '', llm_model: '', llm_api_key_set: false })
      return Promise.resolve([])
    })
    mockApi.put.mockResolvedValue({ dev_mode: false, confidence_threshold: 0.7 })
  })

  it('renders tabs and defaults to General', async () => {
    renderSettings()
    await waitFor(() => expect(screen.getByText('Developer mode')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /General/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /AI & Model/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Insights/ })).toBeInTheDocument()
  })

  it('shows the review-bar slider on the Insights tab', async () => {
    renderSettings()
    await waitFor(() => expect(screen.getByText('Developer mode')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Insights/ }))
    expect(screen.getByText('Review bar')).toBeInTheDocument()
    expect(screen.getByRole('slider')).toBeInTheDocument()
    // Default 85% shown.
    expect(screen.getByText('85%')).toBeInTheDocument()
  })

  it('loads the BYOM form on the AI & Model tab', async () => {
    renderSettings()
    await waitFor(() => expect(screen.getByText('Developer mode')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /AI & Model/ }))
    await waitFor(() => expect(screen.getByText('AI provider')).toBeInTheDocument())
    expect(screen.getByText('Ollama')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Test connection/ })).toBeInTheDocument()
  })
})
