import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import WorkspaceSettingsSection from '@/features/settings/sections/WorkspaceSettingsSection'
import { getWorkspaceCatalog, type WorkspaceCatalog } from '@/lib/workspaces-api'
import { ApiError } from '@/lib/api'

const fixture = vi.hoisted(() => ({ fetch: vi.fn() }))
const t = (key: string) => key
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t, i18n: { language: 'en' } }) }))

const emptyCatalog: WorkspaceCatalog = { root: '/workspaces', workspaces: [] }
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': 'application/json' },
})

beforeEach(() => {
  fixture.fetch.mockReset()
  vi.stubGlobal('fetch', fixture.fetch)
  window.history.replaceState({}, '', '/settings/workspace')
})
afterEach(() => vi.unstubAllGlobals())

it('preserves a workspace refusal status even when the response is not JSON', async () => {
  fixture.fetch.mockResolvedValue(new Response('Forbidden', { status: 403 }))
  const error = await getWorkspaceCatalog().catch(error => error)
  expect(error).toBeInstanceOf(ApiError)
  expect(error.status).toBe(403)
  expect(error.retryable).toBe(false)
})

it('explains server-denied workspace access without exposing a raw 403 or retry controls', async () => {
  fixture.fetch.mockResolvedValue(json({ detail: 'Learning policy forbids this endpoint' }, 403))
  render(<WorkspaceSettingsSection />)

  expect(await screen.findByRole('heading', { name: 'Workspaces are managed by your administrator' })).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('Contact your administrator to request access.')
  expect(screen.getByRole('link', { name: 'Personal settings' })).toHaveAttribute('href', '/settings/general')
  expect(screen.queryByRole('link', { name: 'Back to chat' })).not.toBeInTheDocument()
  expect(screen.queryByText('Learning policy forbids this endpoint')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'New workspace' })).not.toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('replaces stale editable workspace data when a mutation is denied', async () => {
  const catalog: WorkspaceCatalog = { ...emptyCatalog, workspaces: [{
    workspace_id: 'algebra', kind: 'workspace', follows_root: true,
    display_name: 'Algebra', path: '/workspaces/algebra', archived: false,
    created_at: '', status: 'ready', error: '',
  }] }
  fixture.fetch.mockImplementation((_url: string, init?: RequestInit) => Promise.resolve(
    init?.method === 'PATCH' ? json({ detail: 'Policy changed' }, 403) : json(catalog)
  ))
  render(<WorkspaceSettingsSection />)
  expect(await screen.findByRole('article', { name: 'Algebra' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Rename workspace' }))
  fireEvent.change(screen.getByLabelText('Workspace name'), { target: { value: 'Math' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))

  expect(await screen.findByRole('heading', { name: 'Workspaces are managed by your administrator' })).toBeInTheDocument()
  expect(screen.queryByRole('article', { name: 'Algebra' })).not.toBeInTheDocument()
  expect(screen.queryByText('/workspaces/algebra')).not.toBeInTheDocument()
  expect(screen.queryByText('Policy changed')).not.toBeInTheDocument()
  expect(fixture.fetch).toHaveBeenCalledTimes(2)
})

it('keeps network failures retryable and restores workspace controls after recovery', async () => {
  fixture.fetch.mockRejectedValueOnce(new TypeError('Failed to fetch'))
  fixture.fetch.mockImplementation(() => Promise.resolve(json(emptyCatalog)))
  render(<WorkspaceSettingsSection />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Unable to reach the server')
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'New workspace' })).toBeEnabled())
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Workspaces are managed by your administrator' })).not.toBeInTheDocument()
})
