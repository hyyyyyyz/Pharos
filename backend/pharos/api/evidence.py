"""Evidence Ledger API — statements bound to the page they came from.

Every endpoint here requires ``current_user`` and every service call is scoped by
that user's id. None of these routes is public and none may become public: an
evidence row carries a researcher's own reading of a paper, and the ledger's
whole value is that it is *theirs*. ``tests/test_app_routes.py`` asserts this
against the assembled application, and adding one of these paths to its ``PUBLIC``
allow-list would be a line a reviewer has to see.

The one route worth reading twice is ``POST /api/evidence/resolve``. It answers
"where would this quote land?" without writing anything, so the reader can tell a
user their quotation is not in the paper *before* they save it — and because it
is a preflight rather than a fallback, it cannot be used to obtain a page number
the write path would have refused.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

# ``RectIn``/``RectOut`` are imported rather than redeclared. ``Evidence.rects``
# and ``Highlight.rects`` are documented as the same convention (PDF points at
# scale 1, origin bottom-left), and two declarations of one convention is how a
# reader ends up drawing evidence in one coordinate space and highlights in
# another — a discrepancy that only shows up on a zoomed page.
from pharos.api.annotate import RectIn, RectOut
from pharos.api.deps import current_user, get_session
from pharos.api.schemas import as_utc
from pharos.db.models import Evidence, User
from pharos.services import annotate
from pharos.services import evidence as evidence_service
from pharos.services.evidence import EvidenceError


class _EvidenceRoute(APIRoute):
    """Map every :class:`EvidenceError` to its status, in one place.

    A route class rather than a ``try`` per handler, as in ``api/projects.py``
    and ``api/annotate.py``. Forgetting the mapping on a new endpoint would not
    be cosmetic: an uncaught ``NotFound`` is a 500 with a traceback where the
    contract promised a 404, which both breaks the client and confirms the id
    was real.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except EvidenceError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return handler


router = APIRouter(prefix="/api/evidence", tags=["evidence"], route_class=_EvidenceRoute)
_SESSION_DEP = Depends(get_session)
_USER_DEP = Depends(current_user)

EvidenceKind = Literal["quote", "note", "rule_summary", "model_inference"]
EvidenceLocator = Literal["page", "abstract_only", "unlocated"]

#: The fourth resolver answer, which has no locator because there is no honest
#: one. Published in the schema so a client can branch on it explicitly rather
#: than inferring it from a missing page.
PlacementOutcome = Literal["page", "abstract_only", "unlocated", "not_in_paper"]


# ------------------------------------------------------------------- schemas


class EvidenceOut(BaseModel):
    """One ledger row.

    ``kind`` and ``locator`` are both on the wire and both required reading for
    any client that renders this: ``kind`` says who wrote the text, ``locator``
    says how precisely it is placed. A UI that shows the text without showing
    ``kind`` has recreated the failure the ledger exists to prevent — a model's
    paraphrase and the paper's own words rendered identically.
    """

    id: str
    paper_id: str
    project_id: str | None = None
    #: The chunk the text was matched against, when it was matched against one.
    #: Null after a re-extraction replaced the chunks; ``page_no`` survives that,
    #: which is why evidence keeps its own copy of both.
    chunk_id: str | None = None
    kind: EvidenceKind
    locator: EvidenceLocator
    #: Non-null if and only if ``locator`` is ``page``. Enforced in the service
    #: and again by a CHECK constraint in the schema.
    page_no: int | None = None
    rects: list[RectOut]
    text: str
    statement: str | None = None
    provider: str | None = None
    model: str | None = None
    workflow_version: str | None = None
    input_sha256: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class PlacementOut(BaseModel):
    """The answer to "where would this quote land?", without writing anything."""

    outcome: PlacementOutcome
    page_no: int | None = None
    chunk_id: str | None = None


class EvidenceCreate(BaseModel):
    """A new ledger row.

    ``page_no`` is declared here but the service *refuses* it when ``kind`` is
    ``quote`` — a quote's page is resolved from the paper's extracted text and is
    never taken from the caller. Declaring it and then rejecting it is
    deliberate: a client that sends one gets told why, whereas dropping it from
    the model would have ``extra="forbid"`` answer "unknown field" about a field
    that is perfectly real on the other three kinds, and the client would go
    looking for a typo instead of reading the rule.
    """

    model_config = ConfigDict(extra="forbid")

    paper_id: str
    kind: EvidenceKind
    #: Verbatim when ``kind`` is ``quote``, authored otherwise. Capped at the
    #: edge as well as in the service so an oversized body is refused before it
    #: is normalised and compared against every page of a paper.
    text: Annotated[str, Field(min_length=1, max_length=evidence_service.MAX_TEXT)]
    project_id: str | None = None
    statement: Annotated[str, Field(max_length=evidence_service.MAX_STATEMENT)] | None = None
    page_no: Annotated[int, Field(ge=1, le=evidence_service.MAX_PAGE)] | None = None
    #: An untrusted occurrence hint for a quote, never a page assertion. The
    #: service adopts it only after finding the exact quote in that page's own
    #: extracted chunk. Reader geometry requires this verified association.
    page_hint: Annotated[int, Field(ge=1, le=evidence_service.MAX_PAGE)] | None = None
    rects: Annotated[list[RectIn], Field(min_length=1, max_length=annotate.MAX_RECTS)] | None = None
    provider: Annotated[str, Field(max_length=32)] | None = None
    model: Annotated[str, Field(max_length=64)] | None = None
    workflow_version: Annotated[str, Field(max_length=16)] | None = None
    input_sha256: Annotated[str, Field(max_length=64)] | None = None


class EvidencePatch(BaseModel):
    """Omitted keys are left alone; an explicit null clears the field.

    ``kind`` and the four provenance columns are absent by design — see
    ``evidence.update_evidence``. Editing ``text`` on a quote re-resolves its
    page rather than keeping the old one.
    """

    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(min_length=1, max_length=evidence_service.MAX_TEXT)] | None = None
    statement: Annotated[str, Field(max_length=evidence_service.MAX_STATEMENT)] | None = None
    project_id: str | None = None
    page_no: Annotated[int, Field(ge=1, le=evidence_service.MAX_PAGE)] | None = None
    rects: Annotated[list[RectIn], Field(min_length=1, max_length=annotate.MAX_RECTS)] | None = None


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    quote: Annotated[str, Field(min_length=1, max_length=evidence_service.MAX_TEXT)]
    #: Prefer this occurrence only if the quote is present on the page. It is a
    #: hint rather than ``page_no`` because the client is not the page authority.
    page_hint: Annotated[int, Field(ge=1, le=evidence_service.MAX_PAGE)] | None = None


# ---------------------------------------------------------------- converters


def evidence_out(row: Evidence) -> EvidenceOut:
    """Render a stored row.

    ``load_rects`` yields an empty list for geometry this service did not write
    (a hand-edited database, a future importer), so one unreadable region costs
    that row its rectangles rather than costing the request every row on the
    paper.
    """
    return EvidenceOut(
        id=row.id,
        paper_id=row.paper_id,
        project_id=row.project_id,
        chunk_id=row.chunk_id,
        kind=row.kind,  # type: ignore[arg-type]
        locator=row.locator,  # type: ignore[arg-type]
        page_no=row.page_no,
        rects=[RectOut(x=r.x, y=r.y, w=r.w, h=r.h) for r in annotate.load_rects(row.rects)],
        text=row.text or "",
        statement=row.statement,
        provider=row.provider,
        model=row.model,
        workflow_version=row.workflow_version,
        input_sha256=row.input_sha256,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
    )


def _rects_payload(rects: list[RectIn] | None) -> list[dict[str, float]] | None:
    return None if rects is None else [rect.model_dump() for rect in rects]


# ------------------------------------------------------------------ endpoints


@router.get("", response_model=list[EvidenceOut])
def list_evidence(
    paper_id: Annotated[str | None, Query()] = None,
    project_id: Annotated[str | None, Query()] = None,
    kind: Annotated[EvidenceKind | None, Query()] = None,
    locator: Annotated[EvidenceLocator | None, Query()] = None,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> list[EvidenceOut]:
    rows = evidence_service.list_evidence(
        session,
        user_id=user.id,
        paper_id=paper_id,
        project_id=project_id,
        kind=kind,
        locator=locator,
    )
    return [evidence_out(row) for row in rows]


@router.post("", response_model=EvidenceOut, status_code=201)
def create_evidence(
    body: EvidenceCreate,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> EvidenceOut:
    payload = body.model_dump()
    payload["rects"] = _rects_payload(body.rects)
    return evidence_out(evidence_service.create_evidence(session, user_id=user.id, **payload))


@router.post("/resolve", response_model=PlacementOut)
def resolve_quote(
    body: ResolveIn,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> PlacementOut:
    """Preflight a quotation. Writes nothing, and answers ``not_in_paper`` plainly.

    Registered before ``/{evidence_id}`` so the literal path can never be shadowed
    by an id-shaped one, even if a future edit adds a POST on the id route.
    """
    placement = evidence_service.resolve_quote(
        session,
        user_id=user.id,
        paper_id=body.paper_id,
        quote=body.quote,
        page_hint=body.page_hint,
    )
    return PlacementOut(
        outcome=placement.outcome,  # type: ignore[arg-type]
        page_no=placement.page_no,
        chunk_id=placement.chunk_id,
    )


@router.get("/{evidence_id}", response_model=EvidenceOut)
def get_evidence(
    evidence_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> EvidenceOut:
    return evidence_out(evidence_service.require_evidence(session, evidence_id, user_id=user.id))


@router.patch("/{evidence_id}", response_model=EvidenceOut)
def patch_evidence(
    evidence_id: str,
    body: EvidencePatch,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> EvidenceOut:
    # ``exclude_unset`` only: an explicit null is a real instruction here
    # (detach from a project, drop the page and fall back to an honest locator),
    # and dropping nulls the way ``api/projects.py`` does would make those
    # unreachable over HTTP.
    changes: dict[str, Any] = body.model_dump(exclude_unset=True)
    if "rects" in changes:
        changes["rects"] = _rects_payload(body.rects)
    return evidence_out(
        evidence_service.update_evidence(
            session, user_id=user.id, evidence_id=evidence_id, changes=changes
        )
    )


@router.delete("/{evidence_id}", status_code=204)
def delete_evidence(
    evidence_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> Response:
    evidence_service.delete_evidence(session, user_id=user.id, evidence_id=evidence_id)
    return Response(status_code=204)
