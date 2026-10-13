from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy import create_engine

from .config import PROJECT_ROOT, Settings


class Base(DeclarativeBase):
    pass


def build_engine(settings: Settings) -> Engine:
    connect_args = {}
    if settings.resolved_database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(settings.resolved_database_url, connect_args=connect_args)

    if settings.resolved_database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def run_migrations(settings: Settings) -> None:
    """Upgrade the configured database to the repository's current schema."""
    config_path = PROJECT_ROOT / "alembic.ini"
    migrations_path = PROJECT_ROOT / "alembic"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(migrations_path))
    config.set_main_option("sqlalchemy.url", settings.resolved_database_url.replace("%", "%%"))
    command.upgrade(config, "head")
