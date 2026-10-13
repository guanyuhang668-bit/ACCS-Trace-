import type {
  Analytics,
  ApiErrorBody,
  CandidateDetail,
  CandidatePage,
  ExportArtifact,
  ModelRelease,
  ProjectDetail,
  ProjectSummary,
  ReliabilityLevel,
  ReviewStatus,
  RunSummary,
} from './types'

export class ApiError extends Error {
  status: number
  code: string

  constructor(message: string, status: number, code = 'request_failed') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init)
  if (!response.ok) {
    let body: ApiErrorBody | undefined
    try {
      body = (await response.json()) as ApiErrorBody
    } catch {
      body = undefined
    }
    throw new ApiError(
      body?.error?.message ?? `Request failed (${response.status})`,
      response.status,
      body?.error?.code,
    )
  }
  return (await response.json()) as T
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export const api = {
  listProjects: () => request<ProjectSummary[]>('/projects'),
  createProject: (body: { name: string; description: string; project_key?: string }) =>
    request<ProjectSummary>('/projects', jsonInit('POST', body)),
  updateProject: (id: string, body: { project_key: string | null }) =>
    request<ProjectSummary>(`/projects/${id}`, jsonInit('PATCH', body)),
  getProject: (id: string) => request<ProjectDetail>(`/projects/${id}`),
  importDataset: (id: string, issues: File, commits: File) => {
    const body = new FormData()
    body.append('issues_file', issues)
    body.append('commits_file', commits)
    return request<{ revision: ProjectDetail['revisions'][number]; warnings: string[] }>(
      `/projects/${id}/imports`,
      { method: 'POST', body },
    )
  },
  listModels: () => request<ModelRelease[]>('/models'),
  registerModel: (packageName: string) =>
    request<ModelRelease>(
      '/models/register',
      jsonInit('POST', { package_name: packageName }),
    ),
  listRuns: (projectId?: string) =>
    request<RunSummary[]>(`/runs${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  getRun: (id: string) => request<RunSummary>(`/runs/${id}`),
  retryRun: (id: string) => request<RunSummary>(`/runs/${id}/retry`, { method: 'POST' }),
  createRun: (body: {
    project_id: string
    dataset_revision_id: string
    model_release_id: string
    calibration_profile_id: string | null
    mode: 'recover'
    days_before: number
    days_after: number
    unresolved_window_days: number
    max_candidates_per_issue: number
    threshold_low?: number
    threshold_high?: number
  }) => request<RunSummary>('/runs', jsonInit('POST', body)),
  cancelRun: (id: string) => request<RunSummary>(`/runs/${id}/cancel`, { method: 'POST' }),
  listCandidates: (
    runId: string,
    filters: {
      reliability?: ReliabilityLevel
      review?: ReviewStatus
      search?: string
      limit: number
      offset: number
    },
  ) => {
    const params = new URLSearchParams({ limit: String(filters.limit), offset: String(filters.offset) })
    if (filters.reliability) params.set('reliability_level', filters.reliability)
    if (filters.review) params.set('review_status', filters.review)
    if (filters.search) params.set('search', filters.search)
    return request<CandidatePage>(`/runs/${runId}/candidates?${params}`)
  },
  getCandidate: (id: string) => request<CandidateDetail>(`/candidates/${id}`),
  reviewCandidate: (
    id: string,
    body: { decision: ReviewStatus; note: string; reviewer: string; expected_version: number },
  ) => request<CandidateDetail>(`/candidates/${id}/reviews`, jsonInit('POST', body)),
  getAnalytics: (runId: string) => request<Analytics>(`/runs/${runId}/analytics`),
  createExport: (runId: string, format: 'csv' | 'json', includeRejected: boolean) =>
    request<ExportArtifact>(
      `/runs/${runId}/exports`,
      jsonInit('POST', { format, include_rejected: includeRejected }),
    ),
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'An unexpected error occurred.'
}
