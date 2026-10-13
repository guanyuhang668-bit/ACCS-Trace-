export interface ProjectSummary {
  id: string
  name: string
  project_key?: string | null
  description: string
  created_at: string
  revision_count: number
  run_count: number
}

export interface DatasetRevision {
  id: string
  issue_count: number
  commit_count: number
  issues_sha256: string
  commits_sha256: string
  schema_version: string
  created_at: string
}

export interface ProjectDetail extends ProjectSummary {
  revisions: DatasetRevision[]
}

export interface CalibrationProfile {
  id: string
  name: string
  temperature: number
  threshold_low: number
  threshold_high: number
  source_domain: string
  is_deployment_validated: boolean
  metrics_json: Record<string, unknown>
}

export interface ModelRelease {
  id: string
  name: string
  version: string
  runtime_kind: 'demo' | 'accs_torchscript_v1'
  package_path?: string | null
  manifest_path?: string | null
  checksum_sha256?: string | null
  source_domain: string
  is_demo: boolean
  is_available: boolean
  model_scope?: 'project' | 'issue_key_prefix' | null
  project_key_strategy?: 'fixed' | 'issue-key-prefix' | null
  supported_project_keys?: string[] | null
  metadata_json: Record<string, unknown>
  calibration_profiles: CalibrationProfile[]
}

export type RunStatus =
  | 'queued'
  | 'validating'
  | 'generating_candidates'
  | 'inferencing'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export interface RunSummary {
  id: string
  project_id: string
  dataset_revision_id: string
  model_release_id: string
  calibration_profile_id: string | null
  mode: string
  status: RunStatus
  stage: string
  progress: number
  cancel_requested: boolean
  config_json: Record<string, unknown>
  summary_json: Record<string, string | number | boolean>
  error_code: string | null
  error_message: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export type ReliabilityLevel = 'recommended' | 'review' | 'rejected'
export type ReviewStatus = 'unreviewed' | 'accepted' | 'rejected' | 'skipped'

export interface CandidateSummary {
  id: string
  issue_key: string
  commit_sha: string
  issue_title: string
  commit_title: string
  raw_score: number
  calibrated_probability: number | null
  reliability_level: ReliabilityLevel
  predicted_link: boolean
  time_delta_hours: number
  review_status: ReviewStatus
  review_version: number
}

export interface ReviewDecision {
  id: string
  decision: ReviewStatus
  note: string
  reviewer: string
  previous_version: number
  created_at: string
}

export interface CandidateDetail extends CandidateSummary {
  issue_text: string
  commit_text: string
  commit_code: string
  issue_created_at: string
  issue_resolved_at: string | null
  commit_date: string
  decisions: ReviewDecision[]
  model_name: string
  model_version: string
  source_domain: string
  calibration_validated: boolean
  threshold_low: number
  threshold_high: number
}

export interface CandidatePage {
  items: CandidateSummary[]
  total: number
  limit: number
  offset: number
}

export interface Analytics {
  counts: Record<ReliabilityLevel, number>
  review_counts: Record<ReviewStatus, number>
  score_bins: Array<{ lower: number; upper: number; count: number }>
  calibration_validated: boolean
  source_domain: string
}

export interface ExportArtifact {
  id: string
  run_id: string
  format: 'csv' | 'json'
  sha256: string
  row_count: number
  created_at: string
}

export interface ApiErrorBody {
  error?: { code?: string; message?: string; details?: unknown }
}
