from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..compatibility import (
    CompatibilityMetadataError,
    compatibility_fields,
    compatibility_from_package,
    safe_compatibility_from_model,
)
from ..dependencies import get_app_settings, get_session
from ..errors import ConflictError, NotFoundError, ValidationError
from ..model_package import MANIFEST_FILENAME, validate_model_manifest
from ..models import ModelRelease
from ..runtime import get_runtime
from ..schemas import ModelRegistrationRequest, ModelReleaseSummary


router = APIRouter(prefix="/models", tags=["models"])


def _public_summary(model: ModelRelease) -> ModelReleaseSummary:
    metadata = model.metadata_json or {}
    compatibility = safe_compatibility_from_model(model)
    compatibility_json = compatibility_fields(compatibility)
    return ModelReleaseSummary(
        id=model.id,
        name=model.name,
        version=model.version,
        runtime_kind=model.runtime_kind,
        source_domain=model.source_domain,
        is_demo=model.is_demo,
        is_available=model.is_available,
        package_path=metadata.get("package_path"),
        manifest_path=metadata.get("manifest_path"),
        checksum_sha256=model.checksum_sha256,
        **compatibility_json,
        metadata_json=metadata,
        calibration_profiles=model.calibration_profiles,
    )


def _resolve_manifest(settings: Settings, package_name: str) -> Path:
    try:
        models_root = settings.models_dir.resolve(strict=True)
        package_root = (models_root / package_name).resolve(strict=True)
    except OSError as exc:
        raise NotFoundError("Model package", package_name) from exc
    if package_root.parent != models_root or not package_root.is_dir():
        raise ValidationError("The model package must be a direct child of the models directory")
    manifest_path = package_root / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise NotFoundError("Model package manifest", package_name)
    return manifest_path


@router.get("", response_model=list[ModelReleaseSummary])
def list_models(db: Session = Depends(get_session)) -> list[ModelReleaseSummary]:
    models = list(
        db.scalars(
            select(ModelRelease)
            .options(selectinload(ModelRelease.calibration_profiles))
            .order_by(ModelRelease.created_at.desc())
        )
    )
    return [_public_summary(model) for model in models]


@router.post(
    "/register",
    response_model=ModelReleaseSummary,
    status_code=status.HTTP_201_CREATED,
)
def register_model_package(
    payload: ModelRegistrationRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> ModelReleaseSummary:
    manifest_path = _resolve_manifest(settings, payload.package_name)
    package = validate_model_manifest(manifest_path)
    try:
        compatibility = compatibility_from_package(package)
    except CompatibilityMetadataError as exc:
        raise ValidationError(f"The model package compatibility metadata is invalid: {exc}") from exc
    if not compatibility.declared:
        raise ValidationError(
            "The model package must declare supported project keys in manifest metadata "
            "or legacy preprocessing"
        )
    duplicate = db.scalar(
        select(ModelRelease).where(
            ModelRelease.name == package.name,
            ModelRelease.version == package.version,
        )
    )
    if duplicate:
        raise ConflictError(
            f"Model release '{package.name}' version '{package.version}' is already registered"
        )

    relative_package_path = f"models/{payload.package_name}"
    relative_manifest_path = f"{relative_package_path}/{MANIFEST_FILENAME}"
    package_metadata = {
        "package_path": relative_package_path,
        "manifest_path": relative_manifest_path,
        "manifest_sha256": package.manifest_sha256,
        "package_format": package.runtime_kind,
        "input_schema": package.manifest.model_signature,
        "preprocessing": package.manifest.preprocessing,
        "producer_torch_version": package.manifest.producer_torch_version,
        "max_batch_size": package.manifest.max_batch_size,
        "package_metadata": package.metadata,
    }
    # Real packages carry an explicit compatibility declaration.  The fallback
    # from legacy preprocessing lets older packages be registered without
    # weakening run-time checks.  Test doubles/old hand-created rows without a
    # preprocessing object simply omit these optional fields.
    if compatibility.declared:
        package_metadata.update(compatibility_fields(compatibility))

    release = ModelRelease(
        name=package.name,
        version=package.version,
        runtime_kind=package.runtime_kind,
        model_path=str(package.manifest_path),
        checksum_sha256=package.package_checksum_sha256,
        source_domain=package.source_domain,
        is_demo=False,
        is_available=True,
        metadata_json=package_metadata,
    )

    # Construction loads the package on CPU and executes the runtime's tensor contract check.
    get_runtime(release)
    db.add(release)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            f"Model release '{package.name}' version '{package.version}' is already registered"
        ) from exc
    db.refresh(release)
    return _public_summary(release)
