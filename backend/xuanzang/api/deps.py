"""FastAPI dependencies: DB session + access to app-state services."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from xuanzang.config import Settings
from xuanzang.db.session import session_scope
from xuanzang.services.library import LibraryService
from xuanzang.services.translation import JobManager
from xuanzang.storage.blobs import BlobStore


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_blobs(request: Request) -> BlobStore:
    return request.app.state.blobs


def get_library(request: Request) -> LibraryService:
    return request.app.state.library


def get_job_manager(request: Request) -> JobManager:
    return request.app.state.job_manager
