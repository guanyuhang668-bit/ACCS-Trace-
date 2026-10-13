from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from ..dependencies import get_session
from ..errors import ConflictError, NotFoundError
from ..models import (
    AuditEvent,
    CalibrationProfile,
    CandidateLink,
    CommitArtifact,
    IssueArtifact,
    ModelRelease,
    ReliabilityLevel,
    ReviewDecision,
    ReviewStatus,
    Run,
)
from ..schemas import (
    AnalyticsResponse,
    CandidateDetail,
    CandidatePage,
    CandidateSummary,
    ReviewCreate,
)


router = APIRouter(tags=["candidates"])


def _title(text: str, length: int = 110) -> str:
    normalized = " ".join((text or "").split())
    return normalized if len(normalized) <= length else normalized[: length - 1] + "…"


def _summary(candidate: CandidateLink) -> CandidateSummary:
    return CandidateSummary(
        id=candidate.id,
        issue_key=candidate.issue.issue_key,
        commit_sha=candidate.commit.commit_sha,
        issue_title=_title(candidate.issue.issue_text),
        commit_title=_title(candidate.commit.commit_text),
        raw_score=candidate.raw_score,
        calibrated_probability=candidate.calibrated_probability,
        reliability_level=candidate.reliability_level,
        predicted_link=candidate.predicted_link,
        time_delta_hours=candidate.time_delta_hours,
        review_status=candidate.review_status,
        review_version=candidate.review_version,
    )


@router.get("/runs/{run_id}/candidates", response_model=CandidatePage)
def list_candidates(
    run_id: str,
    reliability_level: ReliabilityLevel | None = None,
    review_status: ReviewStatus | None = None,
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> CandidatePage:
    if not db.get(Run, run_id):
        raise NotFoundError("Run", run_id)
    predicates = [CandidateLink.run_id == run_id]
    if reliability_level:
        predicates.append(CandidateLink.reliability_level == reliability_level.value)
    if review_status:
        predicates.append(CandidateLink.review_status == review_status.value)
    if search:
        pattern = f"%{search.strip()}%"
        predicates.append(
            or_(IssueArtifact.issue_key.ilike(pattern), CommitArtifact.commit_sha.ilike(pattern))
        )

    base = (
        select(CandidateLink)
        .join(CandidateLink.issue)
        .join(CandidateLink.commit)
        .where(*predicates)
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    query = (
        base.options(selectinload(CandidateLink.issue), selectinload(CandidateLink.commit))
        .order_by(CandidateLink.raw_score.desc(), IssueArtifact.issue_key, CommitArtifact.commit_sha)
        .offset(offset)
        .limit(limit)
    )
    items = [_summary(item) for item in db.scalars(query)]
    return CandidatePage(items=items, total=total, limit=limit, offset=offset)


@router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
def get_candidate(candidate_id: str, db: Session = Depends(get_session)) -> CandidateDetail:
    candidate = db.scalar(
        select(CandidateLink)
        .where(CandidateLink.id == candidate_id)
        .options(
            selectinload(CandidateLink.issue),
            selectinload(CandidateLink.commit),
            selectinload(CandidateLink.review_decisions),
            selectinload(CandidateLink.run).selectinload(Run.model_release),
            selectinload(CandidateLink.run).selectinload(Run.calibration_profile),
        )
    )
    if not candidate:
        raise NotFoundError("Candidate", candidate_id)
    run = candidate.run
    calibration = run.calibration_profile
    threshold_low = float(run.config_json.get("threshold_low", 0.4))
    threshold_high = float(run.config_json.get("threshold_high", 0.75))
    return CandidateDetail(
        **_summary(candidate).model_dump(),
        issue_text=candidate.issue.issue_text,
        commit_text=candidate.commit.commit_text,
        commit_code=candidate.commit.commit_code,
        issue_created_at=candidate.issue.creation_date,
        issue_resolved_at=candidate.issue.resolution_date,
        commit_date=candidate.commit.commit_date,
        decisions=candidate.review_decisions,
        model_name=run.model_release.name,
        model_version=run.model_release.version,
        source_domain=(calibration.source_domain if calibration else run.model_release.source_domain),
        calibration_validated=bool(calibration and calibration.is_deployment_validated),
        threshold_low=threshold_low,
        threshold_high=threshold_high,
    )


@router.post(
    "/candidates/{candidate_id}/reviews",
    response_model=CandidateDetail,
    status_code=status.HTTP_201_CREATED,
)
def review_candidate(
    candidate_id: str,
    payload: ReviewCreate,
    db: Session = Depends(get_session),
) -> CandidateDetail:
    candidate = db.get(CandidateLink, candidate_id)
    if not candidate:
        raise NotFoundError("Candidate", candidate_id)
    result = db.execute(
        update(CandidateLink)
        .where(
            CandidateLink.id == candidate_id,
            CandidateLink.review_version == payload.expected_version,
        )
        .values(
            review_status=payload.decision.value,
            review_version=CandidateLink.review_version + 1,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise ConflictError("This candidate was updated in another view. Refresh and try again.")
    db.add(
        ReviewDecision(
            candidate_id=candidate_id,
            decision=payload.decision.value,
            note=payload.note.strip(),
            reviewer=payload.reviewer.strip(),
            previous_version=payload.expected_version,
        )
    )
    db.add(
        AuditEvent(
            run_id=candidate.run_id,
            event_type="candidate.reviewed",
            message="Candidate review decision recorded",
            payload_json={
                "candidate_id": candidate_id,
                "decision": payload.decision.value,
                "reviewer": payload.reviewer.strip(),
                "previous_version": payload.expected_version,
                "new_version": payload.expected_version + 1,
            },
        )
    )
    db.commit()
    return get_candidate(candidate_id, db)


@router.get("/runs/{run_id}/analytics", response_model=AnalyticsResponse)
def run_analytics(run_id: str, db: Session = Depends(get_session)) -> AnalyticsResponse:
    run = db.scalar(
        select(Run)
        .where(Run.id == run_id)
        .options(selectinload(Run.calibration_profile), selectinload(Run.model_release))
    )
    if not run:
        raise NotFoundError("Run", run_id)
    levels = {
        row[0]: row[1]
        for row in db.execute(
            select(CandidateLink.reliability_level, func.count(CandidateLink.id))
            .where(CandidateLink.run_id == run_id)
            .group_by(CandidateLink.reliability_level)
        )
    }
    reviews = {
        row[0]: row[1]
        for row in db.execute(
            select(CandidateLink.review_status, func.count(CandidateLink.id))
            .where(CandidateLink.run_id == run_id)
            .group_by(CandidateLink.review_status)
        )
    }
    calibration = run.calibration_profile
    calibration_validated = bool(calibration and calibration.is_deployment_validated)
    score_column = (
        CandidateLink.calibrated_probability
        if calibration_validated
        else CandidateLink.raw_score
    )
    scores = list(db.scalars(select(score_column).where(CandidateLink.run_id == run_id)))
    if calibration_validated and any(score is None for score in scores):
        raise ConflictError("Validated calibration scores are incomplete for this run")
    bins = []
    for index in range(10):
        lower = index / 10
        upper = (index + 1) / 10
        count = sum(1 for score in scores if lower <= score < upper or (index == 9 and score == 1))
        bins.append({"lower": lower, "upper": upper, "count": count})
    return AnalyticsResponse(
        counts={level.value: levels.get(level.value, 0) for level in ReliabilityLevel},
        review_counts={item.value: reviews.get(item.value, 0) for item in ReviewStatus},
        score_bins=bins,
        calibration_validated=calibration_validated,
        source_domain=calibration.source_domain if calibration else run.model_release.source_domain,
    )
