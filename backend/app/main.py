from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import __version__
from .api import candidates, exports, models, projects, runs
from .config import Settings, get_settings
from .database import build_engine, build_session_factory, run_migrations
from .errors import AppError
from .schemas import HealthResponse
from .seed import seed_builtin_models


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    active_settings.ensure_directories()
    active_engine = build_engine(active_settings)
    session_factory = build_session_factory(active_engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        run_migrations(active_settings)
        with session_factory() as db:
            seed_builtin_models(db)
        yield
        active_engine.dispose()

    app = FastAPI(
        title="ACCS-Trace API",
        version=__version__,
        description=(
            "Local-first API for Issue-Commit candidate generation, model scoring, "
            "reliability triage, human review, and auditable export."
        ),
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.state.engine = active_engine
    app.state.session_factory = session_factory
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[active_settings.frontend_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.get("/api/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        with session_factory() as db:
            db.execute(text("SELECT 1"))
        return HealthResponse(status="ok", version=__version__, database="ok")

    app.include_router(projects.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(candidates.router, prefix="/api")
    app.include_router(exports.router, prefix="/api")
    return app


app = create_app()
