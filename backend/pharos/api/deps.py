"""FastAPI dependencies: DB session, app-state services, and the current user."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from pharos.auth.tokens import TokenError, decode_access_token
from pharos.config import Settings
from pharos.db.models import User
from pharos.db.session import session_scope
from pharos.services.library import LibraryService
from pharos.services.translation import JobManager
from pharos.storage.blobs import BlobStore

#: ``auto_error=False`` so that a missing or non-Bearer Authorization header
#: arrives here as ``None`` instead of FastAPI raising a 403 of its own. That
#: matters twice: the optional dependency needs to see "no credentials" as a
#: normal state, and the required one needs to answer 401 (with a challenge
#: header) rather than 403, which is what a client's "log in again" logic keys on.
_bearer_scheme = HTTPBearer(auto_error=False, description="Pharos access token")

#: Sent with every 401 so the response is a well-formed authentication
#: challenge rather than a bare error.
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


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


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    """Build the single 401 shape every authentication failure uses.

    The detail is intentionally coarse ("invalid or expired"): distinguishing a
    forged signature from a stale expiry, or a deleted account from a bumped
    epoch, tells an attacker which half of their guess was right.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers=_CHALLENGE
    )


def _authenticate(credentials: HTTPAuthorizationCredentials, session: Session) -> User:
    """Turn a Bearer credential into the live ``User`` row, or raise 401.

    The user is re-read from the database on every request rather than trusted
    from the token's own claims. That is the whole reason deactivation and
    ``token_epoch`` bumps take effect immediately instead of whenever the last
    outstanding token happens to expire — with a 14-day TTL, "eventually" is not
    a security control.
    """
    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise _unauthorized("Invalid or expired token") from exc

    user = session.scalar(select(User).where(User.id == claims.user_id))
    if user is None:
        # Account deleted since the token was issued.
        raise _unauthorized("Invalid or expired token")
    if not user.is_active:
        raise _unauthorized("Invalid or expired token")
    if int(user.token_epoch or 0) != claims.epoch:
        # Password changed, or the user logged out everywhere.
        raise _unauthorized("Invalid or expired token")
    return user


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    """The authenticated user, or 401.

    Returns a live ORM object attached to the request's session — the same
    session the endpoint receives from :func:`get_session`, because FastAPI
    caches a dependency per request. Mutating it inside an endpoint is therefore
    persisted by ``session_scope``'s commit, exactly like the paper endpoints do.
    """
    if credentials is None:
        raise _unauthorized()
    return _authenticate(credentials, session)


def current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> User | None:
    """The authenticated user if the request carried credentials, else ``None``.

    A *present but unusable* token is still a 401. Silently treating an expired
    or revoked token as "anonymous" would hand back the public view of an
    endpoint that should have refused, so a client with a stale token would keep
    working while quietly seeing someone else's idea of the data — a bug that
    hides itself. Only the complete absence of an Authorization header is
    anonymous.
    """
    if credentials is None:
        return None
    return _authenticate(credentials, session)
