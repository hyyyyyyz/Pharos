"""Highlights and the paper-level note: ownership, geometry, and the note upsert.

Two families of test, and they defend different things.

**Ownership.** Pharos is multi-user, and the annotation endpoints take ids from
two places at once — a paper id in the path, a highlight id in the path of the
PATCH/DELETE pair. Each is an independent chance to touch a row that is not the
caller's, and each must fail *identically* to a row that does not exist. Every
``NotFound`` assertion below is really an assertion that a probe cannot tell
"not yours" from "no such thing" and therefore cannot walk ids to enumerate
another researcher's reading.

**Geometry.** ``rects`` is the only place in this API where a browser hands over
raw numbers that are written to a column and later read back to paint on screen.
It is free-form JSON, so it is also the easiest thing in the codebase to make
hostile: a hundred thousand rectangles, a ``NaN`` that ``JSON.parse`` chokes on
and that would cost the user *every* highlight on the paper rather than one, a
string where a coordinate belongs. The validation tests pin each of those, and
the round-trip test pins the thing a coordinate bug would break first — that
what the reader stored is bit-for-bit what it gets back.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import annotate as annotate_api
from pharos.api.deps import current_user
from pharos.api.schemas import as_utc
from pharos.db.models import Highlight, Note, Paper, User
from pharos.db.session import init_engine, session_scope
from pharos.services import annotate
from pharos.services.annotate import Invalid, NotFound
from sqlalchemy import delete, select

#: Prefixed rather than named "owner"/"other" because ``init_engine`` memoises:
#: whichever test module runs first wins and every later one shares that
#: database. Ids scoped to this module cannot collide with another module's
#: fixture rows — and, more importantly, cannot be deleted by one.
OWNER = "annotate-owner"
OTHER = "annotate-other"
_USERS = (OWNER, OTHER)

OWNER_PAPER = "annotate-paper-owner"
OTHER_PAPER = "annotate-paper-other"

#: A three-line selection, in PDF points, bottom-left origin. Deliberately
#: fractional: integers would let a rounding bug pass unnoticed.
RECTS = [
    {"x": 72.5, "y": 640.25, "w": 200.75, "h": 11.5},
    {"x": 72.5, "y": 626.0, "w": 210.0, "h": 11.5},
    {"x": 72.5, "y": 611.75, "w": 96.25, "h": 11.5},
]


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Ensure a database, this module's two users, and one paper each.

    The path is only honoured if this module is the first to call
    ``init_engine``; otherwise the call is a no-op returning the existing
    engine. So every insert is written "create if absent" rather than as a plain
    add, which would hit a unique violation on a second run against a shared
    database.
    """
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
    """Wipe *this module's* annotations between tests.

    Scoped to ``_USERS`` rather than truncating the tables, because the engine is
    shared with every other test module and deleting their rows out from under
    them would make this file's cleanup another file's failure. The papers
    themselves are left alone — they are fixture state, not per-test state.
    """
    with session_scope() as s:
        s.execute(delete(Highlight).where(Highlight.user_id.in_(_USERS)))
        s.execute(delete(Note).where(Note.user_id.in_(_USERS)))


def _make(
    *,
    user_id: str = OWNER,
    paper_id: str = OWNER_PAPER,
    kind: str = "original",
    page: int = 3,
    rects: object = None,
    **kwargs: object,
) -> str:
    with session_scope() as s:
        return annotate.create_highlight(
            s,
            user_id=user_id,
            paper_id=paper_id,
            kind=kind,
            page=page,
            rects=RECTS if rects is None else rects,
            **kwargs,
        ).id


# ------------------------------------------------------------------ ownership


def test_cannot_list_highlights_on_another_users_paper() -> None:
    """Not 403. A paper id that is not mine is a paper that does not exist."""
    with session_scope() as s, pytest.raises(NotFound):
        annotate.list_highlights(s, user_id=OWNER, paper_id=OTHER_PAPER)


def test_cannot_highlight_another_users_paper() -> None:
    """The 404 lands before anything is written, not after."""
    with session_scope() as s, pytest.raises(NotFound):
        annotate.create_highlight(
            s, user_id=OWNER, paper_id=OTHER_PAPER, kind="original", page=1, rects=RECTS
        )
    with session_scope() as s:
        assert s.scalar(select(Highlight).where(Highlight.paper_id == OTHER_PAPER)) is None


def test_cannot_patch_another_users_highlight() -> None:
    """And the row is untouched — a rejected write must not be a partial one."""
    theirs = _make(user_id=OTHER, paper_id=OTHER_PAPER, color="green")
    with session_scope() as s, pytest.raises(NotFound):
        annotate.update_highlight(
            s, user_id=OWNER, highlight_id=theirs, changes={"color": "blue"}
        )
    with session_scope() as s:
        assert s.get(Highlight, theirs).color == "green"


def test_cannot_delete_another_users_highlight() -> None:
    theirs = _make(user_id=OTHER, paper_id=OTHER_PAPER)
    with session_scope() as s, pytest.raises(NotFound):
        annotate.delete_highlight(s, user_id=OWNER, highlight_id=theirs)
    with session_scope() as s:
        assert s.get(Highlight, theirs) is not None


def test_cannot_read_or_write_another_users_note() -> None:
    with session_scope() as s, pytest.raises(NotFound):
        annotate.get_note(s, user_id=OWNER, paper_id=OTHER_PAPER)
    with session_scope() as s, pytest.raises(NotFound):
        annotate.set_note(s, user_id=OWNER, paper_id=OTHER_PAPER, body="mine now")
    with session_scope() as s:
        assert s.scalar(select(Note).where(Note.paper_id == OTHER_PAPER)) is None


def test_listing_filters_on_the_highlights_own_owner() -> None:
    """A mis-owned row is excluded by the listing, not merely by the write path.

    Constructed by hand precisely because the write path cannot produce it: the
    point is that the SELECT scopes on ``Highlight.user_id`` in its own right,
    rather than trusting that every row reachable through one of my papers must
    be mine. If that filter is ever dropped, this is what notices — a Zotero
    annotation import or a hand-run SQL statement could put such a row there,
    and it would then surface in the wrong reader.
    """
    mine = _make()
    with session_scope() as s:
        s.add(
            Highlight(
                id="annotate-smuggled",
                user_id=OTHER,
                paper_id=OWNER_PAPER,  # my paper, their highlight
                kind="original",
                page=1,
                rects=json.dumps(RECTS),
            )
        )
    with session_scope() as s:
        ids = [h.id for h in annotate.list_highlights(s, user_id=OWNER, paper_id=OWNER_PAPER)]
    assert ids == [mine]


# ------------------------------------------------------------------- geometry


def test_rects_round_trip_exactly() -> None:
    """What the reader stored is what it gets back — the whole point of the column.

    A highlight that comes back even slightly off lands beside the sentence it
    was drawn on, and the user has no way to tell whether they mis-dragged or
    the app lost it.
    """
    hid = _make()
    with session_scope() as s:
        rects = annotate.load_rects(s.get(Highlight, hid).rects)
    assert [{"x": r.x, "y": r.y, "w": r.w, "h": r.h} for r in rects] == RECTS


def test_coordinates_are_rounded_not_truncated() -> None:
    """Sub-micron precision is dropped, and dropped by rounding.

    0.005pt is about 1.8 micrometres of paper. Storing seventeen float digits
    per number would bloat every page load for a difference no screen can show;
    truncating instead of rounding would bias every highlight one direction.
    """
    hid = _make(rects=[{"x": 10.123456, "y": 20.987654, "w": 5.005, "h": 2.5}])
    with session_scope() as s:
        (rect,) = annotate.load_rects(s.get(Highlight, hid).rects)
    assert (rect.x, rect.y, rect.h) == (10.12, 20.99, 2.5)


@pytest.mark.parametrize(
    "rects",
    [
        pytest.param("not a list", id="not-a-list"),
        pytest.param([], id="empty"),
        pytest.param([{"x": 1, "y": 2, "w": 3}], id="missing-h"),
        pytest.param([{"x": 1, "y": 2, "w": 3, "h": 4, "z": 5}], id="extra-key"),
        pytest.param([{"x": "1", "y": 2, "w": 3, "h": 4}], id="string-coord"),
        pytest.param([{"x": True, "y": 2, "w": 3, "h": 4}], id="bool-coord"),
        pytest.param([{"x": float("nan"), "y": 2, "w": 3, "h": 4}], id="nan"),
        pytest.param([{"x": float("inf"), "y": 2, "w": 3, "h": 4}], id="inf"),
        pytest.param([{"x": 1, "y": 2, "w": 0, "h": 4}], id="zero-width"),
        pytest.param([{"x": 1, "y": 2, "w": 3, "h": -4}], id="negative-height"),
        pytest.param([{"x": 1e9, "y": 2, "w": 3, "h": 4}], id="off-the-page"),
        pytest.param([[1, 2, 3, 4]], id="array-not-object"),
        pytest.param([None], id="null-rect"),
    ],
)
def test_malformed_rects_are_refused(rects: object) -> None:
    with pytest.raises(Invalid):
        annotate.clean_rects(rects)


def test_too_many_rects_are_refused() -> None:
    """A hostile client could otherwise store a megabyte per highlight, and every
    later page load would have to carry it."""
    one = {"x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0}
    assert len(annotate.clean_rects([one] * annotate.MAX_RECTS)) == annotate.MAX_RECTS
    with pytest.raises(Invalid):
        annotate.clean_rects([one] * (annotate.MAX_RECTS + 1))


def test_one_bad_rect_rejects_the_whole_highlight() -> None:
    """Not "keep the good lines". Painting three lines of a four-line passage is
    a highlight the user never drew, and nothing on screen would say so."""
    with pytest.raises(Invalid):
        annotate.clean_rects([*RECTS, {"x": 1, "y": 2, "w": -3, "h": 4}])


def test_slightly_negative_coordinates_are_allowed() -> None:
    """A selection rectangle rounded outward at the page edge legitimately runs
    a hair past zero; refusing it would drop highlights on the first line."""
    assert annotate.clean_rects([{"x": -0.5, "y": -1.25, "w": 10, "h": 12}])


def test_stored_garbage_reads_back_as_no_geometry() -> None:
    """One unreadable row must not cost the user every other highlight.

    ``load_rects`` swallows what it cannot parse so the endpoint still answers;
    the mark simply does not paint. The alternative — raising — turns one
    hand-edited row into a reader that fails to open the paper at all.
    """
    assert annotate.load_rects("{not json") == []
    assert annotate.load_rects(json.dumps([{"x": 1, "y": 2}])) == []
    assert annotate.load_rects(None) == []


def test_dump_rects_never_emits_json_that_cannot_be_parsed() -> None:
    """``NaN``/``Infinity`` are Python's JSON extensions, not JSON. One of them in
    the column makes ``JSON.parse`` throw on the *whole* response."""
    with pytest.raises(ValueError):
        annotate.dump_rects([annotate.Rect(x=float("nan"), y=0.0, w=1.0, h=1.0)])


# ----------------------------------------------------------------- validation


@pytest.mark.parametrize("kind", ["", "ORIGINAL ", "translated", "zh", None, 3])
def test_bad_kind_is_refused(kind: object) -> None:
    if kind == "ORIGINAL ":
        # Case and surrounding space are normalised, not rejected.
        with session_scope() as s:
            row = annotate.create_highlight(
                s,
                user_id=OWNER,
                paper_id=OWNER_PAPER,
                kind="ORIGINAL ",
                page=1,
                rects=RECTS,
            )
            assert row.kind == "original"
        return
    with session_scope() as s, pytest.raises(Invalid):
        annotate.create_highlight(
            s, user_id=OWNER, paper_id=OWNER_PAPER, kind=kind, page=1, rects=RECTS
        )


@pytest.mark.parametrize("page", [0, -1, 1.5, True, "3", annotate.MAX_PAGE + 1])
def test_bad_page_is_refused(page: object) -> None:
    with session_scope() as s, pytest.raises(Invalid):
        annotate.create_highlight(
            s, user_id=OWNER, paper_id=OWNER_PAPER, kind="original", page=page, rects=RECTS
        )


def test_page_is_not_bounded_by_the_originals_page_count() -> None:
    """The dual rendition interleaves source and translation, so it legitimately
    has more pages than ``Paper.page_count`` — which describes the original.
    Validating against that column would reject valid bilingual highlights."""
    hid = _make(kind="dual", page=30)  # the fixture paper's page_count is 15
    with session_scope() as s:
        assert s.get(Highlight, hid).page == 30


def test_unknown_colour_is_refused_and_absent_colour_defaults() -> None:
    """Colours are *token names* the frontend maps to ``--c-*`` variables. A hex
    through this door would put a hard-coded colour in the database that no theme
    could override."""
    with session_scope() as s, pytest.raises(Invalid):
        annotate.create_highlight(
            s,
            user_id=OWNER,
            paper_id=OWNER_PAPER,
            kind="original",
            page=1,
            rects=RECTS,
            color="#ffcc00",
        )
    with session_scope() as s:
        row = annotate.create_highlight(
            s, user_id=OWNER, paper_id=OWNER_PAPER, kind="original", page=1, rects=RECTS
        )
        assert row.color == annotate.DEFAULT_COLOR


def test_overlong_selected_text_is_truncated_not_rejected() -> None:
    """Losing the tail of a quotation beats losing the highlight the user just drew."""
    hid = _make(text="x" * (annotate.MAX_TEXT + 500))
    with session_scope() as s:
        assert len(s.get(Highlight, hid).text) == annotate.MAX_TEXT


# ------------------------------------------------------------------- listing


def test_kind_filter_keeps_renditions_apart() -> None:
    """Three renditions are three documents whose pages do not correspond, so a
    highlight drawn on one is never shown on another."""
    original = _make(kind="original")
    mono = _make(kind="mono")
    _make(kind="dual")
    with session_scope() as s:
        got = annotate.list_highlights(
            s, user_id=OWNER, paper_id=OWNER_PAPER, kind="original"
        )
        assert [h.id for h in got] == [original]
        got = annotate.list_highlights(s, user_id=OWNER, paper_id=OWNER_PAPER, kind="mono")
        assert [h.id for h in got] == [mono]
        assert len(annotate.list_highlights(s, user_id=OWNER, paper_id=OWNER_PAPER)) == 3


# -------------------------------------------------------------------- updates


def test_recolour_and_note_edit() -> None:
    hid = _make()
    with session_scope() as s:
        row = annotate.update_highlight(
            s, user_id=OWNER, highlight_id=hid, changes={"color": "blue", "note": " 关键 "}
        )
        assert (row.color, row.note) == ("blue", "关键")
        assert row.updated_at is not None


def test_omitted_key_leaves_the_field_alone_and_null_clears_it() -> None:
    """The distinction the PATCH exists to preserve: "don't touch this" is not
    the same request as "empty this"."""
    hid = _make(color="green", note="first")
    with session_scope() as s:
        row = annotate.update_highlight(
            s, user_id=OWNER, highlight_id=hid, changes={"color": "pink"}
        )
        assert row.note == "first"  # untouched
    with session_scope() as s:
        row = annotate.update_highlight(
            s, user_id=OWNER, highlight_id=hid, changes={"note": None}
        )
        assert row.note is None
        assert row.color == "pink"


def test_delete_removes_only_that_highlight() -> None:
    keep = _make()
    drop = _make()
    with session_scope() as s:
        annotate.delete_highlight(s, user_id=OWNER, highlight_id=drop)
    with session_scope() as s:
        ids = [h.id for h in annotate.list_highlights(s, user_id=OWNER, paper_id=OWNER_PAPER)]
    assert ids == [keep]


# ---------------------------------------------------------------------- notes


def test_note_is_absent_until_written() -> None:
    """``None``, not a blank row: "nobody ever wrote anything" and "somebody
    wrote something and cleared it" are different facts."""
    with session_scope() as s:
        assert annotate.get_note(s, user_id=OWNER, paper_id=OWNER_PAPER) is None


def test_note_upsert_keeps_one_row_and_its_created_at() -> None:
    """Two saves are an edit, not two notes — and the row remembers when the user
    first wrote about this paper.

    Both timestamps go through ``as_utc`` because SQLite has no timezone type: the
    value we just wrote comes back aware, the same value re-read on the next
    session comes back naive, and comparing them raw fails on the tzinfo rather
    than on anything this test is about.
    """
    with session_scope() as s:
        first = annotate.set_note(s, user_id=OWNER, paper_id=OWNER_PAPER, body="第一版")
        note_id, created = first.id, as_utc(first.created_at)
    with session_scope() as s:
        second = annotate.set_note(s, user_id=OWNER, paper_id=OWNER_PAPER, body="第二版")
        assert second.id == note_id
        assert as_utc(second.created_at) == created
        assert second.updated_at is not None
    with session_scope() as s:
        rows = list(s.scalars(select(Note).where(Note.user_id == OWNER)))
        assert len(rows) == 1
        assert rows[0].body == "第二版"


def test_note_can_be_cleared() -> None:
    """An empty body is a request, not an error."""
    with session_scope() as s:
        annotate.set_note(s, user_id=OWNER, paper_id=OWNER_PAPER, body="something")
    with session_scope() as s:
        annotate.set_note(s, user_id=OWNER, paper_id=OWNER_PAPER, body="")
    with session_scope() as s:
        assert annotate.get_note(s, user_id=OWNER, paper_id=OWNER_PAPER).body == ""


def test_note_reads_back_the_row_it_wrote_when_duplicates_exist() -> None:
    """``models.py`` has no unique constraint on ``(user_id, paper_id)`` and is not
    ours to change, so two concurrent first-writes could in principle leave two
    rows. What must hold regardless is that ``get_note`` and ``set_note`` pick the
    *same* one, or a client would sometimes read back something it never wrote.
    """
    older = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with session_scope() as s:
        s.add(
            Note(
                id="annotate-note-old",
                user_id=OWNER,
                paper_id=OWNER_PAPER,
                body="older",
                created_at=older,
            )
        )
        s.add(
            Note(
                id="annotate-note-new",
                user_id=OWNER,
                paper_id=OWNER_PAPER,
                body="newer",
                created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        )
    with session_scope() as s:
        annotate.set_note(s, user_id=OWNER, paper_id=OWNER_PAPER, body="written")
    with session_scope() as s:
        assert annotate.get_note(s, user_id=OWNER, paper_id=OWNER_PAPER).body == "written"


def test_overlong_note_is_refused() -> None:
    """Unlike the quoted passage, the note is not truncated: it is what the user
    typed, and silently dropping the end of their write-up would lose work they
    cannot see is gone."""
    with session_scope() as s, pytest.raises(Invalid):
        annotate.set_note(
            s, user_id=OWNER, paper_id=OWNER_PAPER, body="x" * (annotate.MAX_NOTE_BODY + 1)
        )


# ------------------------------------------------------------------ endpoints


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """The annotate router with the signed-in user selectable per test.

    ``current_user`` is overridden rather than driven with a real JWT: token
    minting needs an instance secret and is covered thoroughly elsewhere, and
    what is under test here is the scoping, not the credential. The override is a
    *generator* yielding a live ORM object inside ``session_scope``, exactly as
    the real dependency does, so writes are committed on the way out and these
    are real round trips rather than assertions about an in-memory object.
    """
    app = FastAPI()
    app.include_router(annotate_api.router)
    app.state.signed_in_as = OWNER

    def _override() -> Iterator[User]:
        with session_scope() as s:
            yield s.scalar(select(User).where(User.id == app.state.signed_in_as))

    app.dependency_overrides[current_user] = _override
    with TestClient(app) as c:
        yield c


def test_endpoint_hides_another_users_paper_behind_404(client: TestClient) -> None:
    """404, never 403. A 403 confirms the id is real and turns the endpoint into
    an oracle for walking ids across other users' libraries."""
    for response in (
        client.get(f"/api/papers/{OTHER_PAPER}/highlights"),
        client.post(
            f"/api/papers/{OTHER_PAPER}/highlights",
            json={"kind": "original", "page": 1, "rects": RECTS},
        ),
        client.get(f"/api/papers/{OTHER_PAPER}/note"),
        client.put(f"/api/papers/{OTHER_PAPER}/note", json={"body": "hi"}),
    ):
        assert response.status_code == 404, response.text


def test_endpoint_hides_another_users_highlight_behind_404(client: TestClient) -> None:
    theirs = _make(user_id=OTHER, paper_id=OTHER_PAPER)
    assert client.patch(f"/api/highlights/{theirs}", json={"color": "blue"}).status_code == 404
    assert client.delete(f"/api/highlights/{theirs}").status_code == 404
    assert client.patch("/api/highlights/no-such-id", json={"color": "blue"}).status_code == 404


def test_full_highlight_lifecycle_over_http(client: TestClient) -> None:
    created = client.post(
        f"/api/papers/{OWNER_PAPER}/highlights",
        json={"kind": "mono", "page": 4, "rects": RECTS, "text": "we propose", "color": "green"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["rects"] == RECTS  # survives the JSON round trip unchanged
    assert (body["kind"], body["page"], body["color"]) == ("mono", 4, "green")

    hid = body["id"]
    patched = client.patch(f"/api/highlights/{hid}", json={"color": "pink", "note": "核心"})
    assert patched.status_code == 200
    assert (patched.json()["color"], patched.json()["note"]) == ("pink", "核心")

    listed = client.get(f"/api/papers/{OWNER_PAPER}/highlights", params={"kind": "mono"})
    assert [h["id"] for h in listed.json()] == [hid]
    others = client.get(f"/api/papers/{OWNER_PAPER}/highlights", params={"kind": "original"})
    assert others.json() == []

    assert client.delete(f"/api/highlights/{hid}").status_code == 204
    assert client.get(f"/api/papers/{OWNER_PAPER}/highlights").json() == []


def test_geometry_is_not_patchable(client: TestClient) -> None:
    """``page``/``kind``/``rects`` record where the user dragged. A highlight that
    a PATCH could move to another page is no longer a record of anything."""
    hid = _make()
    for payload in ({"page": 9}, {"kind": "dual"}, {"rects": RECTS}):
        assert client.patch(f"/api/highlights/{hid}", json=payload).status_code == 422


def test_empty_patch_is_a_400(client: TestClient) -> None:
    hid = _make()
    assert client.patch(f"/api/highlights/{hid}", json={}).status_code == 400


def test_malformed_rects_are_refused_over_http(client: TestClient) -> None:
    """422 from the edge model where the *shape* is wrong, 400 from the service
    where the shape is fine but the geometry is impossible."""
    def post(rects: object) -> int:
        return client.post(
            f"/api/papers/{OWNER_PAPER}/highlights",
            json={"kind": "original", "page": 1, "rects": rects},
        ).status_code

    assert post([]) == 422
    assert post([{"x": 1, "y": 2, "w": 3}]) == 422
    assert post([{"x": "no", "y": 2, "w": 3, "h": 4}]) == 422
    assert post([{"x": 1, "y": 2, "w": 3, "h": 4, "z": 5}]) == 422
    assert post([{"x": 1, "y": 2, "w": 3, "h": 4}] * (annotate.MAX_RECTS + 1)) == 422
    assert post([{"x": 1, "y": 2, "w": 0, "h": 4}]) == 400
    assert post([{"x": 1e9, "y": 2, "w": 3, "h": 4}]) == 400


def test_note_endpoint_reports_absence_as_empty_string(client: TestClient) -> None:
    """The client renders an editor, not a branch on null."""
    fresh = client.get(f"/api/papers/{OWNER_PAPER}/note").json()
    assert fresh == {"paper_id": OWNER_PAPER, "body": "", "created_at": None, "updated_at": None}

    saved = client.put(f"/api/papers/{OWNER_PAPER}/note", json={"body": "读完了"})
    assert saved.status_code == 200
    assert saved.json()["body"] == "读完了"
    assert client.get(f"/api/papers/{OWNER_PAPER}/note").json()["body"] == "读完了"


def test_signing_in_as_the_other_user_sees_none_of_it(client: TestClient) -> None:
    """The end-to-end statement of the whole file: two accounts, one reader, and
    nothing crosses."""
    client.post(
        f"/api/papers/{OWNER_PAPER}/highlights",
        json={"kind": "original", "page": 1, "rects": RECTS},
    )
    client.put(f"/api/papers/{OWNER_PAPER}/note", json={"body": "私人笔记"})

    client.app.state.signed_in_as = OTHER
    assert client.get(f"/api/papers/{OWNER_PAPER}/highlights").status_code == 404
    assert client.get(f"/api/papers/{OWNER_PAPER}/note").status_code == 404
    assert client.get(f"/api/papers/{OTHER_PAPER}/highlights").json() == []
    assert client.get(f"/api/papers/{OTHER_PAPER}/note").json()["body"] == ""
