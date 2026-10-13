from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import select, update

from .config import get_settings
from .database import build_engine, build_session_factory, run_migrations
from .models import AuditEvent, Run, RunStatus, utc_now
from .services.runs import process_run


logger = logging.getLogger(__name__)


def claim_next_run(session_factory) -> str | None:
    with session_factory() as db:
        run_id = db.scalar(
            select(Run.id)
            .where(Run.status == RunStatus.QUEUED.value)
            .order_by(Run.created_at)
            .limit(1)
        )
        if not run_id:
            return None
        result = db.execute(
            update(Run)
            .where(
                Run.id == run_id,
                Run.status == RunStatus.QUEUED.value,
                Run.cancel_requested.is_(False),
            )
            .values(status=RunStatus.VALIDATING.value, stage="Claimed by worker", progress=1)
        )
        if result.rowcount != 1:
            db.rollback()
            return None
        db.add(
            AuditEvent(
                run_id=run_id,
                event_type="run.claimed",
                message="Run claimed by worker",
                payload_json={"progress": 1},
            )
        )
        db.commit()
        return run_id


def recover_interrupted_runs(session_factory) -> None:
    active_statuses = {
        RunStatus.VALIDATING.value,
        RunStatus.GENERATING.value,
        RunStatus.INFERENCING.value,
    }
    with session_factory() as db:
        active_runs = list(db.scalars(select(Run).where(Run.status.in_(active_statuses))))
        for run in active_runs:
            previous_status = run.status
            if run.cancel_requested:
                run.status = RunStatus.CANCELLED.value
                run.stage = "Cancelled after worker restart"
                run.error_code = None
                run.error_message = None
                event_type = "run.cancelled"
                message = "Pending cancellation applied during worker recovery"
            else:
                run.status = RunStatus.INTERRUPTED.value
                run.stage = "Interrupted before worker restart"
                run.error_code = "worker_interrupted"
                run.error_message = (
                    "The worker stopped before this run completed. Requeue the run to retry."
                )
                event_type = "run.interrupted"
                message = "Active run marked interrupted during worker recovery"
            run.completed_at = utc_now()
            db.add(
                AuditEvent(
                    run_id=run.id,
                    event_type=event_type,
                    message=message,
                    payload_json={"previous_status": previous_status},
                )
            )
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="ACCS-Trace local run worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued run")
    args = parser.parse_args()
    settings = get_settings()
    settings.ensure_directories()
    run_migrations(settings)
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    recover_interrupted_runs(session_factory)
    try:
        while True:
            run_id = claim_next_run(session_factory)
            if run_id:
                try:
                    process_run(session_factory, run_id)
                except Exception:
                    logger.exception("Run %s failed", run_id)
                if args.once:
                    return
            elif args.once:
                return
            else:
                time.sleep(settings.worker_poll_seconds)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
