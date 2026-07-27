"""Authenticated import/export endpoints for the portable Daily Vault."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session
from pharos.daily.vault import (
    DailyVaultArchive,
    DailyVaultImportResult,
    build_archive,
    import_archive,
)
from pharos.db.models import User

router = APIRouter(prefix="/api/daily/vault", tags=["daily"])
SessionDep = Annotated[Session, Depends(get_session)]
UserDep = Annotated[User, Depends(current_user)]


class DailyVaultImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archive: DailyVaultArchive
    restore_profile: bool = True


@router.get("/export", response_model=DailyVaultArchive)
def export_daily_vault(
    session: SessionDep,
    user: UserDep,
) -> DailyVaultArchive:
    """Return the caller's complete portable Daily snapshot."""
    return build_archive(session, user_id=user.id)


@router.post("/import", response_model=DailyVaultImportResult)
def restore_daily_vault(
    body: DailyVaultImportRequest,
    session: SessionDep,
    user: UserDep,
) -> DailyVaultImportResult:
    """Merge a validated snapshot into the caller's online working copy."""
    return import_archive(
        session,
        user_id=user.id,
        archive=body.archive,
        restore_profile=body.restore_profile,
    )
