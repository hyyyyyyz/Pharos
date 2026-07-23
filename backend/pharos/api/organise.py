"""Collections and tags API — the folder tree and the labels beside it.

Every endpoint here requires ``current_user`` and is scoped to that user by the
service layer, which takes the owner id as a required keyword. Nothing in this
module compares ids by hand: a row the caller does not own is not fetched and
then rejected, it is never fetched, and the resulting 404 is indistinguishable
from one for an id that was never issued.
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
from pharos.db.models import Tag, User
from pharos.services import organise
from pharos.services.organise import CollectionNode, OrganiseError

_MAX_NAME = 256
_MAX_TAG_NAME = 128
_MAX_IDS = 500


class _OrganiseRoute(APIRoute):
    """Map every :class:`OrganiseError` to its HTTP status, in one place.

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
            except OrganiseError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return handler


router = APIRouter(prefix="/api", tags=["organise"], route_class=_OrganiseRoute)


# ------------------------------------------------------------------- schemas


class CollectionOut(BaseModel):
    """One folder, without its subtree."""

    id: str
    name: str
    parent_id: str | None = None
    position: int
    #: Papers filed directly in this folder, trashed ones excluded. NOT a
    #: roll-up over descendants — see ``organise.CollectionNode``.
    paper_count: int
    created_at: datetime


class CollectionTreeOut(CollectionOut):
    """A folder with its children nested inside it."""

    children: list[CollectionTreeOut] = []


class CollectionsOut(BaseModel):
    """The whole sidebar: the tree plus the two counts rendered beside it.

    ``uncategorised_count`` is 未分类 — computed as "in no collection", never
    stored as a folder, so it has no id and cannot be filed into.
    """

    collections: list[CollectionTreeOut]
    all_count: int
    uncategorised_count: int


class CollectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=_MAX_NAME)]
    parent_id: str | None = None


class CollectionPatch(BaseModel):
    """Omitted keys are left alone; an explicit ``parent_id: null`` moves the
    folder to the top level. ``name`` may not be null — a folder needs a name,
    and the column is NOT NULL besides."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=_MAX_NAME)] | None = None
    parent_id: str | None = None
    position: int | None = None


class PaperIds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_ids: Annotated[list[str], Field(max_length=_MAX_IDS)]


class TagIds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: An empty list is meaningful: it clears every tag on the paper.
    tag_ids: Annotated[list[str], Field(max_length=_MAX_IDS)]


class TagOut(BaseModel):
    id: str
    name: str
    #: One of ``organise.TAG_COLORS``, or null for the neutral chip. Always a
    #: token name the frontend resolves to a ``--c-*`` variable, never a colour.
    color: str | None = None
    paper_count: int
    created_at: datetime


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=_MAX_TAG_NAME)]
    color: str | None = None


class TagPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=_MAX_TAG_NAME)] | None = None
    color: str | None = None


class CollectionDeleted(BaseModel):
    """``promoted_children`` says how many folders moved up a level.

    Reported rather than left implicit because it is the visible consequence of
    the delete: the subtree survives, one level shallower.
    """

    id: str
    promoted_children: int


class AddResult(BaseModel):
    """``added`` counts only papers that were not already filed here."""

    added: int
    collection: CollectionOut


class PaperIdsOut(BaseModel):
    paper_ids: list[str]


# ``CollectionTreeOut`` names itself in its own annotation, and with
# ``from __future__ import annotations`` that annotation is still a string when
# the class body finishes. Resolving it here means a malformed self-reference is
# an ImportError at boot rather than a 500 the first time the sidebar loads.
CollectionTreeOut.model_rebuild()


# ---------------------------------------------------------------- converters


def _flat(node: CollectionNode) -> CollectionOut:
    return CollectionOut(
        id=node.id,
        name=node.name,
        parent_id=node.parent_id,
        position=node.position,
        paper_count=node.paper_count,
        created_at=as_utc(node.created_at),
    )


def _tree(node: CollectionNode) -> CollectionTreeOut:
    return CollectionTreeOut(
        **_flat(node).model_dump(), children=[_tree(c) for c in node.children]
    )


def _tag_out(tag: Tag, paper_count: int) -> TagOut:
    return TagOut(
        id=tag.id,
        name=tag.name,
        color=tag.color,
        paper_count=paper_count,
        created_at=as_utc(tag.created_at),
    )


# -------------------------------------------------------------- collections


@router.get("/collections", response_model=CollectionsOut)
def list_collections(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> CollectionsOut:
    view = organise.overview(session, user_id=user.id)
    return CollectionsOut(
        collections=[_tree(n) for n in view.collections],
        all_count=view.all_count,
        uncategorised_count=view.uncategorised_count,
    )


@router.post("/collections", response_model=CollectionOut, status_code=201)
def create_collection(
    body: CollectionCreate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> CollectionOut:
    collection = organise.create_collection(
        session, user_id=user.id, name=body.name, parent_id=body.parent_id
    )
    return _flat(organise.collection_node(session, collection.id, user_id=user.id))


@router.patch("/collections/{collection_id}", response_model=CollectionOut)
def patch_collection(
    collection_id: str,
    patch: CollectionPatch,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> CollectionOut:
    # exclude_unset is what separates "leave this alone" from "set this to null".
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields provided")
    organise.update_collection(
        session, user_id=user.id, collection_id=collection_id, changes=changes
    )
    session.flush()
    return _flat(organise.collection_node(session, collection_id, user_id=user.id))


@router.delete("/collections/{collection_id}", response_model=CollectionDeleted)
def delete_collection(
    collection_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> CollectionDeleted:
    """Delete a folder. Papers are never deleted — they fall back to 未分类.

    Child folders are promoted to this folder's parent rather than destroyed;
    see ``organise.delete_collection`` for why.
    """
    promoted = organise.delete_collection(
        session, user_id=user.id, collection_id=collection_id
    )
    return CollectionDeleted(id=collection_id, promoted_children=promoted)


@router.get("/collections/{collection_id}/papers", response_model=PaperIdsOut)
def list_collection_papers(
    collection_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> PaperIdsOut:
    """Ids only, newest first. Deliberately not full papers: the library listing
    shape belongs to ``api/papers.py``, and duplicating it here would give the
    frontend two ``PaperOut``s that could drift apart."""
    return PaperIdsOut(
        paper_ids=organise.list_papers_in_collection(session, collection_id, user_id=user.id)
    )


@router.post("/collections/{collection_id}/papers", response_model=AddResult)
def add_papers_to_collection(
    collection_id: str,
    body: PaperIds,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> AddResult:
    added = organise.add_papers(
        session, user_id=user.id, collection_id=collection_id, paper_ids=body.paper_ids
    )
    node = organise.collection_node(session, collection_id, user_id=user.id)
    return AddResult(added=added, collection=_flat(node))


@router.delete("/collections/{collection_id}/papers/{paper_id}", response_model=CollectionOut)
def remove_paper_from_collection(
    collection_id: str,
    paper_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> CollectionOut:
    organise.remove_paper(
        session, user_id=user.id, collection_id=collection_id, paper_id=paper_id
    )
    return _flat(organise.collection_node(session, collection_id, user_id=user.id))


# --------------------------------------------------------------------- tags


@router.get("/tags", response_model=list[TagOut])
def list_tags(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[TagOut]:
    return [_tag_out(tc.tag, tc.paper_count) for tc in organise.list_tags(session, user_id=user.id)]


@router.post("/tags", response_model=TagOut, status_code=201)
def create_tag(
    body: TagCreate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> TagOut:
    tag = organise.create_tag(session, user_id=user.id, name=body.name, color=body.color)
    return _tag_out(tag, 0)


@router.patch("/tags/{tag_id}", response_model=TagOut)
def patch_tag(
    tag_id: str,
    patch: TagPatch,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> TagOut:
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields provided")
    tag = organise.update_tag(session, user_id=user.id, tag_id=tag_id, changes=changes)
    session.flush()
    counts = {tc.tag.id: tc.paper_count for tc in organise.list_tags(session, user_id=user.id)}
    return _tag_out(tag, counts.get(tag.id, 0))


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(
    tag_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    """Delete a tag. The papers that carried it keep everything else."""
    organise.delete_tag(session, user_id=user.id, tag_id=tag_id)
    return Response(status_code=204)


@router.get("/papers/{paper_id}/tags", response_model=list[TagOut])
def get_paper_tags(
    paper_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[TagOut]:
    counts = {tc.tag.id: tc.paper_count for tc in organise.list_tags(session, user_id=user.id)}
    tags = organise.paper_tags(session, user_id=user.id, paper_id=paper_id)
    return [_tag_out(t, counts.get(t.id, 0)) for t in tags]


@router.put("/papers/{paper_id}/tags", response_model=list[TagOut])
def set_paper_tags(
    paper_id: str,
    body: TagIds,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[TagOut]:
    """Replace the paper's tags with exactly ``tag_ids`` and return the result."""
    tags = organise.set_paper_tags(
        session, user_id=user.id, paper_id=paper_id, tag_ids=body.tag_ids
    )
    counts = {tc.tag.id: tc.paper_count for tc in organise.list_tags(session, user_id=user.id)}
    return [_tag_out(t, counts.get(t.id, 0)) for t in tags]
