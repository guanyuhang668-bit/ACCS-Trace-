from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeKind(str, enum.Enum):
    DEMO = "demo"
    ACCS_TORCHSCRIPT_V1 = "accs_torchscript_v1"


class RunMode(str, enum.Enum):
    RECOVER = "recover"
    ADAPT = "adapt"
    EVALUATE = "evaluate"


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    VALIDATING = "validating"
    GENERATING = "generating_candidates"
    INFERENCING = "inferencing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ReliabilityLevel(str, enum.Enum):
    RECOMMENDED = "recommended"
    REVIEW = "review"
    REJECTED = "rejected"


class ReviewStatus(str, enum.Enum):
    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # Nullable keeps projects created before project/model compatibility was
    # introduced runnable with the explicitly marked demo runtime.
    project_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    revisions: Mapped[list[DatasetRevision]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    runs: Mapped[list[Run]] = relationship(back_populates="project", cascade="all, delete-orphan")


class DatasetRevision(Base):
    __tablename__ = "dataset_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    issues_filename: Mapped[str] = mapped_column(String(255))
    commits_filename: Mapped[str] = mapped_column(String(255))
    issues_path: Mapped[str] = mapped_column(Text)
    commits_path: Mapped[str] = mapped_column(Text)
    issues_sha256: Mapped[str] = mapped_column(String(64))
    commits_sha256: Mapped[str] = mapped_column(String(64))
    issue_count: Mapped[int] = mapped_column(Integer)
    commit_count: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(32), default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[Project] = relationship(back_populates="revisions")
    issues: Mapped[list[IssueArtifact]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )
    commits: Mapped[list[CommitArtifact]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )
    runs: Mapped[list[Run]] = relationship(back_populates="dataset_revision")


class IssueArtifact(Base):
    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("dataset_revision_id", "issue_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_revision_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_revisions.id", ondelete="CASCADE"), index=True
    )
    issue_key: Mapped[str] = mapped_column(String(160), index=True)
    issue_text: Mapped[str] = mapped_column(Text)
    creation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolution_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    revision: Mapped[DatasetRevision] = relationship(back_populates="issues")
    candidate_links: Mapped[list[CandidateLink]] = relationship(back_populates="issue")


class CommitArtifact(Base):
    __tablename__ = "commits"
    __table_args__ = (UniqueConstraint("dataset_revision_id", "commit_sha"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_revision_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_revisions.id", ondelete="CASCADE"), index=True
    )
    commit_sha: Mapped[str] = mapped_column(String(160), index=True)
    commit_text: Mapped[str] = mapped_column(Text)
    commit_code: Mapped[str] = mapped_column(Text, default="")
    commit_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    revision: Mapped[DatasetRevision] = relationship(back_populates="commits")
    candidate_links: Mapped[list[CandidateLink]] = relationship(back_populates="commit")


class ModelRelease(Base):
    __tablename__ = "model_releases"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(80))
    runtime_kind: Mapped[str] = mapped_column(String(32), default=RuntimeKind.DEMO.value)
    model_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_domain: Mapped[str] = mapped_column(String(255), default="synthetic demo data")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    calibration_profiles: Mapped[list[CalibrationProfile]] = relationship(
        back_populates="model_release", cascade="all, delete-orphan"
    )
    runs: Mapped[list[Run]] = relationship(back_populates="model_release")


class CalibrationProfile(Base):
    __tablename__ = "calibration_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_release_id: Mapped[str] = mapped_column(
        ForeignKey("model_releases.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    temperature: Mapped[float] = mapped_column(Float, default=1.0)
    threshold_low: Mapped[float] = mapped_column(Float, default=0.40)
    threshold_high: Mapped[float] = mapped_column(Float, default=0.75)
    source_domain: Mapped[str] = mapped_column(String(255), default="")
    is_deployment_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    model_release: Mapped[ModelRelease] = relationship(back_populates="calibration_profiles")
    runs: Mapped[list[Run]] = relationship(back_populates="calibration_profile")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    dataset_revision_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_revisions.id", ondelete="RESTRICT"), index=True
    )
    model_release_id: Mapped[str] = mapped_column(
        ForeignKey("model_releases.id", ondelete="RESTRICT"), index=True
    )
    calibration_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("calibration_profiles.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(32), default=RunMode.RECOVER.value)
    status: Mapped[str] = mapped_column(String(48), default=RunStatus.QUEUED.value, index=True)
    stage: Mapped[str] = mapped_column(String(120), default="Queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="runs")
    dataset_revision: Mapped[DatasetRevision] = relationship(back_populates="runs")
    model_release: Mapped[ModelRelease] = relationship(back_populates="runs")
    calibration_profile: Mapped[CalibrationProfile | None] = relationship(back_populates="runs")
    candidates: Mapped[list[CandidateLink]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    exports: Mapped[list[ExportArtifact]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class CandidateLink(Base):
    __tablename__ = "candidate_links"
    __table_args__ = (
        UniqueConstraint("run_id", "issue_id", "commit_id"),
        Index("ix_candidate_run_level_score", "run_id", "reliability_level", "raw_score"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    issue_id: Mapped[str] = mapped_column(ForeignKey("issues.id", ondelete="RESTRICT"), index=True)
    commit_id: Mapped[str] = mapped_column(ForeignKey("commits.id", ondelete="RESTRICT"), index=True)
    raw_score: Mapped[float] = mapped_column(Float)
    calibrated_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability_level: Mapped[str] = mapped_column(String(32), index=True)
    predicted_link: Mapped[bool] = mapped_column(Boolean)
    time_delta_hours: Mapped[float] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(
        String(32), default=ReviewStatus.UNREVIEWED.value, index=True
    )
    review_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[Run] = relationship(back_populates="candidates")
    issue: Mapped[IssueArtifact] = relationship(back_populates="candidate_links")
    commit: Mapped[CommitArtifact] = relationship(back_populates="candidate_links")
    review_decisions: Mapped[list[ReviewDecision]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        order_by=lambda: (
            ReviewDecision.previous_version,
            ReviewDecision.created_at,
            ReviewDecision.id,
        ),
    )


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_links.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default="")
    reviewer: Mapped[str] = mapped_column(String(160), default="local-user")
    previous_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    candidate: Mapped[CandidateLink] = relationship(back_populates="review_decisions")


class ExportArtifact(Base):
    __tablename__ = "export_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(24), default="csv")
    file_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[Run] = relationship(back_populates="exports")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[Run | None] = relationship(back_populates="audit_events")
