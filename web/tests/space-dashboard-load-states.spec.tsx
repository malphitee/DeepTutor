import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import SpaceDashboard from '@/components/space/SpaceDashboard'
import { ApiError } from '@/lib/api'

const fixture = vi.hoisted(() => ({ skills: vi.fn() }))
const t = (key: string) => key
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t, i18n: { language: 'en' } }) }))
vi.mock('@/features/capabilities/useCapabilityCatalog', () => ({ useCapabilityFilter: () => () => false }))
vi.mock('@/lib/session-api', () => ({ listSessions: async () => [] }))
vi.mock('@/lib/notebook-api', () => ({ listNotebooks: async () => [], listNotebookEntries: async () => ({ total: 0 }) }))
vi.mock('@/lib/personas-api', () => ({ listPersonas: async () => [] }))
vi.mock('@/features/knowledge/api/catalog', () => ({ listKnowledgeBases: async () => [] }))
vi.mock('@/lib/skills-api', () => ({ listSkills: fixture.skills }))
vi.mock('@/lib/cli-apps-api', () => ({ getCliApps: async () => ({ apps: [] }) }))
vi.mock('@/components/mcp/surface', () => ({ SPACE_MCP_SURFACE: {}, loadMcpSurface: async () => ({ servers: {} }) }))

beforeEach(() => { fixture.skills.mockReset() })

it('ends failed tile loading with an actionable message and a working retry', async () => {
  fixture.skills.mockRejectedValueOnce(new Error('Request failed: 403'))
  fixture.skills.mockResolvedValue([])
  render(<SpaceDashboard />)
  expect(await screen.findByRole('status')).toHaveTextContent('Some resources could not be loaded. Try again or contact your administrator for help.')
  expect(screen.getByText('Currently unavailable')).toBeInTheDocument()
  expect(screen.queryByText('Request failed: 403')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(fixture.skills).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.queryByText('Currently unavailable')).not.toBeInTheDocument())
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Skills/ })).toHaveTextContent(/0\s*skills/)
})

it('renders a typed server permission denial as administrator-managed instead of a broken link', async () => {
  fixture.skills.mockRejectedValue(new ApiError({
    code: 'http_403', message: 'Internal policy reason', status: 403,
    retryable: false, scope: 'settings',
  }))
  render(<SpaceDashboard />)
  expect(await screen.findByRole('status')).toHaveTextContent('Some resources are managed by your administrator. Contact them to request access.')
  expect(screen.getByText('Managed by your administrator')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /Skills/ })).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Personas/ })).toBeInTheDocument()
  expect(screen.queryByText('Internal policy reason')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
})
