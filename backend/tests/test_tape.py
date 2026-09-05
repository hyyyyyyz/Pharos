"""Tape marks: ownership, geometry, and the resize/straighten/reveal partial
update.

Same two test families as ``test_ink.py``, defending the same two things —
ownership (a tape id in a path must fail identically to a row that does not
exist) and geometry (hostile x/y/w/h/angle must fail at the door) — plus a
third specific to `update_tape`: a field left OUT of a PATCH must be untouched,
which the ``...`` sentinel exists to guarantee.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import tape as tape_api
from pharos.api.deps import current_user
from pharos.db.models import Paper, TapeMark, User
from pharos.db.session import init_engine, session_scope
from pharos.services import tape
from pharos.services.annotate import Invalid, NotFound
from sqlalchemy import delete, select

OWNER = "tape-owner"
OTHER = "tape-other"
_USERS = (OWNER, OTHER)

OWNER_PAPER = "tape-paper-owner"
OTHER_PAPER = "tape-paper-other"

RECT = {"x": 72.5, "y": 600.0, "w": 120.0, "h": 14.0}


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
        s.execute(delete(TapeMark).where(TapeMark.user_id.in_(_USERS)))


def _make(*, user_id: str = OWNER, paper_id: str = OWNER_PAPER, kind: str = "original", page: int = 3, **kwargs: object) -> str:
    spec = {**RECT, **kwargs}
    with session_scope() as s:
        return tape.create_tape(s, user_id=user_id, paper_id=paper_id, kind=kind, page=page, **spec).id


# ------------------------------------------------------------------ ownership


def test_cannot_list_tapes_on_another_users_paper() -> None:
    with session_scope() as s, pytest.raises(NotFound):
        tape.list_tapes(s, user_id=OWNER, paper_id=OTHER_PAPER)


def test_cannot_create_a_tape_on_another_users_paper() -> None:
    with session_scope() as s, pytest.raises(NotFound):
        tape.create_tape(s, user_id=OWNER, paper_id=OTHER_PAPER, kind="original", page=1, **RECT)
    with session_scope() as s:
        assert s.scalar(select(TapeMark).where(TapeMark.paper_id == OTHER_PAPER)) is None


def test_cannot_update_another_users_tape() -> None:
    theirs = _make(user_id=OTHER, paper_id=OTHER_PAPER)
    with session_scope() as s, pytest.raises(NotFound):
        tape.update_tape(s, user_id=OWNER, tape_id=theirs, revealed=True)
    with session_scope() as s:
        assert s.get(TapeMark, theirs).revealed is False


def test_cannot_delete_another_users_tape() -> None:
    theirs = _make(user_id=OTHER, paper_id=OTHER_PAPER)
    with session_scope() as s, pytest.raises(NotFound):
        tape.delete_tape(s, user_id=OWNER, tape_id=theirs)
    with session_scope() as s:
        assert s.get(TapeMark, theirs) is not None


# ------------------------------------------------------------------- defaults


def test_a_fresh_tape_is_covered_and_unrotated() -> None:
    tid = _make()
    with session_scope() as s:
        row = s.get(TapeMark, tid)
    assert row.revealed is False
    assert row.angle == 0.0


# --------------------------------------------------------------- reveal / cover


def test_tap_toggles_revealed_without_touching_geometry() -> None:
    tid = _make()
    with session_scope() as s:
        tape.update_tape(s, user_id=OWNER, tape_id=tid, revealed=True)
    with session_scope() as s:
        row = s.get(TapeMark, tid)
    assert row.revealed is True
    assert (row.x, row.y, row.w, row.h) == (RECT["x"], RECT["y"], RECT["w"], RECT["h"])


# -------------------------------------------------------------------- resize


def test_resize_changes_only_w_and_h() -> None:
    tid = _make()
    with session_scope() as s:
        tape.update_tape(s, user_id=OWNER, tape_id=tid, w=200.0, h=18.0)
    with session_scope() as s:
        row = s.get(TapeMark, tid)
    assert (row.w, row.h) == (200.0, 18.0)
    assert (row.x, row.y) == (RECT["x"], RECT["y"])


@pytest.mark.parametrize("size", [0, -5, tape.MAX_SIZE + 1])
def test_hostile_sizes_are_refused(size: float) -> None:
    tid = _make()
    with session_scope() as s, pytest.raises(Invalid):
        tape.update_tape(s, user_id=OWNER, tape_id=tid, w=size)


# ------------------------------------------------------------------ straighten


def test_straighten_resets_angle_to_zero() -> None:
    tid = _make(angle=37.0)
    with session_scope() as s:
        assert s.get(TapeMark, tid).angle == 37.0
    with session_scope() as s:
        tape.update_tape(s, user_id=OWNER, tape_id=tid, angle=0.0)
    with session_scope() as s:
        assert s.get(TapeMark, tid).angle == 0.0


def test_angle_normalises_into_0_360() -> None:
    tid = _make(angle=-90.0)
    with session_scope() as s:
        assert s.get(TapeMark, tid).angle == 270.0


def test_a_field_left_out_of_the_update_is_untouched() -> None:
    """The `...` sentinel's whole job: PATCHing `revealed` alone must not
    reset x/y/w/h/angle, the way a naive "None means unchanged" scheme would
    break the moment a caller legitimately wants angle=0."""
    tid = _make(angle=45.0)
    with session_scope() as s:
        tape.update_tape(s, user_id=OWNER, tape_id=tid, revealed=True)
    with session_scope() as s:
        row = s.get(TapeMark, tid)
    assert row.revealed is True
    assert row.angle == 45.0
    assert (row.x, row.y, row.w, row.h) == (RECT["x"], RECT["y"], RECT["w"], RECT["h"])


# ------------------------------------------------------------------------ http


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(tape_api.router)
    app.state.signed_in_as = OWNER

    def _override() -> Iterator[User]:
        with session_scope() as s:
            yield s.scalar(select(User).where(User.id == app.state.signed_in_as))

    app.dependency_overrides[current_user] = _override
    with TestClient(app) as c:
        yield c


def test_endpoint_hides_another_users_paper_behind_404(client: TestClient) -> None:
    r = client.get(f"/api/papers/{OTHER_PAPER}/tape")
    assert r.status_code == 404


def test_full_tape_lifecycle_over_http(client: TestClient) -> None:
    made = client.post(f"/api/papers/{OWNER_PAPER}/tape", json={"kind": "original", "page": 3, **RECT})
    assert made.status_code == 201
    row = made.json()
    assert row["revealed"] is False
    assert row["angle"] == 0.0

    listed = client.get(f"/api/papers/{OWNER_PAPER}/tape?kind=original")
    assert [t["id"] for t in listed.json()] == [row["id"]]

    revealed = client.patch(f"/api/tape/{row['id']}", json={"revealed": True})
    assert revealed.status_code == 200
    assert revealed.json()["revealed"] is True
    assert revealed.json()["w"] == RECT["w"]  # untouched by the reveal-only patch

    gone = client.delete(f"/api/tape/{row['id']}")
    assert gone.status_code == 204
    assert client.get(f"/api/papers/{OWNER_PAPER}/tape").json() == []


# ------------------------------------------------------------------ freehand


PATH = [{"x": 72.5, "y": 600.0}, {"x": 96.25, "y": 604.5}, {"x": 130.0, "y": 598.75}]


def test_a_straight_strip_stores_no_path() -> None:
    """NULL `points` IS the "this is a straight run" signal — a straight strip
    must not be given a two-point path that means the same thing twice."""
    tid = _make()
    with session_scope() as s:
        assert s.get(TapeMark, tid).points is None
        assert tape.load_path(s.get(TapeMark, tid).points) is None


def test_a_freehand_strip_round_trips_its_path() -> None:
    tid = _make(points=PATH)
    with session_scope() as s:
        path = tape.load_path(s.get(TapeMark, tid).points)
    assert path == [(p["x"], p["y"]) for p in PATH]


def test_freehand_strip_keeps_its_bounding_box_too() -> None:
    """The path does not replace (x, y, w, h): hit-testing and the popover
    anchor read the box without caring which kind of strip they have."""
    tid = _make(points=PATH)
    with session_scope() as s:
        row = s.get(TapeMark, tid)
    assert (row.x, row.y, row.w, row.h) == (RECT["x"], RECT["y"], RECT["w"], RECT["h"])


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("not a list", id="not-a-list"),
        pytest.param([], id="empty"),
        pytest.param([{"x": 1, "y": 2}], id="single-point"),
        pytest.param([{"x": 1, "y": 2}, {"x": 3}], id="missing-y"),
        pytest.param([{"x": 1, "y": 2}, {"x": "3", "y": 4}], id="string-x"),
        pytest.param([{"x": 1, "y": 2}, {"x": float("nan"), "y": 4}], id="nan-x"),
        pytest.param([{"x": 1, "y": 2, "p": 0.5}, {"x": 3, "y": 4}], id="extra-key"),
        pytest.param([{"x": 1, "y": 2}] * (tape.MAX_PATH_POINTS + 1), id="too-many"),
    ],
)
def test_hostile_paths_are_refused(path: object) -> None:
    with session_scope() as s, pytest.raises(Invalid):
        tape.create_tape(
            s, user_id=OWNER, paper_id=OWNER_PAPER, kind="original", page=1, **RECT, points=path
        )


def test_unreadable_path_falls_back_to_straight_not_fatal() -> None:
    """A hand-edited row must not take the whole page's tape down with it —
    the box is still there, so show it straight."""
    tid = _make(points=PATH)
    with session_scope() as s:
        s.get(TapeMark, tid).points = "{not json"
    with session_scope() as s:
        assert tape.load_path(s.get(TapeMark, tid).points) is None


def test_freehand_lifecycle_over_http(client: TestClient) -> None:
    made = client.post(
        f"/api/papers/{OWNER_PAPER}/tape",
        json={"kind": "original", "page": 2, **RECT, "points": PATH},
    )
    assert made.status_code == 201
    assert made.json()["points"] == PATH

    listed = client.get(f"/api/papers/{OWNER_PAPER}/tape?kind=original").json()
    assert listed[0]["points"] == PATH

    # A reveal-only patch must not disturb the path either.
    patched = client.patch(f"/api/tape/{made.json()['id']}", json={"revealed": True})
    assert patched.json()["revealed"] is True
    assert patched.json()["points"] == PATH


def test_straight_strip_reports_null_points_over_http(client: TestClient) -> None:
    made = client.post(f"/api/papers/{OWNER_PAPER}/tape", json={"kind": "original", "page": 2, **RECT})
    assert made.json()["points"] is None


def test_single_point_path_refused_at_the_edge(client: TestClient) -> None:
    r = client.post(
        f"/api/papers/{OWNER_PAPER}/tape",
        json={"kind": "original", "page": 1, **RECT, "points": [{"x": 1, "y": 2}]},
    )
    assert r.status_code == 422  # pydantic's min_length=2, before the service runs


def test_hostile_create_payload_refused_over_http(client: TestClient) -> None:
    r = client.post(
        f"/api/papers/{OWNER_PAPER}/tape",
        json={"kind": "original", "page": 1, "x": 0, "y": 0, "w": 0, "h": 10},
    )
    assert r.status_code == 422  # pydantic's own ge=MIN_SIZE catches it at the edge
