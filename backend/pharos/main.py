"""Pharos FastAPI application.

Wires the library, storage, engine, and job manager together and exposes the
REST + SSE API. Run with::

    uvicorn pharos.main:app --reload --app-dir backend
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pharos.api import (
    annotate,
    auth,
    daily,
    directions,
    jobs,
    organise,
    papers,
    search,
    zotero,
)
from pharos.config import get_settings
from pharos.daily.scheduler import DailyScheduler
from pharos.daily.service import DailySweeper
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
        # JobManager and DailySweeper hold asyncio state, so create them inside
        # the loop rather than at import time.
        app.state.settings = settings
        app.state.blobs = blobs
        app.state.engine = engine
        app.state.library = library
        app.state.job_manager = JobManager(engine, blobs, settings.max_concurrent_jobs)

        # The daily digest keeps itself current: the scheduler checks hourly
        # whether today has been swept and sweeps it if not, so the user never
        # has to run anything by hand. Disable with PHAROS_DAILY_ENABLED=0.
        sweeper = DailySweeper()
        scheduler = DailyScheduler(sweeper)
        app.state.daily_sweeper = sweeper
        app.state.daily_scheduler = scheduler
        scheduler.start()
        try:
            yield
        finally:
            # Stop the timer before the sweep, so it cannot start a new one
            # while we are cancelling the current one.
            await scheduler.aclose()
            await sweeper.aclose()
            # The Zotero syncer owns background sync tasks of its own; without
            # this the process cannot exit cleanly while a sync is in flight.
            await zotero.syncer.aclose()

    app = FastAPI(title="Pharos API", version="0.0.1", lifespan=lifespan)

    # CORS. The default stays "*" so a fresh clone works against the Vite dev
    # server with no configuration, but it is now configurable via
    # PHAROS_CORS_ORIGINS (comma-separated) and a real deployment must set it.
    #
    # Note what "*" does and does not mean here. Pharos authenticates with a
    # Bearer token in a header, not with a cookie, so a wildcard origin does not
    # hand an attacker's page the user's credentials the way it would in a
    # cookie-session app: the browser will not attach the token by itself, and
    # script on evil.example cannot read it out of another origin's storage.
    # That is why "*" is survivable as a dev default rather than an outright
    # bug. It is still wrong to ship: it makes every browser on the internet a
    # willing proxy for probing this API, and the moment anything here starts
    # relying on a cookie, `allow_credentials=True` alongside "*" is rejected by
    # the CORS spec outright — Starlette would silently stop sending the header
    # and the frontend would break in a way that looks like anything but this.
    # Listing the real origins now avoids discovering that later.
    origins = settings.cors_origin_list or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth first: it is the only router whose endpoints are reachable without a
    # token, and the one every other router now depends on.
    app.include_router(auth.router)
    app.include_router(papers.router)
    app.include_router(jobs.router)
    app.include_router(search.router)
    app.include_router(organise.router)
    app.include_router(annotate.router)
    # BEFORE daily.router, and the order is load-bearing rather than tidy. Both
    # share the /api/daily prefix, and daily.router serves GET /api/daily/{date}
    # whose path segment matches anything — the YYYY-MM-DD pattern on it is a
    # *validation* rule, applied only after the route has already won. Registered
    # the other way round, GET /api/daily/directions would resolve to the day
    # view and answer 422 "not a date" instead of listing directions, which is a
    # confusing failure to debug from the frontend.
    app.include_router(directions.router)
    app.include_router(daily.router)
    app.include_router(zotero.router)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {
            "status": "ok",
            "engine": engine.name,
            "translator": settings.translator_config().type,
        }

    return app


app = create_app()
