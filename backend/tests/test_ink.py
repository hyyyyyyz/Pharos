"""Ink strokes: ownership, geometry, and the eraser's delete path.

Same two test families as ``test_annotate.py``, defending the same two things.

**Ownership.** Handwriting is the most personal annotation Pharos stores, and
every id arrives in a path — a paper id on the list/create pair, a stroke id on
the delete. Each must fail *identically* to a row that does not exist, so a
probe cannot tell "not yours" from "no such thing".

**Geometry.** ``points`` is free-form JSON from a browser and later read back
to paint under the user's pen. A ``NaN``, a four-hundred-thousand-sample
stroke, or a string where a number belongs must fail at the door: one bad row
in the column would make ``JSON.parse`` throw in the browser and cost the user
*every* stroke on the paper, not just the bad one.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import ink as ink_api
from pharos.api.deps import current_user
from pharos.db.models import InkStroke, Paper, User
from pharos.db.session import init_engine, session_scope
from pharos.services import ink
from pharos.services.annotate import Invalid, NotFound
from sqlalchemy import delete, select

#: Prefixed ids, for the same shared-engine reason as every other module: the
#: first ``init_engine`` wins, so fixtures are "create if absent" and cleanup
#: touches only this module's rows.
OWNER = "ink-owner"
OTHER = "ink-other"
_USERS = (OWNER, OTHER)

OWNER_PAPER = "ink-paper-owner"
OTHER_PAPER = "ink-paper-other"

#: A short stroke, in PDF points, bottom-left origin, with pressure. Fractional
#: on purpose — integers would let a rounding bug pass unnoticed.
POINTS = [
    {"x": 72.5, "y": 640.25, "p": 0.2},
    {"x": 90.12, "y": 620.5, "p": 0.65},
    {"x": 130.0, "y": 600.0, "p": 0.3},
]


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
    """Wipe *this module's* strokes between tests, leaving other modules' alone."""
    with session_scope() as s:
        s.execute(delete(InkStroke).where(InkStroke.user_id.in_(_USERS)))


def _make(
    *,
    user_id: str = OWNER,
    paper_id: str = OWNER_PAPER,
    kind: str = "original",
    page: int = 3,
    points: object = None,
    **kwargs: object,
) -> str:
    with session_scope() as s:
        return ink.create_stroke(
            s,
            user_id=user_id,
            paper_id=paper_id,
            kind=kind,
            page=page,
            points=POINTS if points is None else points,
            **kwargs,
        ).id


# ------------------------------------------------------------------ ownership


def test_cannot_list_strokes_on_another_users_paper() -> None:
    with session_scope() as s, pytest.raises(NotFound):
        ink.list_strokes(s, user_id=OWNER, paper_id=OTHER_PAPER)


def test_cannot_create_a_stroke_on_another_users_paper() -> None:
    with session_scope() as s, pytest.raises(NotFound):
        ink.create_stroke(
            s, user_id=OWNER, paper_id=OTHER_PAPER, kind="original", page=1, points=POINTS
        )
    with session_scope() as s:
        assert s.scalar(select(InkStroke).where(InkStroke.paper_id == OTHER_PAPER)) is None


def test_cannot_delete_another_users_stroke() -> None:
    theirs = _make(user_id=OTHER, paper_id=OTHER_PAPER)
    with session_scope() as s, pytest.raises(NotFound):
        ink.delete_stroke(s, user_id=OWNER, stroke_id=theirs)
    with session_scope() as s:
        assert s.get(InkStroke, theirs) is not None


# ------------------------------------------------------------------- geometry


def test_points_round_trip_exactly() -> None:
    """What the pen drew is what the reader gets back, to the stored precision."""
    sid = _make()
    with session_scope() as s:
        pts = ink.load_points(s.get(InkStroke, sid).points)
    assert [{"x": p.x, "y": p.y, "p": p.p} for p in pts] == POINTS


def test_pressure_defaults_like_a_pressureless_pointer() -> None:
    """A stroke from a mouse carries no pressure; 0.5 is what every pointer
    backend reports for that case, and it must not be read as 0 (invisible)."""
    sid = _make(points=[{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}])
    with session_scope() as s:
        pts = ink.load_points(s.get(InkStroke, sid).points)
    assert [p.p for p in pts] == [0.5, 0.5]


@pytest.mark.parametrize(
    "points",
    [
        pytest.param("not a list", id="not-a-list"),
        pytest.param([], id="empty"),
        pytest.param([{"x": 1, "y": 2, "p": 0.5, "z": 3}], id="extra-key"),
        pytest.param([{"x": 1}], id="missing-y"),
        pytest.param([{"x": "1", "y": 2}], id="string-x"),
        pytest.param([{"x": True, "y": 2}], id="bool-x"),
        pytest.param([{"x": float("nan"), "y": 2}], id="nan-x"),
        pytest.param([{"x": 1, "y": 2, "p": 1.5}], id="pressure-out-of-range"),
        pytest.param([{"x": 1, "y": 2}] * (ink.MAX_POINTS + 1), id="too-many-points"),
        pytest.param([{"x": 10**6, "y": 2}], id="coordinate-out-of-range"),
    ],
)
def test_hostile_point_payloads_are_refused(points: object) -> None:
    """One bad sample rejects the whole stroke — never a silently truncated one."""
    with session_scope() as s, pytest.raises(Invalid):
        ink.create_stroke(
            s, user_id=OWNER, paper_id=OWNER_PAPER, kind="original", page=1, points=points
        )


@pytest.mark.parametrize("width", [0, -1, 99])
def test_hostile_widths_are_refused(width: float) -> None:
    with session_scope() as s, pytest.raises(Invalid):
        ink.create_stroke(
            s,
            user_id=OWNER,
            paper_id=OWNER_PAPER,
            kind="original",
            page=1,
            points=POINTS,
            width=width,
        )


def test_color_is_a_closed_token_set() -> None:
    """A hex value would put an unthemable colour in the database."""
    with session_scope() as s, pytest.raises(Invalid):
        ink.create_stroke(
            s,
            user_id=OWNER,
            paper_id=OWNER_PAPER,
            kind="original",
            page=1,
            points=POINTS,
            color="#ff0000",
        )


def test_unreadable_stroke_is_filtered_not_fatal() -> None:
    """A hand-edited row must not take down the reader's whole ink list."""
    sid = _make()
    with session_scope() as s:
        s.get(InkStroke, sid).points = "{not json"
    with session_scope() as s:
        assert ink.list_strokes(s, user_id=OWNER, paper_id=OWNER_PAPER) == []


def test_kind_filter_returns_only_that_rendition() -> None:
    """Ink on the bilingual build has no position on the original's pages."""
    _make(kind="original")
    _make(kind="dual")
    with session_scope() as s:
        rows = ink.list_strokes(s, user_id=OWNER, paper_id=OWNER_PAPER, kind="dual")
    assert [r.kind for r in rows] == ["dual"]


# ------------------------------------------------------------------ endpoints


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """The ink router with the signed-in user selectable per test.

    ``current_user`` is overridden exactly as in ``test_annotate.py``: what is
    under test is the scoping, not the credential, and the override is a
    generator yielding a live ORM object so writes really commit.
    """
    app = FastAPI()
    app.include_router(ink_api.router)
    app.state.signed_in_as = OWNER

    def _override() -> Iterator[User]:
        with session_scope() as s:
            yield s.scalar(select(User).where(User.id == app.state.signed_in_as))

    app.dependency_overrides[current_user] = _override
    with TestClient(app) as c:
        yield c


def test_endpoint_hides_another_users_paper_behind_404(client: TestClient) -> None:
    r = client.get(f"/api/papers/{OTHER_PAPER}/ink")
    assert r.status_code == 404


def test_full_stroke_lifecycle_over_http(client: TestClient) -> None:
    made = client.post(
        f"/api/papers/{OWNER_PAPER}/ink",
        json={"kind": "original", "page": 3, "points": POINTS, "color": "blue", "width": 2.5},
    )
    assert made.status_code == 201
    row = made.json()
    assert row["points"] == POINTS
    assert row["color"] == "blue"
    assert row["width"] == 2.5

    listed = client.get(f"/api/papers/{OWNER_PAPER}/ink?kind=original")
    assert [s["id"] for s in listed.json()] == [row["id"]]

    gone = client.delete(f"/api/ink/{row['id']}")
    assert gone.status_code == 204
    assert client.get(f"/api/papers/{OWNER_PAPER}/ink").json() == []


def test_malformed_points_are_refused_over_http(client: TestClient) -> None:
    r = client.post(
        f"/api/papers/{OWNER_PAPER}/ink",
        json={"kind": "original", "page": 1, "points": [{"x": "left"}]},
    )
    assert r.status_code == 422  # pydantic, at the edge, before the service runs


def test_oversized_stroke_is_refused_at_the_edge(client: TestClient) -> None:
    """The payload bound is enforced twice on purpose: pydantic refuses an
    oversized list before FastAPI builds thousands of model instances (422),
    and the service's ceiling backs the edge up for any caller that skips it
    (400, pinned by the direct-service tests above)."""
    r = client.post(
        f"/api/papers/{OWNER_PAPER}/ink",
        json={
            "kind": "original",
            "page": 1,
            "points": [{"x": 1, "y": 2}] * (ink.MAX_POINTS + 1),
        },
    )
    assert r.status_code == 422
