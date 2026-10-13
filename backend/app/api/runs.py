from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..dependencies import get_session
from ..errors import NotFoundError, ValidationError
from ..models import AuditEvent, Run, RunStatus, utc_now
from ..schemas import (
    AuditEventSummary,
    ManifestCalibration,
    ManifestDataset,
    ManifestModel,
    ManifestProject,
    RunCreate,
    RunManifest,
    RunSummary,
)
from ..services.runs import create_run, retry_run


router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", response_model=RunSummary, status_code=status.HTTP_201_CREATED)
def queue_run(payload: RunCreate, db: Session = Depends(get_session)) -> Run:
    return create_run(db, payload)


@router.get("", response_model=list[RunSummary])
def list_runs(
    project_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> list[Run]:
    query = select(Run).order_by(Run.created_at.desc()).limit(limit)
    if project_id:
        query = query.where(Run.project_id == project_id)
    return list(db.scalars(query))


@router.get("/{run_id}", response_model=RunSummary)
def get_run(run_id: str, db: Session = Depends(get_session)) -> Run:
    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run", run_id)
    return run


@router.get("/{run_id}/manifest", response_model=RunManifest)
def get_run_manifest(run_id: str, db: Session = Depends(get_session)) -> RunManifest:
    run = db.scalar(
        select(Run)
        .where(Run.id == run_id)
        .options(
            selectinload(Run.project),
            selectinload(Run.dataset_revision),
            selectinload(Run.model_release),
            selectinload(Run.calibration_profile),
        )
    )
    if not run:
        raise NotFoundError("Run", run_id)
    revision = run.dataset_revision
    model = run.model_release
    calibration = run.calibration_profile
    compatibility_json = {
        "model_scope": run.config_json.get("model_scope"),
        "project_key_strategy": run.config_json.get("project_key_strategy"),
        "supported_project_keys": run.config_json.get("supported_project_keys"),
    }
    return RunManifest(
        run_id=run.id,
        mode=run.mode,
        status=run.status,
        project=ManifestProject(
            id=run.project.id,
            name=run.project.name,
            # project_key is a run-creation snapshot.  Reading the live
            # Project row would rewrite history for runs created before the
            # compatibility migration.
            project_key=run.config_json.get("project_key"),
        ),
        dataset=ManifestDataset(
            revision_id=revision.id,
            schema_version=revision.schema_version,
            issue_count=revision.issue_count,
            commit_count=revision.commit_count,
            issues_sha256=revision.issues_sha256,
            commits_sha256=revision.commits_sha256,
        ),
        model=ManifestModel(
            release_id=model.id,
            name=model.name,
            version=model.version,
            runtime_kind=model.runtime_kind,
            package_sha256=model.checksum_sha256,
            source_domain=model.source_domain,
            is_demo=model.is_demo,
            metadata=model.metadata_json,
            **compatibility_json,
        ),
        deployment_calibration=(
            ManifestCalibration(
                profile_id=calibration.id,
                name=calibration.name,
                deployment_validated=calibration.is_deployment_validated,
                temperature=calibration.temperature,
                threshold_low=calibration.threshold_low,
                threshold_high=calibration.threshold_high,
                source_domain=calibration.source_domain,
                metrics=calibration.metrics_json,
            )
            if calibration
            else None
        ),
        config=run.config_json,
        summary=run.summary_json,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@router.get("/{run_id}/events", response_model=list[AuditEventSummary])
def list_run_events(
    run_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_session),
) -> list[AuditEvent]:
    if not db.get(Run, run_id):
        raise NotFoundError("Run", run_id)
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.run_id == run_id)
            .order_by(AuditEvent.created_at, AuditEvent.id)
            .limit(limit)
        )
    )


@router.post("/{run_id}/cancel", response_model=RunSummary)
def cancel_run(run_id: str, db: Session = Depends(get_session)) -> Run:
    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run", run_id)
    if run.status in {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
        RunStatus.INTERRUPTED.value,
    }:
        raise ValidationError(f"Run in status '{run.status}' cannot be cancelled")
    run.cancel_requested = True
    if run.status == RunStatus.QUEUED.value:
        run.status = RunStatus.CANCELLED.value
        run.stage = "Cancelled before processing"
        run.progress = 0
        run.completed_at = utc_now()
        event_type = "run.cancelled"
        message = "Queued run cancelled before processing"
    else:
        event_type = "run.cancel_requested"
        message = "Cancellation requested for active run"
    db.add(
        AuditEvent(
            run_id=run.id,
            event_type=event_type,
            message=message,
            payload_json={"status": run.status},
        )
    )
    db.commit()
    db.refresh(run)
    return run


@router.post("/{run_id}/retry", response_model=RunSummary)
def retry_failed_run(run_id: str, db: Session = Depends(get_session)) -> Run:
    return retry_run(db, run_id)
