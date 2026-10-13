from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from .compatibility import normalize_project_key
from .models import ReliabilityLevel, ReviewStatus, RunMode, RunStatus


def _serialize_utc_datetime(value: datetime) -> str:
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


UTCDateTime = Annotated[
    datetime,
    PlainSerializer(_serialize_utc_datetime, return_type=str, when_used="json"),
]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=2000)
    project_key: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("name must contain at least two non-whitespace characters")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("project_key")
    @classmethod
    def normalize_project_key_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalize_project_key(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # ``None`` is meaningful when explicitly supplied: it clears a key before
    # the project has been used.  The API checks ``model_fields_set`` to
    # distinguish that from an omitted field.
    project_key: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("project_key")
    @classmethod
    def normalize_project_key_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalize_project_key(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("description")
    @classmethod
    def normalize_description_field(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_one_field(self):
        if not self.model_fields_set:
            raise ValueError("at least one project field must be supplied")
        return self


class DatasetRevisionSummary(ORMModel):
    id: str
    issue_count: int
    commit_count: int
    issues_sha256: str
    commits_sha256: str
    schema_version: str
    created_at: UTCDateTime


class ProjectSummary(ORMModel):
    id: str
    name: str
    description: str
    project_key: str | None
    created_at: UTCDateTime
    revision_count: int = 0
    run_count: int = 0


class ProjectDetail(ProjectSummary):
    revisions: list[DatasetRevisionSummary] = Field(default_factory=list)


class ImportResult(BaseModel):
    revision: DatasetRevisionSummary
    warnings: list[str] = Field(default_factory=list)


class CalibrationProfileSummary(ORMModel):
    id: str
    name: str
    temperature: float
    threshold_low: float
    threshold_high: float
    source_domain: str
    is_deployment_validated: bool
    metrics_json: dict[str, Any]


class ModelReleaseSummary(ORMModel):
    id: str
    name: str
    version: str
    runtime_kind: str
    source_domain: str
    is_demo: bool
    is_available: bool
    package_path: str | None = None
    manifest_path: str | None = None
    checksum_sha256: str | None = None
    model_scope: str | None = None
    project_key_strategy: str | None = None
    supported_project_keys: list[str] | None = None
    metadata_json: dict[str, Any]
    calibration_profiles: list[CalibrationProfileSummary] = Field(default_factory=list)


class ModelRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    package_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
    )

    @field_validator("package_name")
    @classmethod
    def require_single_safe_directory_name(cls, value: str) -> str:
        if value != value.strip() or ".." in value:
            raise ValueError("package_name must be one safe directory name")
        return value


class RunCreate(BaseModel):
    project_id: str
    dataset_revision_id: str
    model_release_id: str
    calibration_profile_id: str | None = None
    mode: RunMode = RunMode.RECOVER
    days_before: int = Field(default=3, ge=0, le=365)
    days_after: int = Field(default=14, ge=0, le=365)
    unresolved_window_days: int = Field(default=90, ge=1, le=3650)
    max_candidates_per_issue: int = Field(default=200, ge=1, le=5000)
    threshold_low: float | None = Field(default=None, ge=0, le=1)
    threshold_high: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_thresholds(self):
        if (
            self.threshold_low is not None
            and self.threshold_high is not None
            and self.threshold_low >= self.threshold_high
        ):
            raise ValueError("threshold_low must be lower than threshold_high")
        return self


class RunSummary(ORMModel):
    id: str
    project_id: str
    dataset_revision_id: str
    model_release_id: str
    calibration_profile_id: str | None
    mode: str
    status: str
    stage: str
    progress: int
    cancel_requested: bool
    config_json: dict[str, Any]
    summary_json: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: UTCDateTime
    started_at: UTCDateTime | None
    completed_at: UTCDateTime | None


class AuditEventSummary(ORMModel):
    id: str
    event_type: str
    message: str
    payload_json: dict[str, Any]
    created_at: UTCDateTime


class ManifestProject(BaseModel):
    id: str
    name: str
    project_key: str | None


class ManifestDataset(BaseModel):
    revision_id: str
    schema_version: str
    issue_count: int
    commit_count: int
    issues_sha256: str
    commits_sha256: str


class ManifestModel(BaseModel):
    release_id: str
    name: str
    version: str
    runtime_kind: str
    package_sha256: str | None
    source_domain: str
    is_demo: bool
    metadata: dict[str, Any]
    model_scope: str | None = None
    project_key_strategy: str | None = None
    supported_project_keys: list[str] | None = None


class ManifestCalibration(BaseModel):
    profile_id: str
    name: str
    deployment_validated: bool
    temperature: float
    threshold_low: float
    threshold_high: float
    source_domain: str
    metrics: dict[str, Any]


class RunManifest(BaseModel):
    manifest_schema_version: Literal["1.0"] = "1.0"
    run_id: str
    mode: str
    status: str
    project: ManifestProject
    dataset: ManifestDataset
    model: ManifestModel
    deployment_calibration: ManifestCalibration | None
    config: dict[str, Any]
    summary: dict[str, Any]
    created_at: UTCDateTime
    started_at: UTCDateTime | None
    completed_at: UTCDateTime | None


class CandidateSummary(BaseModel):
    id: str
    issue_key: str
    commit_sha: str
    issue_title: str
    commit_title: str
    raw_score: float
    calibrated_probability: float | None
    reliability_level: str
    predicted_link: bool
    time_delta_hours: float
    review_status: str
    review_version: int


class ReviewDecisionSummary(ORMModel):
    id: str
    decision: str
    note: str
    reviewer: str
    previous_version: int
    created_at: UTCDateTime


class CandidateDetail(CandidateSummary):
    issue_text: str
    commit_text: str
    commit_code: str
    issue_created_at: UTCDateTime
    issue_resolved_at: UTCDateTime | None
    commit_date: UTCDateTime
    decisions: list[ReviewDecisionSummary] = Field(default_factory=list)
    model_name: str
    model_version: str
    source_domain: str
    calibration_validated: bool
    threshold_low: float
    threshold_high: float


class CandidatePage(BaseModel):
    items: list[CandidateSummary]
    total: int
    limit: int
    offset: int


class ReviewCreate(BaseModel):
    decision: ReviewStatus
    note: str = Field(default="", max_length=2000)
    reviewer: str = Field(default="local-user", min_length=1, max_length=160)
    expected_version: int = Field(ge=0)

    @field_validator("decision")
    @classmethod
    def require_actual_decision(cls, value: ReviewStatus) -> ReviewStatus:
        if value == ReviewStatus.UNREVIEWED:
            raise ValueError("decision must be accepted, rejected, or skipped")
        return value

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        return value.strip()

    @field_validator("reviewer")
    @classmethod
    def normalize_reviewer(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reviewer must contain a non-whitespace character")
        return normalized


class ExportCreate(BaseModel):
    format: Literal["csv", "json"] = "csv"
    include_rejected: bool = True


class ExportSummary(ORMModel):
    id: str
    run_id: str
    format: str
    sha256: str
    row_count: int
    created_at: UTCDateTime


class AnalyticsResponse(BaseModel):
    counts: dict[str, int]
    review_counts: dict[str, int]
    score_bins: list[dict[str, float | int]]
    calibration_validated: bool
    source_domain: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    database: Literal["ok"]
