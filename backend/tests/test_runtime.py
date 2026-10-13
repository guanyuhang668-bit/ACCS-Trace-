from __future__ import annotations

import pytest

from app.models import CommitArtifact, IssueArtifact
from app.runtime import (
    DemoHeuristicRuntime,
    apply_temperature,
)
from app.services.imports import parse_datetime


def test_temperature_identity_and_softening() -> None:
    assert apply_temperature(0.8, 1.0) == pytest.approx(0.8)
    assert apply_temperature(0.8, 2.0) < 0.8
    assert apply_temperature(0.8, 0.5) > 0.8
    with pytest.raises(ValueError):
        apply_temperature(0.5, 0)


def test_demo_runtime_is_deterministic_and_rewards_issue_key() -> None:
    issue = IssueArtifact(
        id="issue",
        dataset_revision_id="revision",
        issue_key="TIKA-101",
        issue_text="Encrypted PDF password parsing fails",
        creation_date=parse_datetime("2025-01-01T00:00:00Z", "date", 1),
    )
    matching = CommitArtifact(
        id="matching",
        dataset_revision_id="revision",
        commit_sha="abc",
        commit_text="TIKA-101 fix encrypted PDF password parsing",
        commit_code="parser.setPassword(password)",
        commit_date=parse_datetime("2025-01-02T00:00:00Z", "date", 1),
    )
    unrelated = CommitArtifact(
        id="unrelated",
        dataset_revision_id="revision",
        commit_sha="xyz",
        commit_text="Refactor build plugin",
        commit_code="plugins.configure()",
        commit_date=parse_datetime("2025-01-02T00:00:00Z", "date", 1),
    )
    runtime = DemoHeuristicRuntime()
    first = runtime.score(issue, matching)
    assert first == runtime.score(issue, matching)
    assert first > runtime.score(issue, unrelated)
    assert 0 < first < 1
    assert runtime.score_batch([(issue, matching), (issue, unrelated)]) == [
        runtime.score(issue, matching),
        runtime.score(issue, unrelated),
    ]
