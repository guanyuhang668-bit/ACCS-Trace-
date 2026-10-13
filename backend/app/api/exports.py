from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import Settings
from ..dependencies import get_app_settings, get_session
from ..errors import NotFoundError
from ..models import ExportArtifact
from ..schemas import ExportCreate, ExportSummary
from ..services.exports import create_export, resolve_export_path


router = APIRouter(tags=["exports"])


@router.post(
    "/runs/{run_id}/exports",
    response_model=ExportSummary,
    status_code=status.HTTP_201_CREATED,
)
def export_run(
    run_id: str,
    payload: ExportCreate,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> ExportArtifact:
    return create_export(db, settings, run_id, payload.format, payload.include_rejected)


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: str,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> FileResponse:
    artifact = db.get(ExportArtifact, export_id)
    if not artifact:
        raise NotFoundError("Export", export_id)
    path = resolve_export_path(settings, artifact)
    media_type = "text/csv" if artifact.format == "csv" else "application/json"
    return FileResponse(path, media_type=media_type, filename=path.name)

