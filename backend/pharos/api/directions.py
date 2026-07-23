"""每日论文 settings API — the caller's own research directions and sweep config.

Every endpoint here requires ``current_user`` and is scoped to that user by the
service layer, which takes the owner id as a required keyword. Nothing in this
module compares ids by hand: a row the caller does not own is not fetched and
then rejected, it is never fetched, and the resulting 404 is indistinguishable
from one for an id that was never issued.

**Mounting order matters.** ``pharos.api.daily`` declares ``GET /api/daily/{date}``.
Its ``pattern=`` is Pydantic *validation*, applied after routing has already
chosen a handler — it does not narrow Starlette's path regex. So this router
must be included **before** ``daily.router`` in ``main.create_app``, or
``/api/daily/directions`` is routed to the date handler and answered with a 422.
``tests/test_user_directions.py`` pins that ordering against the assembled app.

Stored shape is not wire shape, as elsewhere in this codebase: ``keywords`` and
``categories`` live in SQLite as joined strings because that is convenient for
the sweep, and go out as arrays because that is what a client wants. Requests
accept *either* — a textarea posts one string, a chip editor posts a list, and
refusing one of them would be a puzzle rather than a validation.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session
from pharos.api.schemas import as_utc
from pharos.daily import user_directions
from pharos.daily.user_directions import DirectionError
from pharos.db.models import User, UserDailyConfig, UserDirection

#: Outer bounds on request *bodies*, so a hostile payload is rejected by
#: Pydantic before it reaches the service. The service enforces the real limits
#: (and reports them precisely); these exist so a 10 MB keyword blob never gets
#: as far as being split, lower-cased and de-duplicated first.
_MAX_TEXT = 8_000
_MAX_LIST = 500
_MAX_IDS = 500


class _DirectionsRoute(APIRoute):
    """Map every :class:`DirectionError` to its HTTP status, in one place.

    A route class rather than a ``try`` in each handler: the mapping then cannot
    be forgotten when an endpoint is added, and forgetting it would not be a
    cosmetic slip — an uncaught ``NotFound`` is a 500 with a traceback where the
    contract promised a 404.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except DirectionError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return handler


router = APIRouter(prefix="/api/daily", tags=["daily"], route_class=_DirectionsRoute)


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #

#: Free text or a list of terms. Both are normalised identically by the service.
KeywordsIn = (
    Annotated[str, Field(max_length=_MAX_TEXT)]
    | Annotated[list[Annotated[str, Field(max_length=_MAX_TEXT)]], Field(max_length=_MAX_LIST)]
)


class DirectionOut(BaseModel):
    """One direction, as the settings page renders it."""

    id: str
    name: str
    #: Newline-separated in the column, an array here — never a joined string.
    keywords: list[str]
    enabled: bool
    #: Display order *and* the tie-break when a paper matches several directions.
    position: int
    created_at: datetime


class DirectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=_MAX_TEXT)]
    keywords: KeywordsIn
    enabled: bool = True


class DirectionPatch(BaseModel):
    """Omitted keys are left alone; there is no nullable field to clear."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=_MAX_TEXT)] | None = None
    keywords: KeywordsIn | None = None
    enabled: bool | None = None
    position: int | None = None


class ReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: A partial list is fine: unmentioned directions keep their relative order
    #: after the listed ones.
    direction_ids: Annotated[list[str], Field(max_length=_MAX_IDS)]


class DailyConfigOut(BaseModel):
    """The caller's sweep settings.

    ``categories`` widens the *shared* net — the sweep fetches the union across
    all users — rather than starting a private crawl. ``seeded`` is exposed so
    the UI can tell "these are the defaults we gave you" from "these are yours",
    and so that an empty direction list reads as a deliberate choice.
    """

    categories: list[str]
    max_per_day: int
    enabled: bool
    seeded: bool
    updated_at: datetime | None = None


class DailyConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: (
        Annotated[str, Field(max_length=_MAX_TEXT)]
        | Annotated[list[Annotated[str, Field(max_length=_MAX_TEXT)]], Field(max_length=_MAX_LIST)]
        | None
    ) = None
    max_per_day: int | None = None
    enabled: bool | None = None


# --------------------------------------------------------------------------- #
# converters
# --------------------------------------------------------------------------- #


def _changes(patch: BaseModel) -> dict[str, Any]:
    """The fields a PATCH body actually set, with explicit nulls dropped.

    ``exclude_unset`` is what separates "leave this alone" from "change it".
    Nothing in this module is nullable — a direction always has a name, a config
    always has categories — so an explicit ``null`` is a client bug, and letting
    it through would store the *string* ``"None"`` as a name. Dropped rather
    than 422'd because the handler already answers "no fields provided" for a
    body that changes nothing, which is exactly what this is.
    """
    return {k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None}


def _direction_out(row: UserDirection) -> DirectionOut:
    return DirectionOut(
        id=row.id,
        name=row.name,
        keywords=[k for k in (row.keywords or "").splitlines() if k.strip()],
        enabled=bool(row.enabled),
        position=int(row.position or 0),
        created_at=as_utc(row.created_at),
    )


def _config_out(config: UserDailyConfig) -> DailyConfigOut:
    return DailyConfigOut(
        categories=user_directions.config_categories(config),
        max_per_day=int(config.max_per_day),
        enabled=bool(config.enabled),
        seeded=bool(config.seeded),
        updated_at=as_utc(config.updated_at),
    )


# --------------------------------------------------------------------------- #
# directions
# --------------------------------------------------------------------------- #


@router.get("/directions", response_model=list[DirectionOut])
def list_directions(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[DirectionOut]:
    """The caller's directions in match order, disabled ones included.

    First call for an account seeds the seven defaults, so the settings page is
    never an empty form the user has to guess how to fill in. Deleting them all
    afterwards sticks — see ``user_directions.ensure_seeded``.
    """
    rows = user_directions.list_directions(session, user_id=user.id)
    return [_direction_out(row) for row in rows]


@router.post("/directions", response_model=DirectionOut, status_code=201)
def create_direction(
    body: DirectionCreate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> DirectionOut:
    row = user_directions.create_direction(
        session,
        user_id=user.id,
        name=body.name,
        keywords=body.keywords,
        enabled=body.enabled,
    )
    return _direction_out(row)


@router.patch("/directions/{direction_id}", response_model=DirectionOut)
def patch_direction(
    direction_id: str,
    patch: DirectionPatch,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> DirectionOut:
    """Edit one direction. Takes effect on the caller's next feed request — the
    matching is derived at query time, so nothing is re-fetched or re-read."""
    changes = _changes(patch)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields provided")
    row = user_directions.update_direction(
        session, user_id=user.id, direction_id=direction_id, changes=changes
    )
    return _direction_out(row)


@router.delete("/directions/{direction_id}", status_code=204)
def delete_direction(
    direction_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    """Delete one direction. No paper row is touched; the feed simply stops
    matching it. Deleting the last one leaves an empty feed on purpose."""
    user_directions.delete_direction(session, user_id=user.id, direction_id=direction_id)
    return Response(status_code=204)


@router.post("/directions/reorder", response_model=list[DirectionOut])
def reorder_directions(
    body: ReorderRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[DirectionOut]:
    """Rewrite positions from an explicit id order, and return the new order.

    Not cosmetic: position is the tie-break when a paper matches several
    directions, so this changes which badge papers wear.
    """
    rows = user_directions.reorder_directions(
        session, user_id=user.id, direction_ids=body.direction_ids
    )
    return [_direction_out(row) for row in rows]


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #


@router.get("/config", response_model=DailyConfigOut)
def get_config(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> DailyConfigOut:
    return _config_out(user_directions.get_config(session, user_id=user.id))


@router.patch("/config", response_model=DailyConfigOut)
def patch_config(
    patch: DailyConfigPatch,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> DailyConfigOut:
    changes = _changes(patch)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields provided")
    config = user_directions.update_config(session, user_id=user.id, changes=changes)
    return _config_out(config)
