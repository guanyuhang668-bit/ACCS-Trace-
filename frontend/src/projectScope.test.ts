import { afterEach, describe, expect, it } from 'vitest'
import {
  declaredProjectKeys,
  isValidProjectKey,
  modelMatchesProject,
  modelScope,
  modelScopeDescription,
  normalizeProjectKey,
  projectKeyFor,
} from './projectScope'
import type { ModelRelease } from './types'

const baseModel: ModelRelease = {
  id: 'model-1',
  name: 'ACCS',
  version: '1.0.0',
  runtime_kind: 'accs_torchscript_v1',
  source_domain: 'Apache Tika',
  is_demo: false,
  is_available: true,
  metadata_json: {},
  calibration_profiles: [],
}

function model(overrides: Partial<ModelRelease>): ModelRelease {
  return { ...baseModel, ...overrides }
}

describe('project/model scope matching', () => {
  afterEach(() => window.localStorage.clear())

  it('matches canonical fixed project keys case-insensitively', () => {
    const release = model({
      model_scope: 'project',
      project_key_strategy: 'fixed',
      supported_project_keys: ['TIKA'],
    })
    expect(modelMatchesProject(release, 'tika')).toBe(true)
    expect(modelMatchesProject(release, 'HADOOP')).toBe(false)
    expect(modelScope(release)).toMatchObject({ kind: 'scoped' })
    expect(declaredProjectKeys(release)).toEqual(new Set(['TIKA']))
  })

  it('allows explicitly dynamic issue-key-prefix releases', () => {
    const release = model({
      model_scope: 'issue_key_prefix',
      project_key_strategy: 'issue-key-prefix',
      supported_project_keys: [],
    })
    expect(modelMatchesProject(release, '')).toBe(false)
    expect(modelMatchesProject(release, 'TIKA')).toBe(true)
    expect(modelScopeDescription(release)).toBe('Any configured project key · issue-key scoped')
  })

  it('keeps slash-separated composite project keys intact and canonical', () => {
    const release = model({
      model_scope: 'project',
      project_key_strategy: 'fixed',
      supported_project_keys: ['HADOOP/TIKA'],
    })
    expect(normalizeProjectKey('tika/hadoop')).toBe('HADOOP/TIKA')
    expect(modelMatchesProject(release, 'TIKA/HADOOP')).toBe(true)
    expect(modelMatchesProject(release, 'TIKA')).toBe(false)
    expect(isValidProjectKey('TIKA/TIKA')).toBe(false)
  })

  it('blocks real releases with no project-key declaration but keeps demo global', () => {
    const undeclared = model({ id: 'missing-scope' })
    const demo = model({ id: 'demo', is_demo: true, runtime_kind: 'demo' })
    expect(modelScope(undeclared)).toMatchObject({ kind: 'missing' })
    expect(modelMatchesProject(undeclared, 'TIKA')).toBe(false)
    expect(modelMatchesProject(demo, '')).toBe(true)
    expect(modelScopeDescription(undeclared)).toBe('Project key not declared · blocked')
  })

  it('treats the server project key as the only source of truth', () => {
    window.localStorage.setItem('accstrace.project-key.project-1', 'TIKA')
    expect(projectKeyFor({ project_key: null })).toBe('')
    expect(projectKeyFor({ project_key: 'tika' })).toBe('TIKA')
  })

  it('does not guess a scope from metadata when canonical fields are missing or malformed', () => {
    const missing = model({
      model_scope: null,
      project_key_strategy: null,
      supported_project_keys: null,
      metadata_json: {
        model_scope: 'project',
        project_key_strategy: 'fixed',
        supported_project_keys: ['TIKA'],
      },
    })
    const inconsistent = model({
      model_scope: 'project',
      project_key_strategy: 'issue-key-prefix',
      supported_project_keys: ['TIKA'],
    })
    expect(modelScope(missing)).toMatchObject({ kind: 'missing' })
    expect(modelMatchesProject(missing, 'TIKA')).toBe(false)
    expect(modelScope(inconsistent)).toMatchObject({ kind: 'missing' })
  })
})
