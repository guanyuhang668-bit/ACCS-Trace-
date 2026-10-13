import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('API client', () => {
  it('maps structured backend errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: 'conflict', message: 'A project already exists' } }),
          { status: 409, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    await expect(api.createProject({ name: 'Tika', description: '' })).rejects.toMatchObject({
      status: 409,
      code: 'conflict',
      message: 'A project already exists',
    })
  })

  it('encodes candidate filters and pagination', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, limit: 25, offset: 50 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await api.listCandidates('run one', {
      reliability: 'review',
      review: 'unreviewed',
      search: 'TIKA-1',
      limit: 25,
      offset: 50,
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/runs/run one/candidates?limit=25&offset=50&reliability_level=review&review_status=unreviewed&search=TIKA-1',
      undefined,
    )
  })

  it('persists project keys through the project PATCH endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 'project-1', project_key: 'TIKA' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await api.updateProject('project-1', { project_key: 'TIKA' })

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/projects/project-1')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body as string)).toEqual({ project_key: 'TIKA' })
  })
})
