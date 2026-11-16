import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {} }))
const noopToast = () => {}
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => noopToast,
}))

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import Groups from './Groups.jsx'

const GROUPS = [
  { id: 'g1', name: 'Goa Trip', labels: [], note: null, summary: { member_count: 0, settle_up: [] } },
]

const renderGroups = () => render(<PrivacyProvider><Groups /></PrivacyProvider>)

describe('Groups', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockResolvedValue(GROUPS)
  })

  it('says the list could not load, never "No groups yet", and recovers on Retry', async () => {
    mockApi.get.mockRejectedValue(new TypeError('Failed to fetch'))
    renderGroups()
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load your groups.")
    expect(screen.queryByText('No groups yet')).not.toBeInTheDocument()
    mockApi.get.mockResolvedValue(GROUPS)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Goa Trip')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows a refused duplicate beside the create field and clears it on typing', async () => {
    mockApi.post.mockRejectedValue(Object.assign(new Error('You already have a group called "Goa Trip".'), { status: 409 }))
    renderGroups()
    await screen.findByText('Goa Trip')
    const field = screen.getByPlaceholderText('New group name…')
    fireEvent.change(field, { target: { value: 'goa trip' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('You already have a group called "Goa Trip".')
    fireEvent.change(field, { target: { value: 'goa trip 2' } })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('merges an edited group into the list', async () => {
    mockApi.patch.mockResolvedValue({ ...GROUPS[0], name: 'Goa 2026' })
    renderGroups()
    await screen.findByText('Goa Trip')
    fireEvent.click(screen.getByRole('button', { name: 'Edit Goa Trip' }))
    fireEvent.change(screen.getByLabelText('Group name'), { target: { value: 'Goa 2026' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Goa 2026')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Goa Trip')).not.toBeInTheDocument())
  })
})
