import type { ModelRelease, ProjectSummary } from './types'

const PROJECT_KEY_PATTERN = /^[A-Za-z0-9_#]+(?:\/[A-Za-z0-9_#]+)*$/

export function isValidProjectKey(value: string): boolean {
  const normalized = value.trim().toUpperCase()
  if (!normalized || normalized.length > 255 || !PROJECT_KEY_PATTERN.test(normalized)) return false
  const segments = normalized.split('/')
  return segments.length === new Set(segments).size
}

export function normalizeProjectKey(value: string | null | undefined): string {
  const normalized = (value ?? '').trim().toUpperCase()
  if (!normalized) return ''
  return normalized.split('/').sort().join('/')
}

export function projectKeyFor(project: Pick<ProjectSummary, 'project_key'>): string {
  const value = project.project_key
  return typeof value === 'string' && isValidProjectKey(value) ? normalizeProjectKey(value) : ''
}

export type ModelScope =
  | { kind: 'global'; keys: null }
  | { kind: 'dynamic'; keys: null }
  | { kind: 'scoped'; keys: Set<string> }
  | { kind: 'missing'; keys: null }

function normalizedSupportedKeys(value: unknown): Set<string> | null {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) return null
  const keys = value as string[]
  if (keys.includes('*')) return keys.length === 1 ? new Set(['*']) : null
  if (keys.some((key) => !isValidProjectKey(key))) return null
  const normalized = keys.map(normalizeProjectKey)
  return normalized.length === new Set(normalized).size ? new Set(normalized) : null
}

/**
 * Resolve the canonical compatibility fields exposed by ModelReleaseSummary.
 * Metadata text is intentionally ignored: null fields mean the backend could
 * not validate a declaration, so the release must remain blocked.
 */
export function modelScope(model: ModelRelease): ModelScope {
  if (model.is_demo) return { kind: 'global', keys: null }

  const keys = normalizedSupportedKeys(model.supported_project_keys)
  if (!keys) return { kind: 'missing', keys: null }
  if (
    model.model_scope === 'issue_key_prefix'
    && model.project_key_strategy === 'issue-key-prefix'
    && (keys.size === 0 || keys.has('*'))
  ) {
    return { kind: 'dynamic', keys: null }
  }
  if (
    model.model_scope === 'project'
    && model.project_key_strategy === 'fixed'
    && keys.size > 0
  ) {
    return keys.has('*') ? { kind: 'dynamic', keys: null } : { kind: 'scoped', keys }
  }
  return { kind: 'missing', keys: null }
}

export function declaredProjectKeys(model: ModelRelease): Set<string> | null {
  const scope = modelScope(model)
  return scope.kind === 'scoped' ? scope.keys : null
}

export function modelMatchesProject(model: ModelRelease, projectKey: string): boolean {
  if (!model.is_available) return false
  const scope = modelScope(model)
  if (scope.kind === 'global') return true
  if (scope.kind === 'missing' || !isValidProjectKey(projectKey)) return false
  const normalized = normalizeProjectKey(projectKey)
  return scope.kind === 'dynamic' || scope.keys.has(normalized)
}

export function modelScopeDescription(model: ModelRelease): string {
  const scope = modelScope(model)
  if (scope.kind === 'global') return 'All projects · demo'
  if (scope.kind === 'dynamic') return 'Any configured project key · issue-key scoped'
  if (scope.kind === 'missing') return 'Project key not declared · blocked'
  return `Projects: ${Array.from(scope.keys).join(', ')}`
}
