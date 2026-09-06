"""Page notes (文本框 / 便利贴): ownership, validation, and partial update.

Same three families as ``test_tape.py``, defending the same three things —
ownership (a note id in a path must fail identically to a row that does not
exist), hostile input rejected at the door, and a field left OUT of a PATCH
left untouched, which the ``...`` sentinel exists to guarantee — plus the one
concern specific to text: a body is user input that ends up in a page, so its
length is bounded and its NULs are stripped rather than handed to a driver.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import pagenote as pagenote_api
from pharos.api.deps import current_user
from pharos.db.models import PageNote, Paper, User
from pharos.db.session import init_engine, session_scope
from pharos.services import pagenote
from pharos.services.annotate import Invalid, NotFound
from sqlalchemy import delete, select

OWNER = "note-owner"
OTHER = "note-other"
_USERS = (OWNER, OTHER)

OWNER_PAPER = "note-paper-owner"
OTHER_PAPER = "note-paper-other"

BOX = {"x": 100.0, "y": 500.0, "w": 160.0, "h": 48.0}


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    init_engine(tmp_path_factory.mktemp("db") / "pharos.db")
    with session_scope() as s:
        for uid in _USERS:
            if s.get(User, uid) is None:
                s.add(User(id=uid, email=f"{uid}@example.test", password_hash="x"))
    for paper_id, owner in ((OWNER_PAPER, OWNER), (OTHER_PAPER, OTHER)):
        with session_scope() as s:
            if s.get(Paper, paper_id) is None:
                s.add(
                    Paper(
                        id=paper_id,
                        user_id=owner,
                        title="Attention Is All You Need",
                        orig_sha256=f"sha-{paper_id}",
                        orig_filename="attention.pdf",
                        page_count=15,
                    )
                )


@pytest.fixture(autouse=True)
def _clean() -> None:
    with session_scope() as s:
        s.execute(delete(PageNote).where(PageNote.user_id.in_(_USERS)))


def _make(
    *,
    user_id: str = OWNER,
    paper_id: str = OWNER_PAPER,
    kind: str = "original",
    page: int = 3,
    **kwargs: object,
) -> str:
    spec = {**BOX, **kwargs}
    with session_scope() as s:
        return pagenote.create_note(
            s, user_id=user_id, paper_id=paper_id, kind=kind, page=page, **spec
        ).id


# ------------------------------------------------------------------ ownership


def test_cannot_list_notes_on_another_users_paper() -> None:
    with session_scope() as s, pytest.raises(NotFound):
        pagenote.list_notes(s, user_id=OWNER, paper_id=OTHER_PAPER)


def test_cannot_create_a_note_on_another_users_paper() -> None:
    with session_scope() as s, pytest.raises(NotFound):
        pagenote.create_note(
            s, user_id=OWNER, paper_id=OTHER_PAPER, kind="original", page=1, **BOX
        )
    with session_scope() as s:
        assert s.scalar(select(PageNote).where(PageNote.paper_id == OTHER_PAPER)) is None


def test_cannot_read_another_users_note_by_id() -> None:
    theirs = _make(user_id=OTHER, paper_id=OTHER_PAPER)
    with session_scope() as s, pytest.raises(NotFound):
        pagenote.update_note(s, user_id=OWNER, note_id=theirs, body="mine now")
    with session_scope() as s:
        assert s.get(PageNote, theirs).body == ""


def test_cannot_delete_another_users_note() -> None:
    theirs = _make(user_id=OTHER, paper_id=OTHER_PAPER)
    with session_scope() as s, pytest.raises(NotFound):
        pagenote.delete_note(s, user_id=OWNER, note_id=theirs)
    with session_scope() as s:
        assert s.get(PageNote, theirs) is not None


def test_owner_scoping_is_required_not_optional() -> None:
    """An empty owner is a programming error, not "match everything"."""
    with session_scope() as s, pytest.raises(ValueError):
        pagenote.update_note(s, user_id="", note_id="whatever", body="x")


# ------------------------------------------------------------------- defaults


def test_a_fresh_note_is_empty_and_plain() -> None:
    """Created empty on purpose: the gesture is a tap, the content is typing."""
    nid = _make()
    with session_scope() as s:
        row = s.get(PageNote, nid)
    assert row.body == ""
    assert row.style == "text"
    assert row.color == "ink"
    assert row.size == 12.0


def test_a_sticky_note_keeps_its_style_and_colour() -> None:
    nid = _make(style="note", color="amber")
    with session_scope() as s:
        row = s.get(PageNote, nid)
    assert row.style == "note"
    assert row.color == "amber"


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "120", True, None])
def test_hostile_coordinates_are_refused(bad: object) -> None:
    with session_scope() as s, pytest.raises(Invalid):
        pagenote.create_note(
            s,
            user_id=OWNER,
            paper_id=OWNER_PAPER,
            kind="original",
            page=1,
            **{**BOX, "x": bad},
        )


def test_a_box_outside_the_size_range_is_refused() -> None:
    for bad in (0.0, 4.0, 9_000.0):
        with session_scope() as s, pytest.raises(Invalid):
            pagenote.create_note(
                s, user_id=OWNER, paper_id=OWNER_PAPER, kind="original", page=1, **{**BOX, "w": bad}
            )


def test_an_unknown_style_is_refused() -> None:
    with session_scope() as s, pytest.raises(Invalid):
        pagenote.create_note(
            s, user_id=OWNER, paper_id=OWNER_PAPER, kind="original", page=1, style="banner", **BOX
        )


def test_an_unknown_colour_is_refused() -> None:
    """Tokens, never hexes — so the palette stays one CSS file's business."""
    with session_scope() as s, pytest.raises(Invalid):
        pagenote.create_note(
            s, user_id=OWNER, paper_id=OWNER_PAPER, kind="original", page=1, color="#ff0000", **BOX
        )


def test_a_font_size_outside_the_range_is_refused() -> None:
    for bad in (0.5, 400.0):
        with session_scope() as s, pytest.raises(Invalid):
            pagenote.create_note(
                s, user_id=OWNER, paper_id=OWNER_PAPER, kind="original", page=1, size=bad, **BOX
            )


def test_an_oversized_body_is_refused() -> None:
    with session_scope() as s, pytest.raises(Invalid):
        pagenote.create_note(
            s,
            user_id=OWNER,
            paper_id=OWNER_PAPER,
            kind="original",
            page=1,
            body="x" * (pagenote.MAX_BODY + 1),
            **BOX,
        )


def test_nul_bytes_are_stripped_rather_than_stored() -> None:
    """They cannot round-trip through every driver and mean nothing in a note."""
    nid = _make(body="be\x00fore")
    with session_scope() as s:
        assert s.get(PageNote, nid).body == "before"


def test_a_non_string_body_is_refused() -> None:
    with session_scope() as s, pytest.raises(Invalid):
        pagenote.create_note(
            s, user_id=OWNER, paper_id=OWNER_PAPER, kind="original", page=1, body=42, **BOX
        )


# --------------------------------------------------------------- partial update


def test_a_field_left_out_of_an_update_is_untouched() -> None:
    """The whole point of the ``...`` sentinel: typing must not move the box."""
    nid = _make(body="first", color="amber", style="note")
    with session_scope() as s:
        pagenote.update_note(s, user_id=OWNER, note_id=nid, body="second")
    with session_scope() as s:
        row = s.get(PageNote, nid)
    assert row.body == "second"
    assert row.color == "amber"
    assert row.style == "note"
    assert row.x == BOX["x"]
    assert row.w == BOX["w"]


def test_null_resets_a_field_to_its_default() -> None:
    """Distinct from "not sent" — the sentinel exists to keep them apart."""
    nid = _make(color="amber", size=20.0)
    with session_scope() as s:
        pagenote.update_note(s, user_id=OWNER, note_id=nid, color=None, size=None)
    with session_scope() as s:
        row = s.get(PageNote, nid)
    assert row.color == "ink"
    assert row.size == 12.0


def test_moving_a_note_rewrites_it_in_place() -> None:
    """Unlike a stroke, whose geometry IS its samples, a note is four numbers."""
    nid = _make()
    with session_scope() as s:
        pagenote.update_note(s, user_id=OWNER, note_id=nid, x=200.0, y=300.0)
    with session_scope() as s:
        row = s.get(PageNote, nid)
    assert (row.x, row.y) == (200.0, 300.0)
    assert row.id == nid  # same row, not a replacement
    assert row.updated_at is not None


def test_listing_is_scoped_by_rendition() -> None:
    _make(kind="original")
    _make(kind="dual")
    with session_scope() as s:
        assert len(pagenote.list_notes(s, user_id=OWNER, paper_id=OWNER_PAPER)) == 2
        assert len(pagenote.list_notes(s, user_id=OWNER, paper_id=OWNER_PAPER, kind="dual")) == 1


# -------------------------------------------------------------------- the API


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(pagenote_api.router)
    app.dependency_overrides[current_user] = lambda: User(
        id=OWNER, email=f"{OWNER}@example.test", password_hash="x"
    )
    return TestClient(app)


def test_api_round_trips_a_note(client: TestClient) -> None:
    made = client.post(
        f"/api/papers/{OWNER_PAPER}/notes",
        json={"kind": "original", "page": 2, "style": "note", "color": "amber", **BOX},
    )
    assert made.status_code == 201, made.text
    note = made.json()
    assert note["style"] == "note"
    assert note["body"] == ""

    typed = client.patch(f"/api/notes/{note['id']}", json={"body": "见第 3 节"})
    assert typed.status_code == 200, typed.text
    assert typed.json()["body"] == "见第 3 节"
    # Typing must not have moved it.
    assert typed.json()["x"] == BOX["x"]

    listed = client.get(f"/api/papers/{OWNER_PAPER}/notes")
    assert [n["id"] for n in listed.json()] == [note["id"]]

    assert client.delete(f"/api/notes/{note['id']}").status_code == 204
    assert client.get(f"/api/papers/{OWNER_PAPER}/notes").json() == []


def test_api_refuses_an_unknown_field(client: TestClient) -> None:
    """``extra="forbid"`` — the wire shape is published, not merely suggested."""
    res = client.post(
        f"/api/papers/{OWNER_PAPER}/notes",
        json={"kind": "original", "page": 1, "rotation": 45, **BOX},
    )
    assert res.status_code == 422


def test_api_maps_a_missing_note_to_404(client: TestClient) -> None:
    assert client.patch("/api/notes/does-not-exist", json={"body": "x"}).status_code == 404
    assert client.delete("/api/notes/does-not-exist").status_code == 404


def test_api_maps_a_bad_style_to_400(client: TestClient) -> None:
    res = client.post(
        f"/api/papers/{OWNER_PAPER}/notes",
        json={"kind": "original", "page": 1, "style": "banner", **BOX},
    )
    assert res.status_code == 400
