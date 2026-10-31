import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.api import router
from app.config import Settings
from app.database import check_database, make_engine
from app.provider import DecisionProvider, DemoProvider, OpenAIProvider
from app.schemas import HealthStatus
from app.telemetry import CURRENT, TraceBuffer, persist_trace

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    provider: DecisionProvider | None = None,
) -> FastAPI:
    configuration = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = engine if engine is not None else make_engine(configuration)
        app.state.database = database
        app.state.settings = configuration
        app.state.provider = provider or (
            DemoProvider()
            if configuration.agent_provider == "demo"
            else OpenAIProvider(configuration)
        )
        try:
            yield
        finally:
            if engine is None:
                database.dispose()

    app = FastAPI(title="Support Pilot", version="0.6.0", lifespan=lifespan)
    app.include_router(router)

    @app.middleware("http")
    async def response_headers(request, call_next):
        trace = TraceBuffer()
        token = CURRENT.set(trace)
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            if request.url.path.startswith("/api/"):
                await run_in_threadpool(
                    persist_trace, app.state.database, trace, request.method, route, status_code
                )
            CURRENT.reset(token)
        response.headers["X-Request-ID"] = trace.id
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path == "/":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return response

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(psycopg.Error)
    async def database_error(request, exc):
        logger.warning("database_request_failed")
        return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"})

    @app.get("/health/live", response_model=HealthStatus)
    def live() -> HealthStatus:
        return HealthStatus(status="ok")

    @app.get("/health/ready", response_model=HealthStatus)
    def ready() -> HealthStatus:
        try:
            check_database(app.state.database)
        except SQLAlchemyError:
            # Do not log exceptions containing connection strings or credentials.
            logger.warning("database_readiness_failed")
            raise HTTPException(status_code=503, detail="Database unavailable") from None
        return HealthStatus(status="ready")

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.is_dir():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(frontend / "index.html")

    return app
