from __future__ import annotations

from fastapi.testclient import TestClient
from pathlib import Path

from app.models import DatasetRevision

from .conftest import create_project


def test_import_rejects_missing_columns(client: TestClient, sample_commits: bytes) -> None:
    project = create_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/imports",
        files={
            "issues_file": ("issues.csv", b"Issue_KEY,creationdate\nT-1,2025-01-01\n", "text/csv"),
            "commits_file": ("commits.csv", sample_commits, "text/csv"),
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "Issue_Text" in body["error"]["message"]


def test_import_rejects_non_utf8(client: TestClient, sample_commits: bytes) -> None:
    project = create_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/imports",
        files={
            "issues_file": ("issues.csv", b"\xff\xfe\x00", "text/csv"),
            "commits_file": ("commits.csv", sample_commits, "text/csv"),
        },
    )
    assert response.status_code == 422
    assert "UTF-8" in response.json()["error"]["message"]


def test_duplicate_project_name_is_case_insensitive(client: TestClient) -> None:
    create_project(client, "Tika")
    response = client.post("/api/projects", json={"name": "tika", "description": "duplicate"})
    assert response.status_code == 409


def test_project_name_cannot_be_only_whitespace(client: TestClient) -> None:
    response = client.post("/api/projects", json={"name": "  ", "description": "invalid"})
    assert response.status_code == 422


def test_upload_filename_cannot_escape_revision_directory(
    client: TestClient,
    app,
    settings,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    project = create_project(client, "Safe Paths")
    response = client.post(
        f"/api/projects/{project['id']}/imports",
        files={
            "issues_file": ("../../issues.csv", sample_issues, "text/csv"),
            "commits_file": ("..\\..\\commits.csv", sample_commits, "text/csv"),
        },
    )
    assert response.status_code == 201
    revision_id = response.json()["revision"]["id"]
    with app.state.session_factory() as db:
        revision = db.get(DatasetRevision, revision_id)
        assert revision is not None
        revision_root = (
            settings.data_dir / "projects" / project["id"] / "revisions" / revision_id
        ).resolve()
        assert Path(revision.issues_path).resolve().parent == revision_root
        assert Path(revision.commits_path).resolve().parent == revision_root
        assert revision.issues_filename == "issues.csv"
        assert revision.commits_filename == "commits.csv"
