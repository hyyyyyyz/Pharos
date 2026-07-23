"""Organisation service — the folder tree (collections) and tags.

Everything here is owner-scoped, and the owner id is a *required keyword* on
every entry point for the same reason it is in :mod:`pharos.services.library`:
an optional owner is one a caller can forget, and a forgotten filter here does
not surface as a wrong number on screen — it puts one researcher's private
library into another's sidebar. Required means the omission is a type error.

Three product decisions are made here rather than left to the caller, because
each of them has to be the same everywhere or the tree stops making sense:

* **未分类 is computed, never stored.** It means "in no collection", so it is a
  ``NOT EXISTS`` over the membership table. Materialising it as a real folder
  would need every add/remove to also maintain it, and the first missed update
  would leave a paper listed both as filed and as unfiled.
* **Deleting a folder promotes its children**, it does not destroy the subtree
  (see :func:`delete_collection`).
* **Names are unique per sibling group, case-insensitively** — for tags, per
  user. Two folders called "生成模型" side by side, or ``NLP`` next to ``nlp``,
  are indistinguishable to the person reading them (see :func:`_assert_free`).

Counts exclude trashed papers (``Paper.deleted_at IS NOT NULL``) everywhere: a
folder badge that counts papers the user has thrown away is simply wrong, and
the recycle bin is its own view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from pharos.db.models import Collection, Paper, PaperCollection, PaperTag, Tag

#: Column widths from ``models.py``. SQLite does not enforce ``VARCHAR`` length,
#: so without these an over-long name is written happily and only becomes an
#: error if the library is ever moved to a database that does enforce it.
_MAX_NAME = 256
_MAX_TAG_NAME = 128

#: How deep the folder tree may nest. Not a storage limit — a rendering and
#: recursion one: the tree is serialised as nested JSON, and an unbounded chain
#: would be both unusable in a 200px sidebar and a way to make the encoder
#: recurse as deep as a client likes.
_MAX_DEPTH = 16

#: Ceiling on a single bulk membership change, so one request cannot ask for an
#: unbounded ``IN`` clause.
_MAX_BULK = 500

#: Accepted tag accents. Deliberately a closed set of *token names*, never a
#: colour value: the frontend maps each to a ``--c-*`` CSS variable, and letting
#: a hex through the API would put a hard-coded colour in the database that no
#: theme could ever override. ``None`` means the neutral chip.
TAG_COLORS = frozenset({"amber", "blue", "green", "red", "purple", "grey"})


class OrganiseError(Exception):
    """Base for the failures this service reports to its caller.

    Each subclass carries the HTTP status it deserves so the API layer maps them
    in one place and cannot forget a case — see ``pharos.api.organise``.
    """

    status_code = 400


class NotFound(OrganiseError):
    """The row does not exist, or does not belong to the caller.

    One class for both on purpose. The API turns this into a 404, never a 403,
    so a probe cannot tell "no such folder" from "not yours" and therefore
    cannot walk ids to enumerate another user's library.
    """

    status_code = 404


class Conflict(OrganiseError):
    """The request is well-formed but collides with something that exists."""

    status_code = 409


class Invalid(OrganiseError):
    """The request cannot be satisfied as asked (empty name, cycle, too deep)."""

    status_code = 400


# ----------------------------------------------------------------- read models


@dataclass
class CollectionNode:
    """One folder plus its subtree, ready to be serialised.

    ``paper_count`` is *direct* membership only: papers filed in this folder,
    not in its descendants. That is what "this folder contains" means to
    someone looking at the row, and it keeps the number a user can verify by
    clicking. A recursive roll-up would make a parent's badge change when a
    grandchild is edited, which reads as a bug.
    """

    id: str
    name: str
    parent_id: str | None
    position: int
    created_at: datetime
    paper_count: int
    children: list[CollectionNode] = field(default_factory=list)


@dataclass
class TagCount:
    tag: Tag
    paper_count: int


@dataclass
class Overview:
    """The whole sidebar in one round trip.

    ``all_count`` and ``uncategorised_count`` are the live 我的文库 and 未分类
    numbers. They ship together with the tree because the frontend renders them
    as siblings of it, and computing them here means the three numbers are
    always from the same instant instead of from three racing requests.
    """

    collections: list[CollectionNode]
    all_count: int
    uncategorised_count: int


# --------------------------------------------------------------------- helpers


def _require_owner(user_id: str) -> str:
    """Reject a falsy owner id before it can reach a WHERE clause.

    ``Paper.user_id`` is nullable for the pre-accounts migration, so a ``None``
    threaded through here renders ``user_id IS NULL`` and quietly matches the
    legacy rows instead of failing. Mirrors ``library._require_owner``.
    """
    if not user_id:
        raise ValueError("user_id is required: every organise query must be owner-scoped")
    return user_id


def _clean_name(value: object, *, limit: int) -> str:
    """Trim and collapse whitespace; a name that is only spaces is no name.

    Collapsing internal runs matters for the uniqueness check below: ``"deep  RL"``
    and ``"deep RL"`` are the same folder to a reader, and storing both would
    give the sidebar two rows nobody can tell apart.

    ``value`` is typed loosely because it arrives from a patch dict where the
    client may have sent an explicit ``null``. That is rejected here rather than
    stringified: ``str(None)`` is the perfectly valid folder name "None", so the
    guard has to be at the point of conversion or it is not a guard at all.
    """
    if not isinstance(value, str):
        raise Invalid("name must be a non-empty string")
    name = " ".join(value.split())
    if not name:
        raise Invalid("name cannot be empty")
    if len(name) > limit:
        raise Invalid(f"name must be at most {limit} characters")
    return name


def _clean_color(value: str | None) -> str | None:
    if value is None:
        return None
    color = value.strip().lower()
    if not color:
        return None
    if color not in TAG_COLORS:
        raise Invalid(f"color must be one of {sorted(TAG_COLORS)}")
    return color


def _same_name(a: str, b: str) -> bool:
    """Case-insensitive name equality, done in Python rather than in SQL.

    SQLite's ``lower()`` is ASCII-only by default, so ``WHERE lower(name) = ?``
    would compare "NLP"/"nlp" correctly and then quietly fail to fold anything
    accented — a rule that works for some of the user's folders and not others
    is worse than no rule. ``casefold`` handles the full range, and the row
    counts involved (one user's folders, one user's tags) are in the tens.
    """
    return a.casefold() == b.casefold()


def _load_collections(session: Session, *, user_id: str) -> list[Collection]:
    """Every folder of this user's, ordered as the sidebar shows them."""
    _require_owner(user_id)
    return list(
        session.scalars(
            select(Collection)
            .where(Collection.user_id == user_id)
            .order_by(Collection.position, Collection.created_at, Collection.id)
        )
    )


def _require_collection(session: Session, collection_id: str, *, user_id: str) -> Collection:
    """Resolve one of ``user_id``'s folders, or raise the 404 that hides the rest.

    A filtered SELECT rather than ``session.get`` plus a check afterwards: drop a
    line from this and the query fails to compile a scope, instead of quietly
    returning every user's row.
    """
    _require_owner(user_id)
    row = session.scalar(
        select(Collection).where(Collection.id == collection_id, Collection.user_id == user_id)
    )
    if row is None:
        raise NotFound("Collection not found")
    return row


def _require_tag(session: Session, tag_id: str, *, user_id: str) -> Tag:
    _require_owner(user_id)
    row = session.scalar(select(Tag).where(Tag.id == tag_id, Tag.user_id == user_id))
    if row is None:
        raise NotFound("Tag not found")
    return row


def _require_paper(session: Session, paper_id: str, *, user_id: str) -> Paper:
    _require_owner(user_id)
    row = session.scalar(select(Paper).where(Paper.id == paper_id, Paper.user_id == user_id))
    if row is None:
        raise NotFound("Paper not found")
    return row


def _require_papers(session: Session, paper_ids: list[str], *, user_id: str) -> list[str]:
    """Verify every id belongs to the caller, then return them deduped in order.

    The verification is a single scoped SELECT and it happens *before* anything
    is written: filing someone else's paper into your own folder must be a 404,
    not a partial success that files the ids that happened to be yours. Any
    unknown id fails the whole request for the same reason a per-id 404 would be
    an oracle — the caller learns nothing about which id was the bad one.
    """
    _require_owner(user_id)
    if not paper_ids:
        raise Invalid("paper_ids must not be empty")
    if len(paper_ids) > _MAX_BULK:
        raise Invalid(f"at most {_MAX_BULK} papers can be filed in one request")

    seen: list[str] = []
    for pid in paper_ids:
        if pid not in seen:
            seen.append(pid)
    owned = set(
        session.scalars(
            select(Paper.id).where(Paper.id.in_(seen), Paper.user_id == user_id)
        )
    )
    if len(owned) != len(seen):
        raise NotFound("Paper not found")
    return seen


def _assert_free(
    rows: list[Collection] | list[Tag],
    *,
    name: str,
    parent_id: str | None = None,
    exclude_id: str | None = None,
) -> None:
    """Refuse a name that already exists in the same group, ignoring case.

    For collections the group is the sibling set (same parent), so "综述" may
    exist under two different parents — those are two distinguishable places.
    For tags the group is the whole user: a tag list is flat, and ``NLP`` beside
    ``nlp`` would let a paper carry what a human reads as the same label twice,
    with filtering by one silently missing the other.
    """
    for row in rows:
        if row.id == exclude_id:
            continue
        if isinstance(row, Collection) and row.parent_id != parent_id:
            continue
        if _same_name(row.name, name):
            kind = "folder" if isinstance(row, Collection) else "tag"
            raise Conflict(f"A {kind} named {row.name!r} already exists")


def _children_map(rows: list[Collection]) -> dict[str | None, list[Collection]]:
    out: dict[str | None, list[Collection]] = {}
    for row in rows:
        out.setdefault(row.parent_id, []).append(row)
    return out


def _descendants(rows: list[Collection], root_id: str) -> set[str]:
    """Ids of ``root_id``'s subtree, itself included. Iterative, cycle-tolerant.

    The visited set is not defensive noise: this runs *before* a move is applied
    precisely to keep cycles out, and if a cycle ever did reach the table (a
    hand-edited database, say) a naive walk would hang the request rather than
    report the problem.
    """
    kids = _children_map(rows)
    seen = {root_id}
    stack = [root_id]
    while stack:
        for child in kids.get(stack.pop(), ()):
            if child.id not in seen:
                seen.add(child.id)
                stack.append(child.id)
    return seen


def _depth_of(rows: list[Collection], node_id: str | None) -> int:
    """Number of ancestors above ``node_id``; a root is depth 0, ``None`` is -1."""
    by_id = {r.id: r for r in rows}
    depth = -1
    seen: set[str] = set()
    cur = node_id
    while cur is not None and cur not in seen:
        seen.add(cur)
        depth += 1
        node = by_id.get(cur)
        cur = node.parent_id if node is not None else None
    return depth


def _height_of(rows: list[Collection], root_id: str) -> int:
    """Longest path from ``root_id`` down to a leaf, in edges."""
    kids = _children_map(rows)
    height = 0
    level = [root_id]
    seen = set(level)
    while level:
        nxt = [c.id for node in level for c in kids.get(node, ()) if c.id not in seen]
        seen.update(nxt)
        if nxt:
            height += 1
        level = nxt
    return height


# ----------------------------------------------------------------------- reads


def _collection_counts(session: Session, *, user_id: str) -> dict[str, int]:
    """Live paper count per folder, for this user only.

    Both sides of the membership row are re-scoped to the caller even though the
    write paths already guarantee it. That is not redundancy for its own sake:
    it means a count can never be inflated by a row this service did not write —
    a Zotero sync, a future import, a hand-run SQL statement — and the query
    reads as its own proof.
    """
    _require_owner(user_id)
    rows = session.execute(
        select(PaperCollection.collection_id, func.count(PaperCollection.paper_id))
        .join(Collection, Collection.id == PaperCollection.collection_id)
        .join(Paper, Paper.id == PaperCollection.paper_id)
        .where(
            Collection.user_id == user_id,
            Paper.user_id == user_id,
            Paper.deleted_at.is_(None),
        )
        .group_by(PaperCollection.collection_id)
    ).all()
    return {cid: count for cid, count in rows}


def _tag_counts(session: Session, *, user_id: str) -> dict[str, int]:
    _require_owner(user_id)
    rows = session.execute(
        select(PaperTag.tag_id, func.count(PaperTag.paper_id))
        .join(Tag, Tag.id == PaperTag.tag_id)
        .join(Paper, Paper.id == PaperTag.paper_id)
        .where(Tag.user_id == user_id, Paper.user_id == user_id, Paper.deleted_at.is_(None))
        .group_by(PaperTag.tag_id)
    ).all()
    return {tid: count for tid, count in rows}


def count_all(session: Session, *, user_id: str) -> int:
    """Live papers in this user's library — the 我的文库 badge."""
    _require_owner(user_id)
    return int(
        session.scalar(
            select(func.count())
            .select_from(Paper)
            .where(Paper.user_id == user_id, Paper.deleted_at.is_(None))
        )
        or 0
    )


def count_uncategorised(session: Session, *, user_id: str) -> int:
    """Live papers filed in no folder at all — the 未分类 badge.

    ``NOT EXISTS`` over the membership table, not a stored flag. Membership rows
    are cascade-deleted with their collection, so deleting a folder moves its
    papers here automatically with nothing left to maintain.
    """
    _require_owner(user_id)
    filed = (
        select(PaperCollection.paper_id)
        .join(Collection, Collection.id == PaperCollection.collection_id)
        .where(PaperCollection.paper_id == Paper.id, Collection.user_id == user_id)
        .exists()
    )
    return int(
        session.scalar(
            select(func.count())
            .select_from(Paper)
            .where(Paper.user_id == user_id, Paper.deleted_at.is_(None), ~filed)
        )
        or 0
    )


def build_tree(rows: list[Collection], counts: dict[str, int]) -> list[CollectionNode]:
    """Assemble the nested tree from a flat, already-ordered row list.

    Built from the roots down through a children map rather than by recursing on
    ``parent_id``, so a row that is somehow unreachable (a cycle) is left out of
    the response instead of hanging the request that asked for it.
    """
    kids = _children_map(rows)

    def node(row: Collection) -> CollectionNode:
        return CollectionNode(
            id=row.id,
            name=row.name,
            parent_id=row.parent_id,
            position=row.position,
            created_at=row.created_at,
            paper_count=counts.get(row.id, 0),
            children=[node(c) for c in kids.get(row.id, ())],
        )

    return [node(r) for r in kids.get(None, ())]


def overview(session: Session, *, user_id: str) -> Overview:
    """The folder tree plus the two computed counts beside it."""
    rows = _load_collections(session, user_id=user_id)
    return Overview(
        collections=build_tree(rows, _collection_counts(session, user_id=user_id)),
        all_count=count_all(session, user_id=user_id),
        uncategorised_count=count_uncategorised(session, user_id=user_id),
    )


def collection_node(session: Session, collection_id: str, *, user_id: str) -> CollectionNode:
    """One of the caller's folders as a flat node (no children), with its count.

    Re-resolves by id through the owner-scoped helper rather than accepting a
    ``Collection``, so there is no way for the API layer to render a row it did
    not prove belongs to the caller.
    """
    collection = _require_collection(session, collection_id, user_id=user_id)
    counts = _collection_counts(session, user_id=user_id)
    return CollectionNode(
        id=collection.id,
        name=collection.name,
        parent_id=collection.parent_id,
        position=collection.position,
        created_at=collection.created_at,
        paper_count=counts.get(collection.id, 0),
    )


def list_papers_in_collection(session: Session, collection_id: str, *, user_id: str) -> list[str]:
    """Ids of the live papers filed in one of the caller's folders."""
    collection = _require_collection(session, collection_id, user_id=user_id)
    return list(
        session.scalars(
            select(Paper.id)
            .join(PaperCollection, PaperCollection.paper_id == Paper.id)
            .where(
                PaperCollection.collection_id == collection.id,
                Paper.user_id == user_id,
                Paper.deleted_at.is_(None),
            )
            .order_by(Paper.added_at.desc())
        )
    )


# ------------------------------------------------------------------ collections


def create_collection(
    session: Session, *, user_id: str, name: str, parent_id: str | None = None
) -> Collection:
    """Create a folder, optionally inside another of the caller's folders."""
    _require_owner(user_id)
    clean = _clean_name(name, limit=_MAX_NAME)
    rows = _load_collections(session, user_id=user_id)

    if parent_id is not None:
        # Resolving through the owner-scoped helper is what makes "put my folder
        # inside someone else's" a 404 rather than a successful write into
        # another user's tree.
        _require_collection(session, parent_id, user_id=user_id)
        if _depth_of(rows, parent_id) + 1 >= _MAX_DEPTH:
            raise Invalid(f"folders may not nest more than {_MAX_DEPTH} deep")

    _assert_free(rows, name=clean, parent_id=parent_id)

    siblings = [r.position for r in rows if r.parent_id == parent_id]
    collection = Collection(
        user_id=user_id,
        name=clean,
        parent_id=parent_id,
        position=(max(siblings) + 1) if siblings else 0,
    )
    session.add(collection)
    session.flush()  # populate collection.id
    return collection


def update_collection(
    session: Session,
    *,
    user_id: str,
    collection_id: str,
    changes: dict[str, object],
) -> Collection:
    """Rename, re-parent, or re-order a folder.

    ``changes`` carries only the keys the client actually sent, so moving a
    folder to the top level (``parent_id: null``) stays distinguishable from
    leaving it where it is.
    """
    collection = _require_collection(session, collection_id, user_id=user_id)
    rows = _load_collections(session, user_id=user_id)

    name = collection.name
    if "name" in changes:
        name = _clean_name(changes["name"], limit=_MAX_NAME)

    parent_id = collection.parent_id
    if "parent_id" in changes:
        raw = changes["parent_id"]
        parent_id = None if raw is None else str(raw)
        if parent_id is not None:
            _require_collection(session, parent_id, user_id=user_id)
            # A folder inside its own subtree would orphan that whole subtree
            # from the roots: it would still be in the table, still be counted,
            # and never appear in the sidebar again.
            if parent_id in _descendants(rows, collection.id):
                raise Invalid("a folder cannot be moved inside itself")
            if _depth_of(rows, parent_id) + 1 + _height_of(rows, collection.id) >= _MAX_DEPTH:
                raise Invalid(f"folders may not nest more than {_MAX_DEPTH} deep")

    if name != collection.name or parent_id != collection.parent_id:
        _assert_free(rows, name=name, parent_id=parent_id, exclude_id=collection.id)

    collection.name = name
    collection.parent_id = parent_id
    if "position" in changes:
        position = changes["position"]
        if not isinstance(position, int) or isinstance(position, bool):
            raise Invalid("position must be an integer")
        collection.position = position
    return collection


def delete_collection(session: Session, *, user_id: str, collection_id: str) -> int:
    """Delete one folder. Returns how many children were promoted.

    Two deliberate choices, both about not destroying more than was asked for:

    * **Papers survive.** Only the membership rows go (the FK cascades them), so
      a paper that was filed nowhere else simply falls back into 未分类. The
      documents themselves are the recycle bin's business, not a folder's.
    * **Children are promoted, not deleted.** ``Collection.parent_id`` cascades,
      so a bare ``DELETE`` would silently take the entire subtree with it — an
      irreversible loss of folders the user never named, since there is no trash
      for collections. Re-parenting them onto the deleted folder's parent first
      means "delete this folder" removes exactly the one folder in the request.

    A promoted child can collide by name with an existing sibling, which is the
    one place the sibling-uniqueness rule is not upheld. That is the accepted
    trade: refusing an otherwise valid delete because of a name clash, or
    renaming a folder behind the user's back, are both worse than two rows the
    user can rename themselves.
    """
    collection = _require_collection(session, collection_id, user_id=user_id)
    # Identified by an owner-scoped SELECT rather than counted from the UPDATE's
    # rowcount, so the number returned is one this function can name row by row.
    children = [
        row.id
        for row in _load_collections(session, user_id=user_id)
        if row.parent_id == collection.id
    ]
    if children:
        session.execute(
            update(Collection)
            .where(Collection.id.in_(children), Collection.user_id == user_id)
            .values(parent_id=collection.parent_id)
        )
        session.flush()  # the promotion must land before the DELETE cascades
    session.delete(collection)
    session.flush()
    return len(children)


def add_papers(
    session: Session, *, user_id: str, collection_id: str, paper_ids: list[str]
) -> int:
    """File papers into a folder. Returns how many were newly added.

    Both ends are verified against the caller first, so neither "add someone
    else's paper to my folder" nor "add my paper to someone else's folder" can
    write a row. Already-filed papers are skipped rather than rejected, so the
    call is idempotent — a double-click must not be an error.

    A trashed paper may be filed: it stays out of every count while it is in the
    bin, and restoring it puts it back in the folder the user chose.
    """
    collection = _require_collection(session, collection_id, user_id=user_id)
    ids = _require_papers(session, paper_ids, user_id=user_id)

    already = set(
        session.scalars(
            select(PaperCollection.paper_id).where(
                PaperCollection.collection_id == collection.id,
                PaperCollection.paper_id.in_(ids),
            )
        )
    )
    added = 0
    for pid in ids:
        if pid in already:
            continue
        session.add(PaperCollection(paper_id=pid, collection_id=collection.id))
        added += 1
    session.flush()
    return added


def remove_paper(session: Session, *, user_id: str, collection_id: str, paper_id: str) -> None:
    """Unfile one paper. The paper itself is untouched."""
    collection = _require_collection(session, collection_id, user_id=user_id)
    paper = _require_paper(session, paper_id, user_id=user_id)
    membership = session.get(PaperCollection, (paper.id, collection.id))
    if membership is None:
        raise NotFound("Paper is not in this collection")
    session.delete(membership)
    session.flush()


# ------------------------------------------------------------------------ tags


def list_tags(session: Session, *, user_id: str) -> list[TagCount]:
    """The caller's tags with their live paper counts, ordered by name."""
    _require_owner(user_id)
    counts = _tag_counts(session, user_id=user_id)
    rows = list(session.scalars(select(Tag).where(Tag.user_id == user_id)))
    rows.sort(key=lambda t: (t.name.casefold(), t.created_at))
    return [TagCount(tag=t, paper_count=counts.get(t.id, 0)) for t in rows]


def create_tag(session: Session, *, user_id: str, name: str, color: str | None = None) -> Tag:
    _require_owner(user_id)
    clean = _clean_name(name, limit=_MAX_TAG_NAME)
    existing = list(session.scalars(select(Tag).where(Tag.user_id == user_id)))
    _assert_free(existing, name=clean)
    tag = Tag(user_id=user_id, name=clean, color=_clean_color(color))
    session.add(tag)
    session.flush()
    return tag


def update_tag(
    session: Session, *, user_id: str, tag_id: str, changes: dict[str, object]
) -> Tag:
    tag = _require_tag(session, tag_id, user_id=user_id)
    if "name" in changes:
        clean = _clean_name(changes["name"], limit=_MAX_TAG_NAME)
        # Re-casing a tag ("nlp" -> "NLP") must stay legal, so the tag itself is
        # excluded from its own uniqueness check.
        existing = list(session.scalars(select(Tag).where(Tag.user_id == user_id)))
        _assert_free(existing, name=clean, exclude_id=tag.id)
        tag.name = clean
    if "color" in changes:
        raw = changes["color"]
        tag.color = _clean_color(None if raw is None else str(raw))
    return tag


def delete_tag(session: Session, *, user_id: str, tag_id: str) -> None:
    """Delete a tag. Its papers keep everything except this label."""
    tag = _require_tag(session, tag_id, user_id=user_id)
    session.delete(tag)  # PaperTag rows follow via the FK cascade
    session.flush()


def paper_tags(session: Session, *, user_id: str, paper_id: str) -> list[Tag]:
    """The tags on one of the caller's papers, ordered by name."""
    paper = _require_paper(session, paper_id, user_id=user_id)
    rows = list(
        session.scalars(
            select(Tag)
            .join(PaperTag, PaperTag.tag_id == Tag.id)
            .where(PaperTag.paper_id == paper.id, Tag.user_id == user_id)
        )
    )
    rows.sort(key=lambda t: (t.name.casefold(), t.created_at))
    return rows


def set_paper_tags(
    session: Session, *, user_id: str, paper_id: str, tag_ids: list[str]
) -> list[Tag]:
    """Replace a paper's tags wholesale. Returns the resulting set.

    Replace rather than merge because the UI is a checkbox list: what the client
    sends is the complete intended state, and a merge would make unchecking
    impossible. An empty list therefore clears every tag, which is a legitimate
    request and not an empty-input error.
    """
    paper = _require_paper(session, paper_id, user_id=user_id)
    if len(tag_ids) > _MAX_BULK:
        raise Invalid(f"at most {_MAX_BULK} tags can be set in one request")

    wanted: list[str] = []
    for tid in tag_ids:
        if tid not in wanted:
            wanted.append(tid)
    if wanted:
        owned = set(
            session.scalars(select(Tag.id).where(Tag.id.in_(wanted), Tag.user_id == user_id))
        )
        if len(owned) != len(wanted):
            # Someone else's tag is indistinguishable from one that never
            # existed, exactly as for papers and collections.
            raise NotFound("Tag not found")

    current = set(
        session.scalars(select(PaperTag.tag_id).where(PaperTag.paper_id == paper.id))
    )
    for tid in current - set(wanted):
        link = session.get(PaperTag, (paper.id, tid))
        if link is not None:
            session.delete(link)
    for tid in wanted:
        if tid not in current:
            session.add(PaperTag(paper_id=paper.id, tag_id=tid))
    session.flush()
    return paper_tags(session, user_id=user_id, paper_id=paper.id)
