"""Pharos FastAPI application.

Wires the library, storage, engine, and job manager together and exposes the
REST + SSE API. Run with::

    uvicorn pharos.main:app --reload --app-dir backend
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pharos.api import jobs, papers
from pharos.config import get_settings
from pharos.db.session import init_engine
from pharos.engines.babeldoc_engine import BabelDocEngine
from pharos.services.library import LibraryService
from pharos.services.translation import JobManager
from pharos.storage.blobs import BlobStore


def create_app() -> FastAPI:
    settings = get_settings()
    init_engine(settings.db_path)

    blobs = BlobStore(settings.files_dir)
    engine = BabelDocEngine(
        engine_python=settings.engine_python,
        translator=settings.translator_config(),
        qps=settings.qps,
    )
    library = LibraryService(blobs)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # JobManager holds asyncio primitives, so create it inside the loop.
        app.state.settings = settings
        app.state.blobs = blobs
        app.state.engine = engine
        app.state.library = library
        app.state.job_manager = JobManager(engine, blobs, settings.max_concurrent_jobs)
        yield

    app = FastAPI(title="Pharos API", version="0.0.1", lifespan=lifespan)

    # Dev: allow the Vite dev server (and any localhost origin) to call the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(papers.router)
    app.include_router(jobs.router)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {
            "status": "ok",
            "engine": engine.name,
            "translator": settings.translator_config().type,
        }

    return app


app = create_app()
