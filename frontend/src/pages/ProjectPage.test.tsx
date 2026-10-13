import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { ProjectPage } from './ProjectPage'
import { renderWithProviders } from '../test/render'
import type { ModelRelease, ProjectDetail } from '../types'

const project: ProjectDetail = {
  id: 'project-1',
  name: 'Apache Tika',
  project_key: 'TIKA',
  description: 'Parser maintenance links',
  created_at: '2026-09-01T08:00:00Z',
  revision_count: 1,
  run_count: 0,
  revisions: [{
    id: 'revision-1',
    issue_count: 2,
    commit_count: 3,
    issues_sha256: 'a'.repeat(64),
    commits_sha256: 'b'.repeat(64),
    schema_version: '1.0',
    created_at: '2026-09-01T08:00:00Z',
  }],
}

const baseModel: ModelRelease = {
  id: 'model-base',
  name: 'ACCS',
  version: '1.0.0',
  runtime_kind: 'accs_torchscript_v1',
  source_domain: 'Apache projects',
  is_demo: false,
  is_available: true,
  metadata_json: {},
  calibration_profiles: [],
}

const demoModel: ModelRelease = {
  ...baseModel,
  id: 'model-demo',
  name: 'Deterministic Demo Runtime',
  version: '1.0.0-demo',
  runtime_kind: 'demo',
  source_domain: 'synthetic demo data',
  is_demo: true,
}

function release(overrides: Partial<ModelRelease>): ModelRelease {
  return { ...baseModel, ...overrides }
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stubProjectApi(models: ModelRelease[], projectResponse: ProjectDetail = project) {
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/api/projects/project-1') && init?.method === 'PATCH') {
      const body = JSON.parse(init.body as string) as { project_key: string | null }
      return Promise.resolve(jsonResponse({
        ...projectResponse,
        project_key: body.project_key?.toUpperCase() ?? null,
      }))
    }
    if (url.endsWith('/api/projects/project-1')) return Promise.resolve(jsonResponse(projectResponse))
    if (url.endsWith('/api/models')) return Promise.resolve(jsonResponse(models))
    if (url.includes('/api/runs?project_id=project-1')) return Promise.resolve(jsonResponse([]))
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderProjectPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/projects/:projectId" element={<ProjectPage />} />
    </Routes>,
    '/projects/project-1',
  )
}

function clickSelectOption(label: string) {
  const content = screen.getByText(label, { selector: '.ant-select-item-option-content' })
  const option = content.closest('.ant-select-item-option')
  if (!option) throw new Error(`Select option wrapper not found for ${label}`)
  fireEvent.click(option)
}

afterEach(() => {
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

describe('ProjectPage model selection', () => {
  it('shows the project key and only matching releases without auto-selecting one', async () => {
    stubProjectApi([
      demoModel,
      release({
        id: 'model-tika',
        name: 'Tika ACCS',
        model_scope: 'project',
        project_key_strategy: 'fixed',
        supported_project_keys: ['TIKA'],
      }),
      release({
        id: 'model-hadoop',
        name: 'Hadoop ACCS',
        model_scope: 'project',
        project_key_strategy: 'fixed',
        supported_project_keys: ['HADOOP'],
      }),
    ])
    renderProjectPage()

    expect(await screen.findByText('Apache Tika')).toBeInTheDocument()
    expect(screen.getByText('Project key: TIKA', { exact: true })).toBeInTheDocument()
    const queueButton = screen.getByRole('button', { name: 'Queue recovery run' })
    expect(queueButton).toBeDisabled()

    const select = screen.getByRole('combobox', { name: 'Model release' })
    expect(select).toHaveValue('')
    await userEvent.click(select)
    const tikaOption = await screen.findByRole('option', { name: 'Tika ACCS 1.0.0' })
    expect(tikaOption).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Deterministic Demo Runtime 1.0.0-demo · DEMO' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Hadoop ACCS 1.0.0' })).not.toBeInTheDocument()

    clickSelectOption('Tika ACCS 1.0.0')
    await waitFor(() => expect(queueButton).toBeEnabled())
  }, 10_000)

  it('blocks Recover and explains how to prepare a matching release', async () => {
    stubProjectApi([
      release({
        id: 'model-hadoop',
        name: 'Hadoop ACCS',
        model_scope: 'project',
        project_key_strategy: 'fixed',
        supported_project_keys: ['HADOOP'],
      }),
      release({ id: 'model-missing-scope', name: 'Legacy ACCS' }),
    ])
    renderProjectPage()

    expect(await screen.findByText('No compatible model release')).toBeInTheDocument()
    expect(screen.getByText(/Register a package with an explicit project key declaration/)).toBeInTheDocument()
    expect(screen.getByText('1 real model release blocked')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Queue recovery run' })).toBeDisabled()
    const select = screen.getByRole('combobox', { name: 'Model release' })
    await userEvent.click(select)
    expect(screen.queryByRole('option', { name: 'Hadoop ACCS 1.0.0' })).not.toBeInTheDocument()
  })

  it('keeps the demo available for an old project with no key while blocking an unscoped real release', async () => {
    const oldProject = { ...project, project_key: null }
    stubProjectApi([
      demoModel,
      release({ id: 'model-legacy', name: 'Legacy ACCS' }),
    ], oldProject)
    renderProjectPage()

    expect(await screen.findByText('Project key: Not configured', { exact: true })).toBeInTheDocument()
    expect(screen.getByText('1 real model release blocked')).toBeInTheDocument()
    const select = screen.getByRole('combobox', { name: 'Model release' })
    await userEvent.click(select)
    const demoOption = await screen.findByRole('option', { name: 'Deterministic Demo Runtime 1.0.0-demo · DEMO' })
    expect(demoOption).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Legacy ACCS 1.0.0' })).not.toBeInTheDocument()
    clickSelectOption('Deterministic Demo Runtime 1.0.0-demo · DEMO')
    expect(await screen.findByText('Demo runtime — not an ACCS model')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Queue recovery run' })).toBeEnabled()
  })

  it('persists a project key through PATCH and locks the editor after a run exists', async () => {
    const oldProject = { ...project, project_key: null }
    const fetchMock = stubProjectApi([demoModel], oldProject)
    const rendered = renderProjectPage()

    const keyInput = await screen.findByRole('textbox', { name: 'Project key' })
    await userEvent.type(keyInput, 'tika')
    await userEvent.click(screen.getByRole('button', { name: 'Set key' }))
    expect(await screen.findByText('Project key set to TIKA')).toBeInTheDocument()
    const patchCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')
    expect(patchCall).toBeDefined()
    const [url, init] = patchCall as unknown as [string, RequestInit]
    expect(url).toBe('/api/projects/project-1')
    expect(JSON.parse(init.body as string)).toEqual({ project_key: 'TIKA' })

    rendered.unmount()
    const lockedProject = { ...project, run_count: 1 }
    vi.unstubAllGlobals()
    stubProjectApi([demoModel], lockedProject)
    renderProjectPage()
    expect(await screen.findByText('Locked after first run')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Project key' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Set key' })).toBeDisabled()
  })
})
