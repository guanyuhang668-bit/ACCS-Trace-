from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

from app.models import CalibrationProfile
from app.services.runs import process_run

from .conftest import create_project, import_sample, queue_demo_run


def test_complete_recovery_review_and_export_workflow(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["database"] == "ok"

    project = create_project(client)
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    run = queue_demo_run(client, project["id"], revision["id"])

    process_run(app.state.session_factory, run["id"])
    completed = client.get(f"/api/runs/{run['id']}")
    assert completed.status_code == 200
    completed_body = completed.json()
    assert completed_body["status"] == "completed"
    assert completed_body["progress"] == 100
    assert completed_body["summary_json"]["candidate_count"] > 0
    assert completed_body["summary_json"]["recommended"] >= 1
    assert completed_body["summary_json"]["rejected"] >= 1
    assert completed_body["summary_json"]["calibration_validated"] is False
    assert completed_body["created_at"].endswith("Z")
    assert completed_body["started_at"].endswith("Z")
    assert completed_body["completed_at"].endswith("Z")

    manifest = client.get(f"/api/runs/{run['id']}/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["created_at"].endswith("Z")
    assert manifest.json()["completed_at"].endswith("Z")

    page = client.get(f"/api/runs/{run['id']}/candidates", params={"limit": 200})
    assert page.status_code == 200
    candidates = page.json()["items"]
    assert len(candidates) == completed_body["summary_json"]["candidate_count"]
    levels = {item["reliability_level"] for item in candidates}
    assert "recommended" in levels
    assert "rejected" in levels

    malicious = next(item for item in candidates if item["issue_key"] == "TIKA-404")
    detail = client.get(f"/api/candidates/{malicious['id']}")
    assert detail.status_code == 200
    assert "<script>alert('review')</script>" in detail.json()["issue_text"]
    assert detail.json()["calibration_validated"] is False
    assert detail.json()["calibrated_probability"] is None
    assert detail.json()["issue_created_at"].endswith("Z")
    assert detail.json()["commit_date"].endswith("Z")

    review = client.post(
        f"/api/candidates/{malicious['id']}/reviews",
        json={
            "decision": "accepted",
            "note": '=HYPERLINK("https://invalid.example", "unsafe")',
            "reviewer": "test-reviewer",
            "expected_version": 0,
        },
    )
    assert review.status_code == 201
    assert review.json()["review_status"] == "accepted"
    assert review.json()["review_version"] == 1
    assert len(review.json()["decisions"]) == 1
    assert review.json()["decisions"][0]["created_at"].endswith("Z")

    stale = client.post(
        f"/api/candidates/{malicious['id']}/reviews",
        json={
            "decision": "rejected",
            "note": "Stale update",
            "reviewer": "other-reviewer",
            "expected_version": 0,
        },
    )
    assert stale.status_code == 409

    invalid_decision = client.post(
        f"/api/candidates/{malicious['id']}/reviews",
        json={
            "decision": "unreviewed",
            "note": "Not a real decision",
            "reviewer": "test-reviewer",
            "expected_version": 1,
        },
    )
    assert invalid_decision.status_code == 422

    export = client.post(
        f"/api/runs/{run['id']}/exports",
        json={"format": "csv", "include_rejected": True},
    )
    assert export.status_code == 201
    assert export.json()["row_count"] == len(candidates)
    download = client.get(f"/api/exports/{export.json()['id']}/download")
    assert download.status_code == 200
    rows = list(csv.DictReader(io.StringIO(download.content.decode("utf-8-sig"))))
    assert len(rows) == len(candidates)
    exported = next(row for row in rows if row["Issue_KEY"] == "TIKA-404")
    assert exported["reviewer_decision"] == "accepted"
    assert exported["reviewer_note"].startswith("'=HYPERLINK")
    assert exported["model_version"] == "1.0.0-demo"

    json_export = client.post(
        f"/api/runs/{run['id']}/exports",
        json={"format": "json", "include_rejected": True},
    )
    assert json_export.status_code == 201
    json_download = client.get(f"/api/exports/{json_export.json()['id']}/download")
    assert json_download.status_code == 200
    json_rows = json_download.json()
    json_row = next(row for row in json_rows if row["Issue_KEY"] == "TIKA-404")
    assert json_row["reviewer_note"].startswith("=HYPERLINK")


def test_filters_and_analytics(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    project = create_project(client, "Analytics Demo")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    run = queue_demo_run(client, project["id"], revision["id"])
    process_run(app.state.session_factory, run["id"])

    recommended = client.get(
        f"/api/runs/{run['id']}/candidates",
        params={"reliability_level": "recommended"},
    )
    assert recommended.status_code == 200
    assert recommended.json()["total"] >= 1
    assert all(item["reliability_level"] == "recommended" for item in recommended.json()["items"])

    search = client.get(
        f"/api/runs/{run['id']}/candidates",
        params={"search": "TIKA-202"},
    )
    assert search.status_code == 200
    assert search.json()["total"] >= 1
    assert all(item["issue_key"] == "TIKA-202" for item in search.json()["items"])

    analytics = client.get(f"/api/runs/{run['id']}/analytics")
    assert analytics.status_code == 200
    assert len(analytics.json()["score_bins"]) == 10
    assert sum(item["count"] for item in analytics.json()["score_bins"]) == run_candidate_count(
        client, run["id"]
    )


def test_analytics_bins_use_deployment_calibrated_scores(
    client: TestClient,
    app,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    project = create_project(client, "Calibrated Analytics Demo")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    run = queue_demo_run(client, project["id"], revision["id"])

    with app.state.session_factory() as db:
        profile = db.get(CalibrationProfile, run["calibration_profile_id"])
        assert profile is not None
        profile.is_deployment_validated = True
        profile.temperature = 2.0
        db.commit()

    process_run(app.state.session_factory, run["id"])
    candidates = client.get(
        f"/api/runs/{run['id']}/candidates",
        params={"limit": 200},
    ).json()["items"]
    calibrated_scores = [item["calibrated_probability"] for item in candidates]
    raw_scores = [item["raw_score"] for item in candidates]
    assert all(score is not None for score in calibrated_scores)
    assert calibrated_scores != raw_scores

    analytics = client.get(f"/api/runs/{run['id']}/analytics")
    assert analytics.status_code == 200
    body = analytics.json()
    assert body["calibration_validated"] is True
    expected_counts = []
    for index in range(10):
        lower = index / 10
        upper = (index + 1) / 10
        expected_counts.append(
            sum(
                1
                for score in calibrated_scores
                if lower <= score < upper or (index == 9 and score == 1)
            )
        )
    assert [item["count"] for item in body["score_bins"]] == expected_counts


def run_candidate_count(client: TestClient, run_id: str) -> int:
    return client.get(f"/api/runs/{run_id}/candidates", params={"limit": 1}).json()["total"]
