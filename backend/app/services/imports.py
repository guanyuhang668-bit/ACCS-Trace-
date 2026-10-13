from __future__ import annotations

import csv
import hashlib
import io
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..compatibility import ensure_issue_keys_match_project
from ..config import Settings
from ..errors import NotFoundError, ValidationError
from ..models import CommitArtifact, DatasetRevision, IssueArtifact, Project, Run


ISSUE_REQUIRED = {"Issue_KEY", "Issue_Text", "creationdate"}
COMMIT_REQUIRED = {"Commit_SHA", "Commit_Text", "Commit_Code", "commitdate"}

#: Real issue-commit corpora contain individual Commit_Code fields far larger than
#: the standard library's 128 KiB default, which makes ``csv`` raise
#: "_csv.Error: field larger than field limit" while reading a perfectly valid
#: dataset. Raise the limit once for this process so such fields are parsed.
MAX_CSV_FIELD_BYTES = 64 * 1024 * 1024
if csv.field_size_limit() < MAX_CSV_FIELD_BYTES:
    csv.field_size_limit(MAX_CSV_FIELD_BYTES)


@dataclass(slots=True)
class ParsedIssue:
    issue_key: str
    issue_text: str
    creation_date: datetime
    resolution_date: datetime | None


@dataclass(slots=True)
class ParsedCommit:
    commit_sha: str
    commit_text: str
    commit_code: str
    commit_date: datetime


def parse_datetime(value: str, field: str, row_number: int, required: bool = True) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        if required:
            raise ValidationError(
                f"Missing {field} at row {row_number}",
                {"field": field, "row": row_number},
            )
        return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError(
            f"Invalid {field} at row {row_number}; expected an ISO-8601 date",
            {"field": field, "row": row_number, "value": raw},
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decode_csv(content: bytes, filename: str, required_headers: set[str]) -> list[dict[str, str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError(
            f"{filename} is not valid UTF-8. Convert it to UTF-8 and try again."
        ) from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = set(reader.fieldnames or [])
    missing = sorted(required_headers - headers)
    if missing:
        raise ValidationError(
            f"{filename} is missing required columns: {', '.join(missing)}",
            {"missing_columns": missing},
        )
    rows = list(reader)
    if not rows:
        raise ValidationError(f"{filename} contains no data rows")
    return rows


def parse_issues(content: bytes, filename: str) -> list[ParsedIssue]:
    rows = decode_csv(content, filename, ISSUE_REQUIRED)
    parsed: list[ParsedIssue] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        key = (row.get("Issue_KEY") or "").strip()
        text = (row.get("Issue_Text") or "").strip()
        if not key or not text:
            raise ValidationError(
                f"Issue_KEY and Issue_Text are required at row {row_number}",
                {"row": row_number},
            )
        if key in seen:
            raise ValidationError(f"Duplicate Issue_KEY '{key}' at row {row_number}")
        seen.add(key)
        parsed.append(
            ParsedIssue(
                issue_key=key,
                issue_text=text,
                creation_date=parse_datetime(row.get("creationdate", ""), "creationdate", row_number),
                resolution_date=parse_datetime(
                    row.get("resolutiondate", ""), "resolutiondate", row_number, required=False
                ),
            )
        )
    return parsed


def parse_commits(content: bytes, filename: str) -> list[ParsedCommit]:
    rows = decode_csv(content, filename, COMMIT_REQUIRED)
    parsed: list[ParsedCommit] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        sha = (row.get("Commit_SHA") or "").strip()
        text = (row.get("Commit_Text") or "").strip()
        code = (row.get("Commit_Code") or "").strip()
        if not sha or not text:
            raise ValidationError(
                f"Commit_SHA and Commit_Text are required at row {row_number}",
                {"row": row_number},
            )
        if sha in seen:
            raise ValidationError(f"Duplicate Commit_SHA '{sha}' at row {row_number}")
        seen.add(sha)
        parsed.append(
            ParsedCommit(
                commit_sha=sha,
                commit_text=text,
                commit_code=code,
                commit_date=parse_datetime(row.get("commitdate", ""), "commitdate", row_number),
            )
        )
    return parsed


async def read_upload(upload: UploadFile, settings: Settings) -> tuple[bytes, str]:
    filename = Path(upload.filename or "upload.csv").name
    if Path(filename).suffix.lower() != ".csv":
        raise ValidationError(f"{filename} must be a CSV file")
    content = await upload.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise ValidationError(
            f"{filename} exceeds the {settings.max_upload_bytes} byte upload limit"
        )
    return content, filename


async def import_dataset(
    db: Session,
    settings: Settings,
    project_id: str,
    issues_upload: UploadFile,
    commits_upload: UploadFile,
) -> DatasetRevision:
    project = db.get(Project, project_id)
    if not project:
        raise NotFoundError("Project", project_id)

    issues_content, issues_filename = await read_upload(issues_upload, settings)
    commits_content, commits_filename = await read_upload(commits_upload, settings)
    parsed_issues = parse_issues(issues_content, issues_filename)
    parsed_commits = parse_commits(commits_content, commits_filename)
    existing_issue_keys = list(
        db.scalars(
            select(IssueArtifact.issue_key)
            .join(DatasetRevision)
            .where(DatasetRevision.project_id == project.id)
        )
    )
    all_issue_keys = existing_issue_keys + [item.issue_key for item in parsed_issues]
    try:
        resolved_project_key = ensure_issue_keys_match_project(
            project.project_key,
            all_issue_keys,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    # Projects without runs obtain one reproducible key from all revision
    # prefixes.  Once any run exists, the key remains frozen (including null)
    # so historical manifests cannot drift after a later import.
    existing_run = db.scalar(select(Run.id).where(Run.project_id == project.id).limit(1))
    if (
        project.project_key is None
        and resolved_project_key is not None
        and existing_run is None
    ):
        project.project_key = resolved_project_key

    revision = DatasetRevision(
        project_id=project_id,
        issues_filename=issues_filename,
        commits_filename=commits_filename,
        issues_path="",
        commits_path="",
        issues_sha256=hashlib.sha256(issues_content).hexdigest(),
        commits_sha256=hashlib.sha256(commits_content).hexdigest(),
        issue_count=len(parsed_issues),
        commit_count=len(parsed_commits),
    )
    db.add(revision)
    db.flush()

    revision_dir = settings.data_dir / "projects" / project_id / "revisions" / revision.id
    revision_dir.mkdir(parents=True, exist_ok=False)
    issues_path = revision_dir / "issues.csv"
    commits_path = revision_dir / "commits.csv"
    try:
        issues_path.write_bytes(issues_content)
        commits_path.write_bytes(commits_content)
        revision.issues_path = str(issues_path.resolve())
        revision.commits_path = str(commits_path.resolve())

        db.add_all(
            [
                IssueArtifact(
                    dataset_revision_id=revision.id,
                    issue_key=item.issue_key,
                    issue_text=item.issue_text,
                    creation_date=item.creation_date,
                    resolution_date=item.resolution_date,
                )
                for item in parsed_issues
            ]
        )
        db.add_all(
            [
                CommitArtifact(
                    dataset_revision_id=revision.id,
                    commit_sha=item.commit_sha,
                    commit_text=item.commit_text,
                    commit_code=item.commit_code,
                    commit_date=item.commit_date,
                )
                for item in parsed_commits
            ]
        )
        db.commit()
    except Exception:
        db.rollback()
        shutil.rmtree(revision_dir, ignore_errors=True)
        raise
    db.refresh(revision)
    return revision
