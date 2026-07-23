"""Search API: full-text search across the caller's own library."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session
from pharos.db.models import User
from pharos.services import search as search_service

router = APIRouter(prefix="/api", tags=["search"])

#: Longest query string accepted. A search box is not an upload endpoint, and an
#: unbounded ``q`` is free work an anonymous-ish caller can ask for — the service
#: caps the number of terms it honours anyway, so nothing useful is lost.
_MAX_QUERY_CHARS = 200


class SearchHitOut(BaseModel):
    """One matching paper."""

    paper_id: str
    title: str
    #: HTML-escaped text with ``<mark>…</mark>`` around the matched terms, and
    #: nothing else live in it. Safe to render as HTML — and *meant* to be, since
    #: escaping it a second time on the client would show the tags as literal
    #: text instead of a highlight.
    snippet: str
    #: ``title`` | ``abstract`` | ``authors`` | ``full_text``.
    field: str
    #: Higher is more relevant. Only comparable within a single response, because
    #: the two engines below score on completely different scales.
    rank: float


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitOut]
    #: Matches across the whole library, not just this page.
    total: int
    limit: int
    offset: int
    #: ``fts5`` or ``like`` — which backend answered. Reported so a client can
    #: tell the user that search is degraded on this deployment rather than
    #: leaving them to wonder why ranking looks odd.
    engine: str


@router.get("/search", response_model=SearchResponse)
def search_library(
    q: str = Query(
        ...,
        max_length=_MAX_QUERY_CHARS,
        description=(
            'Free text. Wrap words in double quotes for an exact phrase ("neural '
            'machine translation"); otherwise all terms must match and the last '
            "one is treated as a prefix."
        ),
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> SearchResponse:
    """Search the caller's library — and only the caller's library.

    ``current_user`` is required rather than optional: there is no public view of
    anybody's papers. The owner id is threaded into the SQL as a required keyword
    the whole way down (see :func:`pharos.services.search.search`), so a result
    set belonging to somebody else is not something a mistake here could produce.

    Any query string is accepted. Nonsense, unbalanced quotes and FTS5 operators
    are all sanitised into a valid query rather than rejected — a search box that
    500s on a stray asterisk is worse than one that finds nothing.
    """
    page = search_service.search(session, user_id=user.id, query=q, limit=limit, offset=offset)
    return SearchResponse(
        query=q,
        hits=[
            SearchHitOut(
                paper_id=hit.paper_id,
                title=hit.title,
                snippet=hit.snippet,
                field=hit.field,
                rank=hit.rank,
            )
            for hit in page.hits
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        engine=page.engine,
    )
