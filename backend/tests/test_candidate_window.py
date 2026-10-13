from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import CandidateLink, CommitArtifact
from app.services.runs import process_run

from .conftest import create_project, import_sample


def _queue_with_limit(
    client: TestClient,
    project_id: str,
    revision_id: str,
    max_candidates: int,
) -> dict:
    model = client.get("/api/models").json()[0]
    response = client.post(
        "/api/runs",
        json={
            "project_id": project_id,
            "dataset_revision_id": revision_id,
            "model_release_id": model["id"],
            "calibration_profile_id": model["calibration_profiles"][0]["id"],
            "mode": "recover",
            "days_before": 0,
            "days_after": 2,
            "unresolved_window_days": 30,
            "max_candidates_per_issue": max_candidates,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_time_window_is_inclusive_and_limit_keeps_nearest_commits(
    client: TestClient,
    app,
) -> None:
    issues = (
        b"Issue_KEY,Issue_Text,creationdate,resolutiondate\n"
        b"WIN-1,Window boundary test,2025-01-01T00:00:00Z,2025-01-10T00:00:00Z\n"
    )
    commits = (
        b"Commit_SHA,Commit_Text,Commit_Code,commitdate\n"
        b"c01,At lower boundary,code,2025-01-01T00:00:00Z\n"
        b"c09,One day before resolution,code,2025-01-09T00:00:00Z\n"
        b"c10,At resolution,code,2025-01-10T00:00:00Z\n"
        b"c11,One day after resolution,code,2025-01-11T00:00:00Z\n"
        b"c12,At upper boundary,code,2025-01-12T00:00:00Z\n"
        b"c13,Outside upper boundary,code,2025-01-13T00:00:00Z\n"
    )
    project = create_project(client, "Candidate Window")
    revision = import_sample(client, project["id"], issues, commits)

    limited = _queue_with_limit(client, project["id"], revision["id"], 2)
    process_run(app.state.session_factory, limited["id"])
    with app.state.session_factory() as db:
        limited_shas = set(
            db.scalars(
                select(CommitArtifact.commit_sha)
                .join(CandidateLink, CandidateLink.commit_id == CommitArtifact.id)
                .where(CandidateLink.run_id == limited["id"])
            )
        )
    assert limited_shas == {"c09", "c10"}

    complete_window = _queue_with_limit(client, project["id"], revision["id"], 20)
    process_run(app.state.session_factory, complete_window["id"])
    with app.state.session_factory() as db:
        all_shas = set(
            db.scalars(
                select(CommitArtifact.commit_sha)
                .join(CandidateLink, CandidateLink.commit_id == CommitArtifact.id)
                .where(CandidateLink.run_id == complete_window["id"])
            )
        )
    assert all_shas == {"c01", "c09", "c10", "c11", "c12"}
