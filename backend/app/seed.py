from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CalibrationProfile, ModelRelease, RuntimeKind


def seed_builtin_models(db: Session) -> None:
    existing = db.scalar(
        select(ModelRelease).where(
            ModelRelease.name == "Deterministic Demo Runtime",
            ModelRelease.version == "1.0.0-demo",
        )
    )
    if existing:
        return
    model = ModelRelease(
        name="Deterministic Demo Runtime",
        version="1.0.0-demo",
        runtime_kind=RuntimeKind.DEMO.value,
        source_domain="Synthetic ACCS-Trace demonstration data",
        is_demo=True,
        is_available=True,
        metadata_json={
            "purpose": "Development and end-to-end testing only",
            "warning": "This is not an ACCS model and must not be used for research claims.",
            "input_schema": "issue-commit-v1",
            "offline": True,
            # Explicit wildcard keeps legacy projects (which have no
            # project_key) compatible with the demo while making its scope
            # visible to the API and run manifest.
            "model_scope": "project",
            "project_key_strategy": "fixed",
            "supported_project_keys": ["*"],
        },
    )
    db.add(model)
    db.flush()
    db.add(
        CalibrationProfile(
            model_release_id=model.id,
            name="Demo decision profile",
            temperature=1.0,
            threshold_low=0.30,
            threshold_high=0.75,
            source_domain=model.source_domain,
            is_deployment_validated=False,
            metrics_json={"status": "not_validated", "note": "Raw demo scores are used."},
        )
    )
    db.commit()
