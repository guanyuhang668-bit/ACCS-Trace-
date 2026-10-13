from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..errors import NotFoundError, ValidationError
from ..models import AuditEvent, CandidateLink, ExportArtifact, ReliabilityLevel, Run


EXPORT_FIELDS = [
    "Issue_KEY",
    "Commit_SHA",
    "raw_score",
    "calibrated_probability",
    "reliability_level",
    "predicted_link",
    "review_status",
    "reviewer_decision",
    "reviewer_note",
    "model_name",
    "model_version",
    "run_id",
]

SPREADSHEET_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _spreadsheet_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(SPREADSHEET_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def _rows_for_run(db: Session, run: Run, include_rejected: bool) -> list[dict]:
    query = (
        select(CandidateLink)
        .where(CandidateLink.run_id == run.id)
        .options(
            selectinload(CandidateLink.issue),
            selectinload(CandidateLink.commit),
            selectinload(CandidateLink.review_decisions),
        )
        .order_by(CandidateLink.raw_score.desc())
    )
    if not include_rejected:
        query = query.where(CandidateLink.reliability_level != ReliabilityLevel.REJECTED.value)
    candidates = list(db.scalars(query))
    result = []
    for candidate in candidates:
        latest = (
            max(
                candidate.review_decisions,
                key=lambda item: (item.previous_version, item.created_at, item.id),
            )
            if candidate.review_decisions
            else None
        )
        result.append(
            {
                "Issue_KEY": candidate.issue.issue_key,
                "Commit_SHA": candidate.commit.commit_sha,
                "raw_score": round(candidate.raw_score, 8),
                "calibrated_probability": (
                    round(candidate.calibrated_probability, 8)
                    if candidate.calibrated_probability is not None
                    else None
                ),
                "reliability_level": candidate.reliability_level,
                "predicted_link": candidate.predicted_link,
                "review_status": candidate.review_status,
                "reviewer_decision": latest.decision if latest else None,
                "reviewer_note": latest.note if latest else None,
                "model_name": run.model_release.name,
                "model_version": run.model_release.version,
                "run_id": run.id,
            }
        )
    return result


def create_export(
    db: Session,
    settings: Settings,
    run_id: str,
    export_format: str,
    include_rejected: bool,
) -> ExportArtifact:
    run = db.scalar(
        select(Run)
        .where(Run.id == run_id)
        .options(selectinload(Run.model_release))
    )
    if not run:
        raise NotFoundError("Run", run_id)
    if run.status != "completed":
        raise ValidationError("Only completed runs can be exported")
    if export_format not in {"csv", "json"}:
        raise ValidationError(f"Unsupported export format '{export_format}'")
    rows = _rows_for_run(db, run, include_rejected)
    export_dir = settings.data_dir / "exports" / run.id
    export_dir.mkdir(parents=True, exist_ok=True)
    artifact = ExportArtifact(run_id=run.id, format=export_format, file_path="", sha256="", row_count=len(rows))
    db.add(artifact)
    db.flush()
    path = export_dir / f"{artifact.id}.{export_format}"
    try:
        if export_format == "csv":
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS)
                writer.writeheader()
                writer.writerows(
                    {
                        field: _spreadsheet_safe(row.get(field))
                        for field in EXPORT_FIELDS
                    }
                    for row in rows
                )
        else:
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact.file_path = str(path.resolve())
        artifact.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        db.add(
            AuditEvent(
                run_id=run.id,
                event_type="run.exported",
                message=f"{export_format.upper()} export created",
                payload_json={
                    "export_id": artifact.id,
                    "format": export_format,
                    "row_count": len(rows),
                    "include_rejected": include_rejected,
                    "sha256": artifact.sha256,
                },
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    db.refresh(artifact)
    return artifact


def resolve_export_path(settings: Settings, artifact: ExportArtifact) -> Path:
    path = Path(artifact.file_path).resolve()
    export_root = (settings.data_dir / "exports").resolve()
    if not path.is_file() or export_root not in path.parents:
        raise NotFoundError("Export file", artifact.id)
    return path
