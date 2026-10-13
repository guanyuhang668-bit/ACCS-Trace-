from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..compatibility import ensure_issue_keys_match_project, normalize_project_key
from ..config import Settings
from ..dependencies import get_app_settings, get_session
from ..errors import ConflictError, NotFoundError, ValidationError
from ..models import DatasetRevision, IssueArtifact, Project, Run
from ..schemas import (
    DatasetRevisionSummary,
    ImportResult,
    ProjectCreate,
    ProjectDetail,
    ProjectSummary,
    ProjectUpdate,
)
from ..services.imports import import_dataset


router = APIRouter(prefix="/projects", tags=["projects"])


def _project_summary(db: Session, project: Project) -> ProjectSummary:
    revision_count = db.scalar(
        select(func.count(DatasetRevision.id)).where(DatasetRevision.project_id == project.id)
    ) or 0
    run_count = db.scalar(select(func.count(Run.id)).where(Run.project_id == project.id)) or 0
    return ProjectSummary(
        id=project.id,
        name=project.name,
        description=project.description,
        project_key=project.project_key,
        created_at=project.created_at,
        revision_count=revision_count,
        run_count=run_count,
    )


@router.post("", response_model=ProjectSummary, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_session)) -> ProjectSummary:
    normalized_name = payload.name.strip()
    existing = db.scalar(select(Project).where(func.lower(Project.name) == normalized_name.lower()))
    if existing:
        raise ConflictError(f"A project named '{normalized_name}' already exists")
    project = Project(
        name=normalized_name,
        description=payload.description.strip(),
        project_key=payload.project_key,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_summary(db, project)


@router.get("", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_session)) -> list[ProjectSummary]:
    projects = list(db.scalars(select(Project).order_by(Project.created_at.desc())))
    return [_project_summary(db, project) for project in projects]


@router.patch("/{project_id}", response_model=ProjectSummary)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: Session = Depends(get_session),
) -> ProjectSummary:
    project = db.get(Project, project_id)
    if not project:
        raise NotFoundError("Project", project_id)

    if "project_key" in payload.model_fields_set:
        requested_key = payload.project_key
        try:
            current_key = normalize_project_key(project.project_key)
        except ValueError as exc:
            raise ConflictError(f"The stored project_key is invalid: {exc}") from exc
        if requested_key != current_key:
            existing_run = db.scalar(
                select(Run.id).where(Run.project_id == project.id).limit(1)
            )
            if existing_run:
                raise ConflictError(
                    "project_key cannot be changed after a run has been created for this project"
                )
            if requested_key is not None:
                imported_issue_keys = list(
                    db.scalars(
                        select(IssueArtifact.issue_key)
                        .join(DatasetRevision)
                        .where(DatasetRevision.project_id == project.id)
                    )
                )
                if imported_issue_keys:
                    try:
                        ensure_issue_keys_match_project(requested_key, imported_issue_keys)
                    except ValueError as exc:
                        raise ValidationError(str(exc)) from exc
        project.project_key = requested_key

    if "description" in payload.model_fields_set:
        # ``description`` is non-null in the database; reject an explicit null
        # rather than silently replacing it with an unrelated default.
        if payload.description is None:
            raise ValidationError("description cannot be null")
        project.description = payload.description

    db.commit()
    db.refresh(project)
    return _project_summary(db, project)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str, db: Session = Depends(get_session)) -> ProjectDetail:
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.revisions))
    )
    if not project:
        raise NotFoundError("Project", project_id)
    summary = _project_summary(db, project)
    return ProjectDetail(
        **summary.model_dump(),
        revisions=[DatasetRevisionSummary.model_validate(item) for item in project.revisions],
    )


@router.post("/{project_id}/imports", response_model=ImportResult, status_code=status.HTTP_201_CREATED)
async def upload_project_data(
    project_id: str,
    issues_file: UploadFile = File(...),
    commits_file: UploadFile = File(...),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> ImportResult:
    revision = await import_dataset(db, settings, project_id, issues_file, commits_file)
    warnings: list[str] = []
    if revision.issue_count > 0 and revision.commit_count / revision.issue_count > 1000:
        warnings.append("The commit-to-issue ratio is high; use a conservative candidate window.")
    return ImportResult(
        revision=DatasetRevisionSummary.model_validate(revision),
        warnings=warnings,
    )
