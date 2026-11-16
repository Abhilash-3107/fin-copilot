import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { useLocation } from 'react-router-dom'
import { MemoryRouter } from '../test/router.jsx'

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

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import Settings from './Settings.jsx'

function Where() {
  const { pathname, search } = useLocation()
  return <p data-testid="where">{pathname + search}</p>
}

function renderSettings(entry = '/settings') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <PrivacyProvider>
        <Settings />
      </PrivacyProvider>
      <Where />
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
    // "Ollama" appears twice: once as an LLM provider, once as an embedding provider.
    expect(screen.getAllByText('Ollama')).toHaveLength(2)
    expect(screen.getByText('AI memory (embeddings)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Test connection/ })).toBeInTheDocument()
  })

  it('labels learned rules by payee and marks money-in rules', async () => {
    mockApi.get.mockImplementation(path => {
      if (path === '/config') return Promise.resolve({ dev_mode: false, confidence_threshold: 0.85 })
      if (path === '/annotations/learned-rules') return Promise.resolve([
        // No merchant name: the payee key is shown.
        { counterparty_key: 'RAVI KUMAR', merchant: null, category: 'Self Transfers',
          subcategory: 'Self Transfer', support: 3, total: 3, purity: 1, direction: 'credit' },
        { counterparty_key: 'PRACTO TECHNOLO', merchant: 'Practo', category: 'Health',
          subcategory: null, support: 3, total: 3, purity: 1, direction: 'debit' },
      ])
      return Promise.resolve([])
    })
    renderSettings()
    await waitFor(() => expect(screen.getByText('Developer mode')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Rules/ }))
    await waitFor(() => expect(screen.getByText('RAVI KUMAR')).toBeInTheDocument())
    expect(screen.getAllByText('money in')).toHaveLength(1)
    expect(screen.getByText('Practo')).toBeInTheDocument()
  })

  it('opens the tab named in the URL, so a link or a reload lands where it should', async () => {
    renderSettings('/settings?tab=ai')
    await waitFor(() => expect(screen.getByText('AI provider')).toBeInTheDocument())
    expect(screen.queryByText('Developer mode')).not.toBeInTheDocument()
  })

  it('writes the tab into the URL when you pick one, and leaves General bare', async () => {
    renderSettings()
    await waitFor(() => expect(screen.getByText('Developer mode')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Data/ }))
    expect(screen.getByTestId('where')).toHaveTextContent('/settings?tab=data')
    fireEvent.click(screen.getByRole('button', { name: /General/ }))
    expect(screen.getByTestId('where').textContent).toBe('/settings')
  })

  it('falls back to General for a tab that does not exist', async () => {
    renderSettings('/settings?tab=nope')
    await waitFor(() => expect(screen.getByText('Developer mode')).toBeInTheDocument())
  })
})

describe('Settings when the API fails', () => {
  const baseGet = path => {
    if (path === '/config') return Promise.resolve({ dev_mode: false, confidence_threshold: 0.85 })
    if (path === '/config/llm') return Promise.resolve({ llm_provider: 'ollama', ollama_url: 'http://localhost:11434', ollama_model: 'qwen3.5:4b', llm_base_url: '', llm_model: '', llm_api_key_set: false })
    if (path === '/categories/tidy-up') return Promise.resolve({ duplicates: [], orphans: [] })
    return Promise.resolve([])
  }
  const down = () => Promise.reject(new TypeError('Failed to fetch'))

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('says the settings could not load instead of showing defaults, and recovers on Retry', async () => {
    let failing = true
    mockApi.get.mockImplementation(path => (failing && path === '/config' ? down() : baseGet(path)))
    renderSettings()
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load your settings.")
    expect(screen.queryByText('Developer mode')).not.toBeInTheDocument()
    failing = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Developer mode')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps the other tabs usable when only the settings failed', async () => {
    mockApi.get.mockImplementation(path => (path === '/config' ? down() : baseGet(path)))
    renderSettings('/settings?tab=ai')
    expect(await screen.findByText('AI provider')).toBeInTheDocument()
  })

  it('names each failed block on the Rules tab and never shows its empty text', async () => {
    let failing = true
    mockApi.get.mockImplementation(path => (
      failing && ['/rules', '/policies', '/annotations/learned-rules'].includes(path) ? down() : baseGet(path)
    ))
    renderSettings('/settings?tab=rules')
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(3))
    const text = screen.getAllByRole('alert').map(a => a.textContent).join(' ')
    expect(text).toContain("Couldn't load your rules.")
    expect(text).toContain("Couldn't load your policies.")
    expect(text).toContain("Couldn't load what it has learned.")
    expect(screen.queryByText(/Nothing learned yet/)).not.toBeInTheDocument()
    expect(screen.queryByText(/No policies yet/)).not.toBeInTheDocument()
    failing = false
    for (const retry of screen.getAllByRole('button', { name: 'Retry' })) fireEvent.click(retry)
    expect(await screen.findByText(/Nothing learned yet/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says the categories could not load', async () => {
    let failing = true
    mockApi.get.mockImplementation(path => (failing && path.startsWith('/categories') ? down() : baseGet(path)))
    renderSettings('/settings?tab=categories')
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load your categories.")
    failing = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('says the model settings could not load instead of loading forever', async () => {
    let failing = true
    mockApi.get.mockImplementation(path => (failing && path === '/config/llm' ? down() : baseGet(path)))
    renderSettings('/settings?tab=ai')
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load your model settings.")
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument()
    failing = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('AI provider')).toBeInTheDocument()
  })
})
