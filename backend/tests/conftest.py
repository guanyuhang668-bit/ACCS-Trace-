from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{(data_dir / 'test.db').as_posix()}",
        frontend_origin="http://testserver",
        max_upload_bytes=1024 * 1024,
        allow_inline_processing=True,
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_issues() -> bytes:
    return (PROJECT_ROOT / "sample_data" / "issues.csv").read_bytes()


@pytest.fixture
def sample_commits() -> bytes:
    return (PROJECT_ROOT / "sample_data" / "commits.csv").read_bytes()


def create_project(
    client: TestClient,
    name: str = "Tika Demo",
    *,
    project_key: str | None = None,
) -> dict:
    payload = {"name": name, "description": "Synthetic demo"}
    if project_key is not None:
        payload["project_key"] = project_key
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def import_sample(client: TestClient, project_id: str, issues: bytes, commits: bytes) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/imports",
        files={
            "issues_file": ("issues.csv", issues, "text/csv"),
            "commits_file": ("commits.csv", commits, "text/csv"),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["revision"]


def queue_demo_run(client: TestClient, project_id: str, revision_id: str) -> dict:
    models = client.get("/api/models").json()
    assert len(models) == 1
    model = models[0]
    response = client.post(
        "/api/runs",
        json={
            "project_id": project_id,
            "dataset_revision_id": revision_id,
            "model_release_id": model["id"],
            "calibration_profile_id": model["calibration_profiles"][0]["id"],
            "mode": "recover",
            "days_before": 2,
            "days_after": 2,
            "unresolved_window_days": 90,
            "max_candidates_per_issue": 20,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
