from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.errors import ValidationError
from app.models import AuditEvent, CandidateLink, Run, RunStatus
from app.services import runs as run_service
from app.services.runs import process_run
from app.worker import claim_next_run, recover_interrupted_runs

from .conftest import create_project, import_sample, queue_demo_run


def test_worker_claim_is_atomic_and_processes_from_claimed_state(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    project = create_project(client, "Worker Claim")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    run = queue_demo_run(client, project["id"], revision["id"])

    assert claim_next_run(app.state.session_factory) == run["id"]
    assert claim_next_run(app.state.session_factory) is None
    claimed = client.get(f"/api/runs/{run['id']}").json()
    assert claimed["status"] == RunStatus.VALIDATING.value
    assert claimed["stage"] == "Claimed by worker"

    process_run(app.state.session_factory, run["id"])
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "completed"
    event_types = [
        item["event_type"] for item in client.get(f"/api/runs/{run['id']}/events").json()
    ]
    assert event_types[:2] == ["run.created", "run.claimed"]
    assert event_types[-1] == "run.completed"


def test_process_run_observes_cross_session_cancellation(
    client: TestClient,
    app,
    monkeypatch: pytest.MonkeyPatch,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    project = create_project(client, "Cancellation Refresh")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    run = queue_demo_run(client, project["id"], revision["id"])

    class CancelAfterBatch:
        def score_batch(self, pairs):
            with app.state.session_factory() as external_db:
                external_db.execute(
                    update(Run)
                    .where(Run.id == run["id"])
                    .values(cancel_requested=True)
                )
                external_db.commit()
            return [0.5] * len(pairs)

    monkeypatch.setattr(run_service, "get_runtime", lambda _release: CancelAfterBatch())
    process_run(app.state.session_factory, run["id"])

    cancelled = client.get(f"/api/runs/{run['id']}").json()
    assert cancelled["status"] == RunStatus.CANCELLED.value
    assert cancelled["cancel_requested"] is True
    assert cancelled["summary_json"] == {}
    with app.state.session_factory() as db:
        assert (
            db.scalar(
                select(func.count(CandidateLink.id)).where(CandidateLink.run_id == run["id"])
            )
            == 0
        )
        assert db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.run_id == run["id"],
                AuditEvent.event_type == "run.cancelled",
            )
        ) == 1


def test_worker_restart_marks_interrupted_and_applies_pending_cancel(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    project = create_project(client, "Worker Restart")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    interrupted = queue_demo_run(client, project["id"], revision["id"])
    assert claim_next_run(app.state.session_factory) == interrupted["id"]

    recover_interrupted_runs(app.state.session_factory)
    recovered = client.get(f"/api/runs/{interrupted['id']}").json()
    assert recovered["status"] == RunStatus.INTERRUPTED.value
    assert recovered["error_code"] == "worker_interrupted"
    assert recovered["completed_at"] is not None

    retried = client.post(f"/api/runs/{interrupted['id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == RunStatus.QUEUED.value
    assert retried.json()["error_code"] is None

    pending_cancel = queue_demo_run(client, project["id"], revision["id"])
    assert claim_next_run(app.state.session_factory) == interrupted["id"]
    process_run(app.state.session_factory, interrupted["id"])
    assert claim_next_run(app.state.session_factory) == pending_cancel["id"]
    with app.state.session_factory() as db:
        db.execute(
            update(Run)
            .where(Run.id == pending_cancel["id"])
            .values(cancel_requested=True)
        )
        db.commit()
    recover_interrupted_runs(app.state.session_factory)
    assert client.get(f"/api/runs/{pending_cancel['id']}").json()["status"] == "cancelled"


def test_no_candidates_fails_cleanly_and_can_be_retried(client: TestClient, app) -> None:
    issues = (
        b"Issue_KEY,Issue_Text,creationdate,resolutiondate\n"
        b"OUT-1,No commit in window,2025-01-01T00:00:00Z,2025-01-02T00:00:00Z\n"
    )
    commits = (
        b"Commit_SHA,Commit_Text,Commit_Code,commitdate\n"
        b"far-away,Unrelated future commit,pass,2030-01-01T00:00:00Z\n"
    )
    project = create_project(client, "No Candidates")
    revision = import_sample(client, project["id"], issues, commits)
    run = queue_demo_run(client, project["id"], revision["id"])

    with pytest.raises(ValidationError):
        process_run(app.state.session_factory, run["id"])
    failed = client.get(f"/api/runs/{run['id']}").json()
    assert failed["status"] == RunStatus.FAILED.value
    assert failed["error_code"] == "validation_error"
    assert "No candidate links" in failed["error_message"]

    retry = client.post(f"/api/runs/{run['id']}/retry")
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"
    with pytest.raises(ValidationError, match="cannot be processed"):
        with app.state.session_factory() as db:
            db.execute(update(Run).where(Run.id == run["id"]).values(status="failed"))
            db.commit()
        process_run(app.state.session_factory, run["id"])


@pytest.mark.parametrize("mode", ["adapt", "evaluate"])
def test_unimplemented_modes_are_rejected(
    client: TestClient,
    sample_issues: bytes,
    sample_commits: bytes,
    mode: str,
) -> None:
    project = create_project(client, f"Unsupported {mode}")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    model = client.get("/api/models").json()[0]
    response = client.post(
        "/api/runs",
        json={
            "project_id": project["id"],
            "dataset_revision_id": revision["id"],
            "model_release_id": model["id"],
            "mode": mode,
        },
    )
    assert response.status_code == 422
    assert "not implemented" in response.json()["error"]["message"]
