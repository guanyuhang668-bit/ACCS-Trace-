import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ModelsPage } from './ModelsPage'
import { renderWithProviders } from '../test/render'

afterEach(() => vi.unstubAllGlobals())

describe('ModelsPage', () => {
  it('does not present the demo runtime as ACCS', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify([
            {
              id: 'demo-1',
              name: 'Workflow demo',
              version: '1.0',
              runtime_kind: 'demo',
              source_domain: 'synthetic demo data',
              is_demo: true,
              is_available: true,
              metadata_json: {},
              calibration_profiles: [],
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )
    renderWithProviders(<ModelsPage />, '/models')

    expect(await screen.findByText('Demo runtime — not an ACCS model')).toBeInTheDocument()
    expect(screen.getByText('Deterministic heuristic scores support workflow and test verification only.')).toBeInTheDocument()
    expect(screen.getByText('Controlled local package registration')).toBeInTheDocument()
  })

  it('shows local package evidence and distinguishes incomplete API metadata', async () => {
    const checksum = 'a'.repeat(64)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify([
            {
              id: 'accs-ready',
              name: 'ACCS Issue-Commit',
              version: '2.0.0',
              runtime_kind: 'accs_torchscript_v1',
              package_path: 'models/accs-v2',
              manifest_path: 'models/accs-v2/manifest.json',
              checksum_sha256: checksum,
              source_domain: 'Apache projects',
              is_demo: false,
              is_available: true,
              model_scope: 'project',
              project_key_strategy: 'fixed',
              supported_project_keys: ['TIKA'],
              metadata_json: {
                input_schema: 'issue-commit-v1',
              },
              calibration_profiles: [],
            },
            {
              id: 'accs-hidden-evidence',
              name: 'Legacy ACCS record',
              version: '1.0.0',
              runtime_kind: 'accs_torchscript_v1',
              source_domain: 'Unknown',
              is_demo: false,
              is_available: true,
              metadata_json: {},
              calibration_profiles: [],
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )
    renderWithProviders(<ModelsPage />, '/models')

    expect(await screen.findByText('ACCS Issue-Commit')).toBeInTheDocument()
    expect(screen.getByText('Package evidence listed')).toBeInTheDocument()
    expect(screen.getByText('models/accs-v2')).toBeInTheDocument()
    expect(screen.getByText('models/accs-v2/manifest.json')).toBeInTheDocument()
    expect(screen.getByText(checksum)).toBeInTheDocument()
    expect(screen.getByText('Projects: TIKA')).toBeInTheDocument()
    expect(screen.getByText('TIKA')).toBeInTheDocument()
    expect(screen.getByText('Package evidence incomplete')).toBeInTheDocument()
    expect(screen.getAllByText('Not exposed by current API')).toHaveLength(3)
  })

  it('registers a staged package directory and refreshes the release list', async () => {
    const registered = {
      id: 'accs-registered',
      name: 'ACCS Issue-Commit',
      version: '2.0.0',
      runtime_kind: 'accs_torchscript_v1',
      package_path: 'models/accs_release_v2',
      manifest_path: 'models/accs_release_v2/manifest.json',
      checksum_sha256: 'b'.repeat(64),
      source_domain: 'Apache projects',
      is_demo: false,
      is_available: true,
      model_scope: 'project',
      project_key_strategy: 'fixed',
      supported_project_keys: ['TIKA'],
      metadata_json: {
        package_path: 'models/accs_release_v2',
        manifest_path: 'models/accs_release_v2/manifest.json',
        manifest_sha256: 'b'.repeat(64),
      },
      calibration_profiles: [],
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(registered), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify([registered]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    vi.stubGlobal('fetch', fetchMock)
    renderWithProviders(<ModelsPage />, '/models')

    await screen.findByText('No model releases registered.')
    await userEvent.click(screen.getByRole('button', { name: 'Register package' }))
    expect(screen.getByText('Local CPU dependencies are required')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Package directory name'), 'accs_release_v2')
    await userEvent.click(screen.getByRole('button', { name: 'Validate and register' }))

    expect(await screen.findByText('ACCS Issue-Commit')).toBeInTheDocument()
    expect(screen.getByText('Registered ACCS Issue-Commit 2.0.0')).toBeInTheDocument()
    expect(screen.getByText('issue-commit dual-input · 512 tokens')).toBeInTheDocument()
    expect(screen.getByText('No deployment calibration profiles.')).toBeInTheDocument()
    const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit]
    expect(url).toBe('/api/models/register')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ package_name: 'accs_release_v2' })
  })

  it('explains that dynamic project-key scope does not remove domain constraints', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([{
          id: 'dynamic-1',
          name: 'Dynamic ACCS',
          version: '1.0.0',
          runtime_kind: 'accs_torchscript_v1',
          source_domain: 'Apache training projects',
          is_demo: false,
          is_available: true,
          model_scope: 'issue_key_prefix',
          project_key_strategy: 'issue-key-prefix',
          supported_project_keys: [],
          metadata_json: {},
          calibration_profiles: [],
        }]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    renderWithProviders(<ModelsPage />, '/models')

    expect(await screen.findByText('Dynamic project-key scope')).toBeInTheDocument()
    expect(screen.getByText('Any configured project key · issue-key scoped')).toBeInTheDocument()
    expect(screen.getByText('Any valid configured key (no fixed list)')).toBeInTheDocument()
    expect(screen.getByText(/does not establish cross-domain validity/)).toBeInTheDocument()
  })
})
