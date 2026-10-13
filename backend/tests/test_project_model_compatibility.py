from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.compatibility import (
    MODEL_SCOPE_ISSUE_KEY_PREFIX,
    MODEL_SCOPE_PROJECT,
    PROJECT_KEY_STRATEGY_FIXED,
    PROJECT_KEY_STRATEGY_PREFIX,
    CompatibilityMetadataError,
    compatibility_from_metadata,
    compatibility_from_model,
    compatibility_from_package,
)
from app.errors import ValidationError
from app.models import ModelRelease, Project, Run, RunStatus
from app.services.runs import process_run

from .conftest import create_project, import_sample, queue_demo_run


def _add_model(app, *, name: str, metadata: dict, source_domain: str = "Apache") -> str:
    with app.state.session_factory() as db:
        model = ModelRelease(
            name=name,
            version="1.0",
            runtime_kind="accs_torchscript_v1",
            model_path=None,
            checksum_sha256=None,
            source_domain=source_domain,
            is_demo=False,
            is_available=True,
            metadata_json=metadata,
        )
        db.add(model)
        db.commit()
        db.refresh(model)
        return model.id


def _fixed_metadata(*keys: str) -> dict:
    return {
        "model_scope": MODEL_SCOPE_PROJECT,
        "project_key_strategy": PROJECT_KEY_STRATEGY_FIXED,
        "supported_project_keys": list(keys),
    }


def _prefix_metadata() -> dict:
    return {
        "model_scope": MODEL_SCOPE_ISSUE_KEY_PREFIX,
        "project_key_strategy": PROJECT_KEY_STRATEGY_PREFIX,
        "supported_project_keys": [],
    }


def test_project_key_is_optional_for_legacy_rows_and_normalized_for_new_projects(
    client: TestClient,
) -> None:
    legacy = create_project(client, "Legacy Project")
    assert legacy["project_key"] is None

    created = client.post(
        "/api/projects",
        json={"name": "Tika Project", "description": "demo", "project_key": "  tika  "},
    )
    assert created.status_code == 201, created.text
    assert created.json()["project_key"] == "TIKA"
    detail = client.get(f"/api/projects/{created.json()['id']}")
    assert detail.status_code == 200
    assert detail.json()["project_key"] == "TIKA"

    invalid = client.post(
        "/api/projects",
        json={"name": "Invalid Key", "project_key": "TIKA-101"},
    )
    assert invalid.status_code == 422


def test_project_key_can_be_persisted_until_first_run_then_is_immutable(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    project = create_project(client, "Patchable Project")
    patched = client.patch(
        f"/api/projects/{project['id']}",
        json={"project_key": " tika ", "description": "Updated"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["project_key"] == "TIKA"
    assert patched.json()["description"] == "Updated"
    preflight = client.options(
        f"/api/projects/{project['id']}",
        headers={
            "Origin": "http://testserver",
            "Access-Control-Request-Method": "PATCH",
        },
    )
    assert preflight.status_code == 200
    assert "PATCH" in preflight.headers["access-control-allow-methods"]

    empty = client.patch(f"/api/projects/{project['id']}", json={})
    assert empty.status_code == 422

    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    run = queue_demo_run(client, project["id"], revision["id"])
    assert run["status"] == "queued"

    changed = client.patch(
        f"/api/projects/{project['id']}",
        json={"project_key": "HADOOP"},
    )
    assert changed.status_code == 409
    assert "cannot be changed" in changed.json()["error"]["message"]
    manifest = client.get(f"/api/runs/{run['id']}/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["project"]["project_key"] == "TIKA"
    assert manifest.json()["config"]["project_key"] == "TIKA"

    same = client.patch(
        f"/api/projects/{project['id']}",
        json={"project_key": "tika"},
    )
    assert same.status_code == 200
    assert same.json()["project_key"] == "TIKA"


def test_import_infers_project_key_and_rejects_explicit_prefix_mismatch(
    client: TestClient,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    inferred_project = create_project(client, "Inferred Project")
    import_sample(client, inferred_project["id"], sample_issues, sample_commits)
    detail = client.get(f"/api/projects/{inferred_project['id']}")
    assert detail.status_code == 200
    assert detail.json()["project_key"] == "TIKA"

    mismatched_project = create_project(
        client, "Explicit Mismatch", project_key="HADOOP"
    )
    rejected = client.post(
        f"/api/projects/{mismatched_project['id']}/imports",
        files={
            "issues_file": ("issues.csv", sample_issues, "text/csv"),
            "commits_file": ("commits.csv", sample_commits, "text/csv"),
        },
    )
    assert rejected.status_code == 422
    assert "Issue_KEY prefixes do not match project_key" in rejected.json()["error"]["message"]
    assert client.get(f"/api/projects/{mismatched_project['id']}").json()[
        "revision_count"
    ] == 0


def test_inference_combines_old_revisions_and_run_rechecks_selected_revision(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    project = create_project(client, "Combined Legacy Revisions")
    hadoop_revision = import_sample(
        client,
        project["id"],
        sample_issues.replace(b"TIKA-", b"HADOOP-"),
        sample_commits,
    )
    with app.state.session_factory() as db:
        stored = db.get(Project, project["id"])
        assert stored is not None
        # Simulate the nullable project row produced by migrating an old DB.
        stored.project_key = None
        db.commit()

    import_sample(client, project["id"], sample_issues, sample_commits)
    combined = client.get(f"/api/projects/{project['id']}").json()
    assert combined["project_key"] == "HADOOP/TIKA"

    model_id = _add_model(
        app,
        name="Tika Revision Guard",
        metadata=_fixed_metadata("TIKA"),
    )
    # Defense in depth: even a directly corrupted/migrated Project row cannot
    # make a TIKA project/model pair consume the older HADOOP revision.
    with app.state.session_factory() as db:
        stored = db.get(Project, project["id"])
        assert stored is not None
        stored.project_key = "TIKA"
        db.commit()
    response = client.post(
        "/api/runs",
        json={
            "project_id": project["id"],
            "dataset_revision_id": hadoop_revision["id"],
            "model_release_id": model_id,
        },
    )
    assert response.status_code == 422
    assert "selected dataset revision is incompatible" in response.json()["error"][
        "message"
    ]


def test_import_does_not_mutate_key_after_run_and_manifest_uses_snapshot(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    project = create_project(client, "Immutable Import Project", project_key="TIKA")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    run = queue_demo_run(client, project["id"], revision["id"])
    with app.state.session_factory() as db:
        stored = db.get(Project, project["id"])
        assert stored is not None
        # Direct mutation represents a pre-migration/administrative edge.  The
        # import service must not infer a new key once a run exists.
        stored.project_key = None
        db.commit()

    import_sample(client, project["id"], sample_issues, sample_commits)
    assert client.get(f"/api/projects/{project['id']}").json()["project_key"] is None
    manifest = client.get(f"/api/runs/{run['id']}/manifest").json()
    assert manifest["project"]["project_key"] == "TIKA"
    assert manifest["config"]["project_key"] == "TIKA"


def test_fixed_model_rejects_missing_and_mismatched_project_key(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    model_id = _add_model(
        app,
        name="Fixed Tika Model",
        metadata=_fixed_metadata("TIKA"),
    )

    missing_project = create_project(client, "Missing Project Key")
    missing_revision = import_sample(
        client, missing_project["id"], sample_issues, sample_commits
    )
    # Simulate a project row imported before project-key inference existed.
    with app.state.session_factory() as db:
        from app.models import Project

        legacy_project = db.get(Project, missing_project["id"])
        assert legacy_project is not None
        legacy_project.project_key = None
        db.commit()
    missing = client.post(
        "/api/runs",
        json={
            "project_id": missing_project["id"],
            "dataset_revision_id": missing_revision["id"],
            "model_release_id": model_id,
        },
    )
    assert missing.status_code == 422
    assert "project_key is required" in missing.json()["error"]["message"]

    mismatched_project = create_project(
        client, "Hadoop Project", project_key="HADOOP"
    )
    mismatched_revision = import_sample(
        client,
        mismatched_project["id"],
        sample_issues.replace(b"TIKA-", b"HADOOP-"),
        sample_commits,
    )
    mismatched = client.post(
        "/api/runs",
        json={
            "project_id": mismatched_project["id"],
            "dataset_revision_id": mismatched_revision["id"],
            "model_release_id": model_id,
        },
    )
    assert mismatched.status_code == 422
    message = mismatched.json()["error"]["message"]
    assert "does not support project key 'HADOOP'" in message
    assert client.get("/api/runs").json() == []


def test_matching_fixed_and_dynamic_prefix_models_are_accepted_and_recorded(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    fixed_model_id = _add_model(
        app,
        name="Fixed Tika Accepted",
        metadata=_fixed_metadata("TIKA"),
    )
    project = create_project(client, "Tika Compatible", project_key="tika")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    queued = client.post(
        "/api/runs",
        json={
            "project_id": project["id"],
            "dataset_revision_id": revision["id"],
            "model_release_id": fixed_model_id,
        },
    )
    assert queued.status_code == 201, queued.text
    assert queued.json()["config_json"]["project_key"] == "TIKA"
    assert queued.json()["config_json"]["model_scope"] == MODEL_SCOPE_PROJECT
    assert queued.json()["config_json"]["supported_project_keys"] == ["TIKA"]
    with app.state.session_factory() as db:
        stored_model = db.get(ModelRelease, fixed_model_id)
        assert stored_model is not None
        stored_model.metadata_json = _fixed_metadata("HADOOP")
        db.commit()
    fixed_manifest = client.get(f"/api/runs/{queued.json()['id']}/manifest").json()
    assert fixed_manifest["model"]["model_scope"] == MODEL_SCOPE_PROJECT
    assert fixed_manifest["model"]["supported_project_keys"] == ["TIKA"]

    prefix_model_id = _add_model(
        app,
        name="Dynamic Prefix Model",
        metadata=_prefix_metadata(),
    )
    other_project = create_project(client, "Other Compatible", project_key="OTHER")
    other_revision = import_sample(
        client,
        other_project["id"],
        sample_issues.replace(b"TIKA-", b"OTHER-"),
        sample_commits,
    )
    dynamic = client.post(
        "/api/runs",
        json={
            "project_id": other_project["id"],
            "dataset_revision_id": other_revision["id"],
            "model_release_id": prefix_model_id,
        },
    )
    assert dynamic.status_code == 201, dynamic.text
    assert dynamic.json()["config_json"]["model_scope"] == MODEL_SCOPE_ISSUE_KEY_PREFIX
    assert dynamic.json()["config_json"]["supported_project_keys"] == []


def test_undeclared_real_model_is_rejected_without_guessing_source_domain(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    model_id = _add_model(
        app,
        name="Undeclared Model",
        metadata={"architecture": "legacy", "source": "TIKA"},
        source_domain="TIKA",
    )
    project = create_project(client, "Undeclared Project", project_key="TIKA")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    response = client.post(
        "/api/runs",
        json={
            "project_id": project["id"],
            "dataset_revision_id": revision["id"],
            "model_release_id": model_id,
        },
    )
    assert response.status_code == 422
    assert "does not declare supported project keys" in response.json()["error"]["message"]


def test_worker_and_retry_revalidate_legacy_real_runs(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    project = create_project(client, "Legacy Queued Real Run")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    with app.state.session_factory() as db:
        stored_project = db.get(Project, project["id"])
        assert stored_project is not None
        stored_project.project_key = None
        model = ModelRelease(
            name="Legacy Fixed Model",
            version="1.0",
            runtime_kind="accs_torchscript_v1",
            source_domain="TIKA",
            is_demo=False,
            is_available=True,
            metadata_json=_fixed_metadata("TIKA"),
        )
        db.add(model)
        db.flush()
        queued = Run(
            project_id=project["id"],
            dataset_revision_id=revision["id"],
            model_release_id=model.id,
            status=RunStatus.QUEUED.value,
            config_json={},
        )
        interrupted = Run(
            project_id=project["id"],
            dataset_revision_id=revision["id"],
            model_release_id=model.id,
            status=RunStatus.INTERRUPTED.value,
            error_code="worker_interrupted",
            config_json={},
        )
        db.add_all([queued, interrupted])
        db.commit()
        queued_id = queued.id
        interrupted_id = interrupted.id

    with pytest.raises(ValidationError, match="project_key is required"):
        process_run(app.state.session_factory, queued_id)
    failed = client.get(f"/api/runs/{queued_id}").json()
    assert failed["status"] == RunStatus.FAILED.value
    assert failed["error_code"] == "validation_error"

    retry = client.post(f"/api/runs/{interrupted_id}/retry")
    assert retry.status_code == 422
    assert "project_key is required" in retry.json()["error"]["message"]
    unchanged = client.get(f"/api/runs/{interrupted_id}").json()
    assert unchanged["status"] == RunStatus.INTERRUPTED.value
    assert unchanged["error_code"] == "worker_interrupted"


def test_demo_wildcard_remains_compatible_with_legacy_project(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    project = create_project(client, "Legacy Demo Project")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    with app.state.session_factory() as db:
        from app.models import Project

        legacy_project = db.get(Project, project["id"])
        assert legacy_project is not None
        legacy_project.project_key = None
        db.commit()
    demo = client.get("/api/models").json()[0]
    assert demo["supported_project_keys"] == ["*"]
    queued = client.post(
        "/api/runs",
        json={
            "project_id": project["id"],
            "dataset_revision_id": revision["id"],
            "model_release_id": demo["id"],
        },
    )
    assert queued.status_code == 201, queued.text


def test_compatibility_parser_requires_explicit_scope_and_valid_keys() -> None:
    with pytest.raises(CompatibilityMetadataError, match="requires model_scope"):
        compatibility_from_metadata({"supported_project_keys": ["TIKA"]})
    with pytest.raises(CompatibilityMetadataError, match="at least one"):
        compatibility_from_metadata(
            {
                "model_scope": "project",
                "project_key_strategy": "fixed",
                "supported_project_keys": [],
            }
        )
    package = SimpleNamespace(
        metadata={
            "model_scope": "project",
            "project_key_strategy": "fixed",
            "supported_project_keys": ["*"],
        },
        preprocessing=None,
    )
    with pytest.raises(CompatibilityMetadataError, match="cannot use.*wildcard"):
        compatibility_from_package(package)
    with pytest.raises(CompatibilityMetadataError, match="must contain strings"):
        compatibility_from_metadata(
            {
                "model_scope": "project",
                "project_key_strategy": "fixed",
                "supported_project_keys": ["TIKA", 1],
            }
        )
    mismatched_package = SimpleNamespace(
        metadata=_fixed_metadata("TIKA"),
        preprocessing=SimpleNamespace(
            project_key_strategy="fixed",
            fixed_project_key="HADOOP",
        ),
    )
    with pytest.raises(CompatibilityMetadataError, match="does not match preprocessing"):
        compatibility_from_package(mismatched_package)


def test_registration_rejects_real_package_wildcard(
    client: TestClient,
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import models as models_api

    package_root = settings.models_dir / "wildcard_release"
    package_root.mkdir()
    manifest_path = package_root / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    package = SimpleNamespace(
        metadata={
            "model_scope": "project",
            "project_key_strategy": "fixed",
            "supported_project_keys": ["*"],
        }
    )
    monkeypatch.setattr(models_api, "validate_model_manifest", lambda _path: package)
    response = client.post(
        "/api/models/register",
        json={"package_name": "wildcard_release"},
    )
    assert response.status_code == 422
    assert "cannot use the supported_project_keys wildcard" in response.json()["error"][
        "message"
    ]

    undeclared_root = settings.models_dir / "undeclared_release"
    undeclared_root.mkdir()
    (undeclared_root / "manifest.json").write_text("{}", encoding="utf-8")
    undeclared = SimpleNamespace(metadata={}, preprocessing=None)
    monkeypatch.setattr(models_api, "validate_model_manifest", lambda _path: undeclared)
    response = client.post(
        "/api/models/register",
        json={"package_name": "undeclared_release"},
    )
    assert response.status_code == 422
    assert "must declare supported project keys" in response.json()["error"]["message"]


def test_old_manifest_preprocessing_can_supply_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.compatibility as compatibility_module

    package = SimpleNamespace(
        metadata={"legacy": True},
        preprocessing=SimpleNamespace(
            project_key_strategy="fixed",
            fixed_project_key="TIKA",
        ),
    )
    monkeypatch.setattr(
        compatibility_module,
        "validate_model_manifest",
        lambda _path: package,
    )
    model = ModelRelease(
        name="Old Manifest Model",
        version="1.0",
        runtime_kind="accs_torchscript_v1",
        model_path="C:/safe/manifest.json",
        source_domain="free-form source domain",
        is_demo=False,
        is_available=True,
        metadata_json={},
    )
    resolved = compatibility_from_model(model)
    assert resolved.model_scope == MODEL_SCOPE_PROJECT
    assert resolved.supported_project_keys == ("TIKA",)
