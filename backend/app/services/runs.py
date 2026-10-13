from __future__ import annotations

import traceback
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import timedelta
import math

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ..compatibility import (
    compatibility_fields,
    ensure_issue_keys_match_project,
    ensure_project_model_compatible,
    normalize_project_key,
)
from ..errors import AppError, NotFoundError, ValidationError
from ..models import (
    AuditEvent,
    CalibrationProfile,
    CandidateLink,
    CommitArtifact,
    DatasetRevision,
    IssueArtifact,
    ModelRelease,
    Project,
    ReliabilityLevel,
    Run,
    RunMode,
    RunStatus,
    utc_now,
)
from ..runtime import apply_temperature, get_runtime
from ..schemas import RunCreate


CANCELLATION_CHECK_INTERVAL = 25
INFERENCE_BATCH_SIZE = 128
DATABASE_BATCH_SIZE = 500


def _ensure_revision_matches_project_key(
    db: Session,
    project: Project,
    revision: DatasetRevision,
) -> None:
    if project.project_key is None:
        return
    revision_issue_keys = list(
        db.scalars(
            select(IssueArtifact.issue_key).where(
                IssueArtifact.dataset_revision_id == revision.id
            )
        )
    )
    try:
        ensure_issue_keys_match_project(project.project_key, revision_issue_keys)
    except ValueError as exc:
        raise ValidationError(
            f"The selected dataset revision is incompatible with project_key: {exc}"
        ) from exc


def create_run(db: Session, payload: RunCreate) -> Run:
    if payload.mode != RunMode.RECOVER:
        raise ValidationError(
            f"Run mode '{payload.mode.value}' is not implemented in ACCS-Trace 0.1. "
            "Use 'recover'; Adapt and Evaluate will only be enabled with validated semantics."
        )
    project = db.get(Project, payload.project_id)
    if not project:
        raise NotFoundError("Project", payload.project_id)
    revision = db.get(DatasetRevision, payload.dataset_revision_id)
    if not revision or revision.project_id != project.id:
        raise ValidationError("The dataset revision does not belong to the selected project")
    _ensure_revision_matches_project_key(db, project, revision)
    model = db.get(ModelRelease, payload.model_release_id)
    if not model:
        raise NotFoundError("Model release", payload.model_release_id)
    if not model.is_available:
        raise ValidationError("The selected model release is not available")

    compatibility = ensure_project_model_compatible(project, model)

    calibration = None
    if payload.calibration_profile_id:
        calibration = db.get(CalibrationProfile, payload.calibration_profile_id)
        if not calibration or calibration.model_release_id != model.id:
            raise ValidationError("The calibration profile does not belong to the selected model")
    elif len(model.calibration_profiles) == 1:
        calibration = model.calibration_profiles[0]
    elif len(model.calibration_profiles) > 1:
        raise ValidationError(
            "The selected model has multiple calibration profiles; select one explicitly"
        )

    if calibration:
        if not 0 <= calibration.threshold_low < calibration.threshold_high <= 1:
            raise ValidationError("The selected calibration profile has invalid thresholds")
        if calibration.is_deployment_validated and calibration.temperature <= 0:
            raise ValidationError("The selected deployment calibration has an invalid temperature")

    threshold_low = (
        payload.threshold_low
        if payload.threshold_low is not None
        else calibration.threshold_low if calibration else 0.40
    )
    threshold_high = (
        payload.threshold_high
        if payload.threshold_high is not None
        else calibration.threshold_high if calibration else 0.75
    )
    if threshold_low >= threshold_high:
        raise ValidationError("threshold_low must be lower than threshold_high")

    config = payload.model_dump(mode="json")
    config.update(
        {
            "threshold_low": threshold_low,
            "threshold_high": threshold_high,
            "calibration_validated": bool(calibration and calibration.is_deployment_validated),
            "source_domain": calibration.source_domain if calibration else model.source_domain,
            "project_key": normalize_project_key(project.project_key),
            **compatibility_fields(compatibility),
        }
    )
    run = Run(
        project_id=project.id,
        dataset_revision_id=revision.id,
        model_release_id=model.id,
        calibration_profile_id=calibration.id if calibration else None,
        mode=payload.mode.value,
        config_json=config,
    )
    db.add(run)
    db.flush()
    db.add(
        AuditEvent(
            run_id=run.id,
            event_type="run.created",
            message="Recovery run queued",
            payload_json={"mode": run.mode, "model_version": model.version},
        )
    )
    db.commit()
    db.refresh(run)
    return run


def _set_stage(db: Session, run: Run, status: RunStatus, stage: str, progress: int) -> None:
    run.status = status.value
    run.stage = stage
    run.progress = progress
    db.add(
        AuditEvent(
            run_id=run.id,
            event_type=f"run.{status.value}",
            message=stage,
            payload_json={"progress": progress},
        )
    )
    db.commit()


def _is_cancel_requested(db: Session, run_id: str) -> bool:
    return bool(db.scalar(select(Run.cancel_requested).where(Run.id == run_id)))


def _mark_cancelled(db: Session, run_id: str, stage: str = "Cancelled by user") -> None:
    db.rollback()
    run = db.get(Run, run_id)
    if not run:
        return
    db.execute(delete(CandidateLink).where(CandidateLink.run_id == run_id))
    run.status = RunStatus.CANCELLED.value
    run.stage = stage
    run.progress = 0
    run.summary_json = {}
    run.completed_at = utc_now()
    db.add(
        AuditEvent(
            run_id=run.id,
            event_type="run.cancelled",
            message=stage,
            payload_json={},
        )
    )
    db.commit()


def retry_run(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run", run_id)
    retryable = {RunStatus.FAILED.value, RunStatus.INTERRUPTED.value}
    if run.status not in retryable:
        raise ValidationError(f"Run in status '{run.status}' cannot be retried")
    project = db.get(Project, run.project_id)
    model = db.get(ModelRelease, run.model_release_id)
    revision = db.get(DatasetRevision, run.dataset_revision_id)
    if not project or not model or not revision:
        raise ValidationError("The run project, dataset revision, or model release no longer exists")
    # Runs created before project compatibility existed must not bypass the new
    # guard by moving from failed/interrupted back to the worker queue.
    _ensure_revision_matches_project_key(db, project, revision)
    ensure_project_model_compatible(project, model)
    previous_status = run.status
    previous_error_code = run.error_code
    db.execute(delete(CandidateLink).where(CandidateLink.run_id == run.id))
    run.status = RunStatus.QUEUED.value
    run.stage = "Queued for retry"
    run.progress = 0
    run.cancel_requested = False
    run.summary_json = {}
    run.error_code = None
    run.error_message = None
    run.started_at = None
    run.completed_at = None
    db.add(
        AuditEvent(
            run_id=run.id,
            event_type="run.retried",
            message="Run queued for retry",
            payload_json={
                "previous_status": previous_status,
                "previous_error_code": previous_error_code,
            },
        )
    )
    db.commit()
    db.refresh(run)
    return run


def process_run(session_factory: sessionmaker[Session], run_id: str) -> None:
    with session_factory() as db:
        run = db.scalar(
            select(Run)
            .where(Run.id == run_id)
            .options(
                selectinload(Run.model_release),
                selectinload(Run.calibration_profile),
            )
        )
        if not run:
            raise NotFoundError("Run", run_id)
        if run.status not in {
            RunStatus.QUEUED.value,
            RunStatus.VALIDATING.value,
        }:
            raise ValidationError(f"Run cannot be processed from status '{run.status}'")
        if _is_cancel_requested(db, run.id):
            _mark_cancelled(db, run.id)
            return
        run.started_at = utc_now()
        run.completed_at = None
        run.error_code = None
        run.error_message = None
        run.summary_json = {}
        db.execute(delete(CandidateLink).where(CandidateLink.run_id == run.id))
        db.commit()

        try:
            _set_stage(db, run, RunStatus.VALIDATING, "Validating model and dataset", 5)
            if _is_cancel_requested(db, run.id):
                _mark_cancelled(db, run.id)
                return
            project = db.get(Project, run.project_id)
            if not project:
                raise ValidationError("The run project no longer exists")
            revision = db.get(DatasetRevision, run.dataset_revision_id)
            if not revision:
                raise ValidationError("The run dataset revision no longer exists")
            # Revalidate worker-claimed rows created before this feature and
            # protect against retry/queue paths that predate create_run checks.
            _ensure_revision_matches_project_key(db, project, revision)
            ensure_project_model_compatible(project, run.model_release)
            runtime = get_runtime(run.model_release)

            issues = list(
                db.scalars(
                    select(IssueArtifact)
                    .where(IssueArtifact.dataset_revision_id == revision.id)
                    .order_by(IssueArtifact.issue_key)
                )
            )
            commits = list(
                db.scalars(
                    select(CommitArtifact)
                    .where(CommitArtifact.dataset_revision_id == revision.id)
                    .order_by(CommitArtifact.commit_date, CommitArtifact.commit_sha)
                )
            )
            if not issues or not commits:
                raise ValidationError("The selected dataset revision has no issues or commits")

            _set_stage(db, run, RunStatus.GENERATING, "Generating candidate pairs", 15)
            config = run.config_json
            days_before = int(config["days_before"])
            days_after = int(config["days_after"])
            unresolved_days = int(config["unresolved_window_days"])
            max_per_issue = int(config["max_candidates_per_issue"])
            candidates: list[tuple[IssueArtifact, CommitArtifact, float]] = []
            commit_dates = [commit.commit_date for commit in commits]

            for issue_index, issue in enumerate(issues, start=1):
                if (
                    issue_index == 1 or issue_index % CANCELLATION_CHECK_INTERVAL == 0
                ) and _is_cancel_requested(db, run.id):
                    _mark_cancelled(db, run.id)
                    return
                window_start = issue.creation_date - timedelta(days=days_before)
                base_end = issue.resolution_date or issue.creation_date + timedelta(days=unresolved_days)
                window_end = base_end + timedelta(days=days_after)
                left = bisect_left(commit_dates, window_start)
                right = bisect_right(commit_dates, window_end)
                matches = commits[left:right]
                matches.sort(key=lambda item: abs((item.commit_date - base_end).total_seconds()))
                for commit in matches[:max_per_issue]:
                    delta_hours = (commit.commit_date - issue.creation_date).total_seconds() / 3600
                    candidates.append((issue, commit, delta_hours))

            if _is_cancel_requested(db, run.id):
                _mark_cancelled(db, run.id)
                return
            if not candidates:
                raise ValidationError(
                    "No candidate links were generated. Check project dates and candidate-window settings."
                )

            _set_stage(db, run, RunStatus.INFERENCING, "Scoring candidate links", 30)
            calibration = run.calibration_profile
            temperature = calibration.temperature if calibration else 1.0
            threshold_low = float(config["threshold_low"])
            threshold_high = float(config["threshold_high"])
            deployment_validated = bool(calibration and calibration.is_deployment_validated)

            counts: Counter[str] = Counter()
            pending_rows: list[CandidateLink] = []
            total = len(candidates)
            for batch_start in range(0, total, INFERENCE_BATCH_SIZE):
                if _is_cancel_requested(db, run.id):
                    _mark_cancelled(db, run.id)
                    return
                inference_batch = candidates[batch_start : batch_start + INFERENCE_BATCH_SIZE]
                raw_scores = runtime.score_batch(
                    [(issue, commit) for issue, commit, _delta_hours in inference_batch]
                )
                if len(raw_scores) != len(inference_batch):
                    raise ValidationError(
                        "The model runtime returned a different number of scores than input pairs"
                    )
                for (issue, commit, delta_hours), raw_score in zip(
                    inference_batch, raw_scores, strict=True
                ):
                    if not math.isfinite(raw_score) or not 0 <= raw_score <= 1:
                        raise ValidationError(
                            "The model runtime returned an invalid score; expected a finite value in [0, 1]"
                        )
                    calibrated = (
                        apply_temperature(raw_score, temperature) if deployment_validated else None
                    )
                    decision_score = calibrated if calibrated is not None else raw_score
                    if decision_score >= threshold_high:
                        level = ReliabilityLevel.RECOMMENDED.value
                    elif decision_score >= threshold_low:
                        level = ReliabilityLevel.REVIEW.value
                    else:
                        level = ReliabilityLevel.REJECTED.value
                    counts[level] += 1
                    pending_rows.append(
                        CandidateLink(
                            run_id=run.id,
                            issue_id=issue.id,
                            commit_id=commit.id,
                            raw_score=raw_score,
                            calibrated_probability=calibrated,
                            reliability_level=level,
                            predicted_link=decision_score >= threshold_low,
                            time_delta_hours=delta_hours,
                        )
                    )
                if len(pending_rows) >= DATABASE_BATCH_SIZE:
                    db.add_all(pending_rows)
                    db.commit()
                    pending_rows.clear()
                completed_count = batch_start + len(inference_batch)
                run.progress = min(90, 30 + int(completed_count / total * 60))
                run.stage = f"Scored {completed_count} of {total} candidates"
                db.commit()
            if pending_rows:
                db.add_all(pending_rows)

            if _is_cancel_requested(db, run.id):
                _mark_cancelled(db, run.id)
                return

            summary = {
                "candidate_count": total,
                "issue_count": len(issues),
                "commit_count": len(commits),
                "recommended": counts[ReliabilityLevel.RECOMMENDED.value],
                "review": counts[ReliabilityLevel.REVIEW.value],
                "rejected": counts[ReliabilityLevel.REJECTED.value],
                "calibration_validated": deployment_validated,
                "source_domain": config.get("source_domain", run.model_release.source_domain),
            }
            db.flush()
            completed_at = utc_now()
            result = db.execute(
                update(Run)
                .where(
                    Run.id == run.id,
                    Run.status == RunStatus.INFERENCING.value,
                    Run.cancel_requested.is_(False),
                )
                .values(
                    summary_json=summary,
                    status=RunStatus.COMPLETED.value,
                    stage="Ready for review",
                    progress=100,
                    completed_at=completed_at,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                if _is_cancel_requested(db, run.id):
                    _mark_cancelled(db, run.id)
                    return
                raise ValidationError("The run state changed before completion")
            db.add(
                AuditEvent(
                    run_id=run.id,
                    event_type="run.completed",
                    message="Candidate scoring completed",
                    payload_json=summary,
                )
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            run = db.get(Run, run_id)
            if run:
                run.status = RunStatus.FAILED.value
                run.stage = "Run failed"
                run.error_code = exc.code if isinstance(exc, AppError) else "internal_error"
                run.error_message = exc.message if isinstance(exc, AppError) else str(exc)
                run.completed_at = utc_now()
                db.add(
                    AuditEvent(
                        run_id=run.id,
                        event_type="run.failed",
                        message=run.error_message or "Run failed",
                        payload_json={"error_code": run.error_code},
                    )
                )
                db.commit()
            if not isinstance(exc, AppError):
                traceback.print_exc()
            raise
