from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.models import ExportArtifact
from app.services.runs import process_run

from .conftest import create_project, import_sample, queue_demo_run


def _completed_run(
    client: TestClient,
    app,
    issues: bytes,
    commits: bytes,
    name: str,
) -> tuple[dict, dict]:
    project = create_project(client, name)
    revision = import_sample(client, project["id"], issues, commits)
    run = queue_demo_run(client, project["id"], revision["id"])
    process_run(app.state.session_factory, run["id"])
    return revision, client.get(f"/api/runs/{run['id']}").json()


def test_manifest_contains_reproducibility_metadata_without_model_path(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    revision, run = _completed_run(
        client, app, sample_issues, sample_commits, "Manifest Demo"
    )
    response = client.get(f"/api/runs/{run['id']}/manifest")
    assert response.status_code == 200
    manifest = response.json()
    assert manifest["manifest_schema_version"] == "1.0"
    assert manifest["dataset"]["revision_id"] == revision["id"]
    assert manifest["dataset"]["issues_sha256"] == hashlib.sha256(sample_issues).hexdigest()
    assert manifest["dataset"]["commits_sha256"] == hashlib.sha256(sample_commits).hexdigest()
    assert manifest["model"]["runtime_kind"] == "demo"
    assert manifest["model"]["is_demo"] is True
    assert "not an ACCS model" in manifest["model"]["metadata"]["warning"]
    assert "model_path" not in manifest["model"]
    assert manifest["deployment_calibration"]["deployment_validated"] is False
    assert manifest["summary"]["candidate_count"] > 0
    assert manifest["config"]["mode"] == "recover"


def test_json_export_uses_latest_versioned_review_and_is_audited(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    _revision, run = _completed_run(
        client, app, sample_issues, sample_commits, "JSON Export"
    )
    candidate = client.get(
        f"/api/runs/{run['id']}/candidates", params={"limit": 1}
    ).json()["items"][0]
    first = client.post(
        f"/api/candidates/{candidate['id']}/reviews",
        json={
            "decision": "accepted",
            "note": "First decision",
            "reviewer": "reviewer-a",
            "expected_version": 0,
        },
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/candidates/{candidate['id']}/reviews",
        json={
            "decision": "rejected",
            "note": "Superseding decision",
            "reviewer": "reviewer-b",
            "expected_version": 1,
        },
    )
    assert second.status_code == 201
    assert [item["previous_version"] for item in second.json()["decisions"]] == [0, 1]

    artifact = client.post(
        f"/api/runs/{run['id']}/exports",
        json={"format": "json", "include_rejected": True},
    )
    assert artifact.status_code == 201
    download = client.get(f"/api/exports/{artifact.json()['id']}/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/json")
    assert hashlib.sha256(download.content).hexdigest() == artifact.json()["sha256"]
    rows = json.loads(download.content)
    reviewed = next(row for row in rows if row["Issue_KEY"] == candidate["issue_key"] and row["Commit_SHA"] == candidate["commit_sha"])
    assert reviewed["reviewer_decision"] == "rejected"
    assert reviewed["reviewer_note"] == "Superseding decision"

    events = client.get(f"/api/runs/{run['id']}/events").json()
    assert sum(item["event_type"] == "candidate.reviewed" for item in events) == 2
    assert events[-1]["event_type"] == "run.exported"
    assert events[-1]["payload_json"]["sha256"] == artifact.json()["sha256"]


def test_download_rejects_export_path_outside_data_root(
    client: TestClient,
    app,
    settings,
    tmp_path: Path,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    _revision, run = _completed_run(
        client, app, sample_issues, sample_commits, "Export Boundary"
    )
    outside = tmp_path / "outside.json"
    outside.write_text("[]", encoding="utf-8")
    with app.state.session_factory() as db:
        artifact = ExportArtifact(
            run_id=run["id"],
            format="json",
            file_path=str(outside.resolve()),
            sha256=hashlib.sha256(outside.read_bytes()).hexdigest(),
            row_count=0,
        )
        db.add(artifact)
        db.commit()
        db.refresh(artifact)
        artifact_id = artifact.id

    response = client.get(f"/api/exports/{artifact_id}/download")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
