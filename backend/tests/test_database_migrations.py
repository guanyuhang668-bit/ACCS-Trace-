from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import PROJECT_ROOT, Settings
from app.database import build_engine, run_migrations


def test_clean_database_migrates_idempotently_with_sqlite_safety(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh" / "accstrace.db"
    database_path.parent.mkdir()
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{database_path.as_posix()}",
    )

    run_migrations(settings)
    run_migrations(settings)
    engine = build_engine(settings)
    expected_tables = {
        "alembic_version",
        "projects",
        "dataset_revisions",
        "issues",
        "commits",
        "model_releases",
        "calibration_profiles",
        "runs",
        "candidate_links",
        "review_decisions",
        "export_artifacts",
        "audit_events",
    }
    assert expected_tables <= set(inspect(engine).get_table_names())

    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA busy_timeout")) >= 5000
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
        project_columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("projects")
        }
        assert project_columns["project_key"]["nullable"] is True

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO dataset_revisions (
                        id, project_id, issues_filename, commits_filename,
                        issues_path, commits_path, issues_sha256, commits_sha256,
                        issue_count, commit_count, schema_version, created_at
                    ) VALUES (
                        'revision', 'missing-project', 'issues.csv', 'commits.csv',
                        'issues.csv', 'commits.csv', :digest, :digest,
                        1, 1, '1.0', :created_at
                    )
                    """
                ),
                {"digest": "0" * 64, "created_at": "2026-01-01T00:00:00Z"},
            )

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.resolved_database_url)
    command.check(config)
    engine.dispose()


def test_existing_0001_project_survives_project_key_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "upgrade" / "accstrace.db"
    database_path.parent.mkdir()
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{database_path.as_posix()}",
    )
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.resolved_database_url)
    command.upgrade(config, "0001")

    engine = build_engine(settings)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (id, name, description, created_at)
                VALUES ('legacy-project', 'Legacy Tika', 'Created before project keys',
                        '2026-01-01T00:00:00Z')
                """
            )
        )
    engine.dispose()

    run_migrations(settings)
    upgraded_engine = build_engine(settings)
    with upgraded_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT id, name, description, project_key FROM projects "
                "WHERE id = 'legacy-project'"
            )
        ).mappings().one()
        assert dict(row) == {
            "id": "legacy-project",
            "name": "Legacy Tika",
            "description": "Created before project keys",
            "project_key": None,
        }
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
    upgraded_engine.dispose()
