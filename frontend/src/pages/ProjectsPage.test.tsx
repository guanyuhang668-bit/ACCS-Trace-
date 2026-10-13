import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ProjectsPage } from './ProjectsPage'
import { renderWithProviders } from '../test/render'

afterEach(() => {
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

describe('ProjectsPage', () => {
  it('shows projects returned by the local API and opens project creation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify([
            {
              id: 'project-1',
              name: 'Apache Tika',
              description: 'Parser maintenance links',
              created_at: '2026-09-01T08:00:00Z',
              revision_count: 2,
              run_count: 4,
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )
    renderWithProviders(<ProjectsPage />, '/projects')

    expect(await screen.findByText('Apache Tika')).toBeInTheDocument()
    expect(screen.getByText('Parser maintenance links')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'New project' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('Create project')
    expect(screen.getByLabelText('Project name')).toBeInTheDocument()
  })

  it('sends an optional project key when creating a project', async () => {
    const created = {
      id: 'project-tika',
      name: 'Apache Tika',
      description: 'Parser maintenance links',
      created_at: '2026-09-01T08:00:00Z',
      revision_count: 0,
      run_count: 0,
      project_key: 'TIKA',
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(created), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }))
    vi.stubGlobal('fetch', fetchMock)
    renderWithProviders(<ProjectsPage />, '/projects')

    await screen.findByText('No projects yet. Create one to import Issue and Commit data.')
    await userEvent.click(screen.getByRole('button', { name: 'New project' }))
    await userEvent.type(screen.getByLabelText('Project name'), 'Apache Tika')
    await userEvent.type(screen.getByLabelText('Project key'), 'TIKA')
    await userEvent.click(screen.getByRole('button', { name: 'Create project' }))

    expect(await screen.findByText('Project created')).toBeInTheDocument()
    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({
      name: 'Apache Tika',
      description: '',
      project_key: 'TIKA',
    })
  })
})
