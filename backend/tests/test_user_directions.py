"""Per-user research directions: seeding, matching, relevance, and ownership.

Four families of test, defending four different things.

**Seeding.** ``ensure_seeded`` runs at the top of every entry point, so it fires
dozens of times per session and must be free of side effects after the first.
The case that actually matters is the *deliberate deletion*: a user who removes
every direction has made a choice, and handing the seven defaults back on their
next request would silently overrule it. That is what the ``seeded`` flag exists
for, and the flag is the kind of thing a later refactor "simplifies" into
``if not directions: seed()`` — which is exactly the bug.

**Parity.** Matching moved from a global table consulted at ingest time to a
per-user list consulted at query time. The *semantics* were supposed to survive
that move untouched: any-keyword substring match, most distinct hits wins, ties
break by declared order. So the parity tests run the new matcher over real
abstract text with the defaults loaded, and assert it agrees with the old global
:func:`~pharos.daily.directions.match_directions` term for term.

**Relevance.** It replaces a number the LLM produced against the old global
rubric. A replacement is only defensible if it is well-behaved, so the ordering
property is pinned directly: more distinct hits is never less relevant, and the
scale is bounded.

**Ownership.** Pharos is multi-user and these endpoints take ids in the path.
Every ``404`` assertion below is really an assertion that a probe cannot tell
"not yours" from "no such thing" and therefore cannot walk ids to learn what
other researchers read.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import directions as directions_api
from pharos.api.deps import current_user
from pharos.daily import user_directions
from pharos.daily.directions import ARXIV_CATEGORIES, DIRECTIONS, match_directions
from pharos.daily.user_directions import (
    Conflict,
    Direction,
    Invalid,
    match_for_user,
    relevance_for,
)
from pharos.db.models import User, UserDailyConfig, UserDirection
from pharos.db.session import init_engine, session_scope
from sqlalchemy import delete, select

#: Prefixed rather than named "owner"/"other" because ``init_engine`` memoises:
#: whichever test module runs first wins and every later one shares that
#: database. Ids scoped to this module cannot collide with another module's
#: fixture users and — more importantly — cannot be deleted by one.
OWNER = "directions-owner"
OTHER = "directions-other"
_USERS = (OWNER, OTHER)


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Ensure a database and this module's two users exist.

    The path is only honoured if this module is the first to call
    ``init_engine``; otherwise the call is a no-op returning the existing
    engine. So the insert is written "create if absent" rather than as a plain
    add, which would hit a unique violation on a second run against a shared
    database.
    """
    init_engine(tmp_path_factory.mktemp("db") / "pharos.db")
    with session_scope() as s:
        for uid in _USERS:
            if s.get(User, uid) is None:
                s.add(User(id=uid, email=f"{uid}@example.test", password_hash="x"))


@pytest.fixture(autouse=True)
def _clean() -> None:
    """Wipe *this module's* directions and configs between tests.

    Scoped to ``_USERS`` rather than truncating the tables, because the engine is
    shared with every other test module and deleting their rows out from under
    them would make this file's cleanup another file's failure.
    """
    with session_scope() as s:
        s.execute(delete(UserDirection).where(UserDirection.user_id.in_(_USERS)))
        s.execute(delete(UserDailyConfig).where(UserDailyConfig.user_id.in_(_USERS)))


def _names(user_id: str = OWNER) -> list[str]:
    with session_scope() as s:
        return [row.name for row in user_directions.list_directions(s, user_id=user_id)]


def _defaults() -> list[Direction]:
    """The global defaults as :class:`Direction` values, in declared order."""
    return [
        Direction(id=str(i), name=name, keywords=tuple(kws), enabled=True, position=i)
        for i, (name, kws) in enumerate(DIRECTIONS.items())
    ]


# ------------------------------------------------------------------- seeding


def test_first_touch_seeds_the_defaults() -> None:
    """A brand-new account opens 每日论文 onto a working list, not a blank form."""
    with session_scope() as s:
        rows = user_directions.list_directions(s, user_id=OWNER)
    assert [r.name for r in rows] == list(DIRECTIONS)
    assert [r.position for r in rows] == list(range(len(DIRECTIONS)))
    # Keywords survive the newline round trip intact, lower-cased.
    assert rows[0].keywords.splitlines() == list(DIRECTIONS[rows[0].name])


def test_seeding_sets_the_config_with_default_categories() -> None:
    with session_scope() as s:
        config = user_directions.get_config(s, user_id=OWNER)
        assert config.seeded is True
        assert user_directions.config_categories(config) == list(ARXIV_CATEGORIES)


def test_seeding_is_idempotent_across_many_calls() -> None:
    """``ensure_seeded`` runs at the top of every entry point, so "once" has to
    mean once no matter how often it is called."""
    for _ in range(5):
        with session_scope() as s:
            user_directions.ensure_seeded(s, user_id=OWNER)
    with session_scope() as s:
        count = len(list(s.scalars(select(UserDirection.id).where(UserDirection.user_id == OWNER))))
    assert count == len(DIRECTIONS)


def test_deleting_every_direction_is_not_undone_by_the_next_request() -> None:
    """The deliberate-deletion case, and the whole reason ``seeded`` is a column.

    An empty list is a choice, not an uninitialised state. A future refactor to
    ``if not directions: seed()`` passes every other test in this file and fails
    this one.
    """
    with session_scope() as s:
        for row in user_directions.list_directions(s, user_id=OWNER):
            user_directions.delete_direction(s, user_id=OWNER, direction_id=row.id)

    assert _names() == []
    # And again, because the bug would be on the *next* request, not this one.
    assert _names() == []
    with session_scope() as s:
        assert user_directions.get_config(s, user_id=OWNER).seeded is True


def test_seeding_does_not_duplicate_directions_that_already_exist() -> None:
    """A restored backup or a half-run migration leaves rows with ``seeded``
    false. Copying the defaults on top of them would double the list."""
    with session_scope() as s:
        s.add(UserDirection(user_id=OWNER, name="Mine", keywords="foo", position=0))
        s.add(UserDailyConfig(user_id=OWNER, categories="cs.RO", seeded=False))
    assert _names() == ["Mine"]
    with session_scope() as s:
        assert user_directions.get_config(s, user_id=OWNER).seeded is True


def test_seeding_one_user_does_not_touch_another() -> None:
    with session_scope() as s:
        user_directions.ensure_seeded(s, user_id=OWNER)
        assert s.get(UserDailyConfig, OTHER) is None


def test_losing_the_concurrent_first_request_race_recovers() -> None:
    """Two first requests from one account race; the loser must not blow up.

    The savepoint in ``ensure_seeded`` exists for exactly this, and it only
    works if the ``session.add`` happens INSIDE it. Entering ``begin_nested``
    autoflushes anything already pending, so an object added beforehand is
    inserted by the *outer* transaction, the savepoint covers nothing, and the
    loser gets a PendingRollbackError the handler cannot undo.

    The race is simulated by committing the row from a second session at the
    moment the first has looked and found nothing.
    """
    with session_scope() as s:
        real_get = s.get

        def racing_get(cls, pk, *args, **kwargs):  # type: ignore[no-untyped-def]
            found = real_get(cls, pk, *args, **kwargs)
            if cls is UserDailyConfig and found is None:
                with session_scope() as rival:
                    rival.add(UserDailyConfig(user_id=pk, categories="cs.RO", seeded=False))
            return found

        s.get = racing_get  # type: ignore[method-assign]
        config = user_directions.ensure_seeded(s, user_id=OTHER)

    # The rival's row is the one that survived, and we seeded on top of it.
    assert config.seeded is True
    assert _names(OTHER) == list(DIRECTIONS)


# -------------------------------------------------------- matching parity

#: Real arXiv abstract text, trimmed. Chosen because each one is a case the old
#: matcher had an opinion about: the first mentions "diffusion policy" while
#: being about world models throughout, the second hits several VLA terms at
#: once, and the third matches nothing at all.
WORLD_MODEL_PAPER = (
    "Learning Latent Dynamics for Long-Horizon Manipulation",
    "We train a world model over latent dynamics and compare against a "
    "diffusion policy baseline. Our neural simulator supports video prediction "
    "at 10Hz and outperforms dreamerv3 on long-horizon tasks.",
)
VLA_PAPER = (
    "OpenVLA-2: A Vision-Language-Action Model for Robotic Manipulation",
    "We present a vision-language-action model trained on robot policy data. "
    "Unlike RT-2, our VLA policy performs instruction-following manipulation "
    "with a language-conditioned policy head.",
)
UNRELATED_PAPER = (
    "Sheaf Cohomology of Toric Varieties",
    "We compute the cohomology of coherent sheaves on smooth toric varieties.",
)
_REAL_PAPERS = (WORLD_MODEL_PAPER, VLA_PAPER, UNRELATED_PAPER)


@pytest.mark.parametrize(("title", "abstract"), _REAL_PAPERS)
def test_matching_agrees_with_the_old_global_matcher(title: str, abstract: str) -> None:
    """Same direction, same hit terms, same order — on real abstract text.

    The per-user matcher is a port, not a rewrite. If this drifts, every user's
    feed silently reclassifies without anyone having edited anything.
    """
    assert match_for_user(_defaults(), title, abstract) == match_directions(f"{title}\n{abstract}")


#: Text that must NOT match anything, and that only fails to match while the
#: whitespace padding on the defaults survives. Every word here *contains*
#: "dit" or "wam" without being about diffusion or world-action models.
_PADDING_DECOYS = (
    "Credit Assignment in Reinforcement Learning",
    "An Audit of Edit Distance Metrics",
    "Additional Conditioning for Editing Images",
)


@pytest.mark.parametrize("title", _PADDING_DECOYS)
def test_seeded_defaults_keep_the_whitespace_padding(title: str) -> None:
    """The padded defaults must survive the trip through the database.

    ``"wam "`` and ``" dit "`` are padded on purpose: the space is what makes
    them match the acronym instead of every word that merely contains those
    letters. A ``.strip()`` anywhere on the seeding path — the obvious
    "normalise the input" reflex — silently turns " dit " into "dit", and then
    "credit", "audit", "edit" and "addition" all match Diffusion. Every new
    account's feed floods, and nothing else in the suite notices.

    This asserts through ``load_directions`` rather than over ``DIRECTIONS``
    directly, because the pure-function parity test above builds its Directions
    from the constant and so cannot see a seeding bug at all.
    """
    with session_scope() as s:
        loaded = user_directions.load_directions(s, user_id=OWNER)
    assert match_for_user(loaded, title, "") == match_directions(title) == (None, ())


def test_seeded_keywords_are_verbatim_copies_of_the_defaults() -> None:
    """Byte-for-byte, padding included, for every direction — not just the first."""
    with session_scope() as s:
        loaded = {d.name: d.keywords for d in user_directions.load_directions(s, user_id=OWNER)}
    assert loaded == {name: tuple(kws) for name, kws in DIRECTIONS.items()}


def test_a_paper_belongs_to_the_direction_it_is_mostly_about() -> None:
    """The rule the "most distinct hits wins" tie-break exists to express."""
    name, hits = match_for_user(_defaults(), *WORLD_MODEL_PAPER)
    assert name == "World Model"
    assert "diffusion policy" not in hits


def test_no_match_returns_none_and_no_hits() -> None:
    assert match_for_user(_defaults(), *UNRELATED_PAPER) == (None, ())


def test_ties_break_by_position_not_by_name() -> None:
    """One hit each, so only order decides — and order is user-editable."""
    early = Direction(id="a", name="Early", keywords=("alpha",), enabled=True, position=0)
    late = Direction(id="b", name="Late", keywords=("beta",), enabled=True, position=1)
    assert match_for_user([early, late], "alpha beta", "")[0] == "Early"
    # Reversed positions must reverse the answer, or the tie-break is really
    # just list order and the position column does nothing.
    assert match_for_user([late, early], "alpha beta", "")[0] == "Late"


def test_matching_is_case_insensitive_and_spans_title_and_abstract() -> None:
    d = [Direction(id="a", name="D", keywords=("gaussian splatting",), enabled=True, position=0)]
    assert match_for_user(d, "Fast GAUSSIAN Splatting", "")[0] == "D"
    assert match_for_user(d, "Fast Rendering", "We use gaussian splatting.")[0] == "D"
    assert match_for_user(d, "Fast Rendering", None)[0] is None


def test_matching_never_touches_the_database() -> None:
    """A pure function is the contract: this runs once per paper per request,
    and a query hiding in here would be a per-row round trip inside a listing."""
    with session_scope() as s:
        loaded = user_directions.load_directions(s, user_id=OWNER)
    # Session closed. If matching lazy-loaded anything this would raise
    # DetachedInstanceError instead of returning an answer.
    assert match_for_user(loaded, *VLA_PAPER)[0] == "VLA"


def test_disabled_directions_are_excluded_from_the_feed_but_not_the_editor() -> None:
    with session_scope() as s:
        target = next(
            r for r in user_directions.list_directions(s, user_id=OWNER) if r.name == "VLA"
        )
        user_directions.update_direction(
            s, user_id=OWNER, direction_id=target.id, changes={"enabled": False}
        )
    with session_scope() as s:
        assert "VLA" not in [d.name for d in user_directions.load_directions(s, user_id=OWNER)]
        assert "VLA" in [r.name for r in user_directions.list_directions(s, user_id=OWNER)]


# ------------------------------------------------------------------ relevance


def test_relevance_is_zero_only_when_nothing_matched() -> None:
    d = Direction(id="a", name="D", keywords=("x", "y"), enabled=True, position=0)
    assert relevance_for((), d) == 0.0
    assert relevance_for(("x",), d) > 0.0


def test_relevance_never_decreases_with_more_hits() -> None:
    """Monotonicity is the property that makes this a defensible replacement for
    the LLM's relevance: a strictly better match can never rank lower."""
    keywords = tuple(f"kw{i}" for i in range(12))
    d = Direction(id="a", name="D", keywords=keywords, enabled=True, position=0)
    scores = [relevance_for(keywords[:n], d) for n in range(1, len(keywords) + 1)]
    assert scores == sorted(scores)
    assert scores[0] < scores[-1]


def test_relevance_stays_inside_the_scale() -> None:
    keywords = tuple(f"kw{i}" for i in range(200))
    d = Direction(id="a", name="D", keywords=keywords, enabled=True, position=0)
    for n in (1, 5, 50, 200):
        assert 0.0 <= relevance_for(keywords[:n], d) <= 10.0


def test_repeated_hits_of_the_same_term_do_not_inflate_relevance() -> None:
    """Distinct terms, not occurrences — otherwise a keyword repeated in the
    abstract would outrank a genuinely broader match."""
    d = Direction(id="a", name="D", keywords=("x", "y"), enabled=True, position=0)
    assert relevance_for(("x", "x", "x"), d) == relevance_for(("x",), d)


def test_relevance_survives_a_direction_with_no_keywords() -> None:
    """Not reachable through the API — zero keywords is rejected — but this is a
    pure function over data, and dividing by the keyword count is right there."""
    empty = Direction(id="a", name="D", keywords=(), enabled=True, position=0)
    assert relevance_for(("x",), empty) >= 0.0
    assert relevance_for(("x",), None) >= 0.0


# ---------------------------------------------------------------------- CRUD


def test_create_appends_to_the_end() -> None:
    with session_scope() as s:
        row = user_directions.create_direction(
            s, user_id=OWNER, name="Neuro", keywords="spiking\nsnn"
        )
        assert row.position == len(DIRECTIONS)
        assert row.keywords == "spiking\nsnn"


def test_keywords_accept_commas_newlines_and_a_list() -> None:
    for raw in ("a, b\nc", ["a", "b", "c"], ["a,b", "c"]):
        assert user_directions.parse_keywords(raw) == ["a", "b", "c"]


def test_keywords_are_lowercased_trimmed_and_deduped_in_order() -> None:
    assert user_directions.parse_keywords("  VLA \n vla\nRT-2 ") == ["vla", "rt-2"]


def test_a_direction_with_no_keywords_is_rejected() -> None:
    """It would match nothing while looking, in the settings list, exactly like
    one that works — the worst kind of broken."""
    with pytest.raises(Invalid), session_scope() as s:
        user_directions.create_direction(s, user_id=OWNER, name="Empty", keywords="  ,\n , ")


def test_duplicate_names_are_rejected_case_insensitively() -> None:
    with pytest.raises(Conflict), session_scope() as s:
        user_directions.create_direction(s, user_id=OWNER, name="vla", keywords="x")


def test_reorder_rewrites_positions_and_changes_the_tie_break() -> None:
    with session_scope() as s:
        rows = user_directions.list_directions(s, user_id=OWNER)
        reversed_ids = [r.id for r in reversed(rows)]
        after = user_directions.reorder_directions(s, user_id=OWNER, direction_ids=reversed_ids)
        assert [r.name for r in after] == [r.name for r in reversed(rows)]
        assert [r.position for r in after] == list(range(len(rows)))


def test_reorder_accepts_a_partial_list_and_keeps_the_rest_in_order() -> None:
    with session_scope() as s:
        rows = user_directions.list_directions(s, user_id=OWNER)
        last = rows[-1]
        after = user_directions.reorder_directions(s, user_id=OWNER, direction_ids=[last.id])
        assert [r.name for r in after] == [last.name] + [r.name for r in rows[:-1]]


def test_reorder_rejects_duplicate_ids() -> None:
    with pytest.raises(Invalid), session_scope() as s:
        rows = user_directions.list_directions(s, user_id=OWNER)
        user_directions.reorder_directions(s, user_id=OWNER, direction_ids=[rows[0].id, rows[0].id])


def test_categories_are_validated_by_shape_not_by_allow_list() -> None:
    """A physicist is a user, not an attack."""
    assert user_directions.parse_categories("cond-mat.stat-mech, math.AP, hep-th, q-bio.NC") == [
        "cond-mat.stat-mech",
        "math.AP",
        "hep-th",
        "q-bio.NC",
    ]


def test_categories_are_canonicalised_so_the_shared_union_has_one_entry_each() -> None:
    assert user_directions.parse_categories("cs.ro, CS.RO, cs.RO") == ["cs.RO"]
    assert user_directions.parse_categories("physics.FLU-DYN") == ["physics.flu-dyn"]


@pytest.mark.parametrize(
    "bogus",
    [
        "cs.RO; DROP TABLE papers",
        "../../etc/passwd",
        "cs..RO",
        ".RO",
        "cs.",
        "http://evil.test/cs.RO",
        "cs.RO cs.CV extra.but.too.deep",
    ],
)
def test_bogus_categories_are_rejected(bogus: str) -> None:
    with pytest.raises(Invalid):
        user_directions.parse_categories(bogus)


def test_max_per_day_is_bounded() -> None:
    with session_scope() as s:
        for bad in (0, -1, 10_000):
            with pytest.raises(Invalid):
                user_directions.update_config(s, user_id=OWNER, changes={"max_per_day": bad})
        assert (
            user_directions.update_config(s, user_id=OWNER, changes={"max_per_day": 25}).max_per_day
            == 25
        )


# ------------------------------------------------------------- hostile input


def test_ten_thousand_keywords_are_refused() -> None:
    """Free text from a browser landing in a substring scan over every paper in
    a day. Unbounded, one account makes its own feed arbitrarily expensive."""
    with pytest.raises(Invalid):
        user_directions.parse_keywords("\n".join(f"kw{i}" for i in range(10_000)))


def test_a_one_megabyte_name_is_refused() -> None:
    with pytest.raises(Invalid), session_scope() as s:
        user_directions.create_direction(s, user_id=OWNER, name="x" * 1_000_000, keywords="a")


def test_a_single_enormous_keyword_is_refused() -> None:
    with pytest.raises(Invalid):
        user_directions.parse_keywords("x" * 100_000)


def test_many_short_keywords_are_refused_by_total_length() -> None:
    """The count cap alone is not enough: sixty 80-character terms is under the
    count and still 4800 characters scanned per paper."""
    with pytest.raises(Invalid):
        user_directions.parse_keywords("\n".join(f"{'x' * 79}{i:1d}" for i in range(60)))


def test_too_many_categories_are_refused() -> None:
    with pytest.raises(Invalid):
        user_directions.parse_categories([f"cs.A{i}" for i in range(100)])


def test_too_many_directions_are_refused() -> None:
    with session_scope() as s:
        for i in range(user_directions.MAX_DIRECTIONS - len(DIRECTIONS)):
            user_directions.create_direction(s, user_id=OWNER, name=f"D{i}", keywords="x")
        with pytest.raises(Invalid):
            user_directions.create_direction(s, user_id=OWNER, name="one more", keywords="x")


# ------------------------------------------------------------------- the API


@pytest.fixture
def client() -> Iterator[TestClient]:
    """The directions router with the signed-in user selectable per test.

    ``current_user`` is overridden rather than driven with a real JWT: token
    minting needs an instance secret and is covered thoroughly elsewhere, and
    what is under test here is the scoping, not the credential. The override is
    a *generator* yielding a live ORM object inside ``session_scope``, exactly as
    the real dependency does, so writes are committed on the way out and these
    are real round trips rather than assertions about an in-memory object.
    """
    app = FastAPI()
    app.include_router(directions_api.router)
    app.state.signed_in_as = OWNER

    def _override() -> Iterator[User]:
        with session_scope() as s:
            yield s.scalar(select(User).where(User.id == app.state.signed_in_as))

    app.dependency_overrides[current_user] = _override
    with TestClient(app) as c:
        yield c


def _create(client: TestClient, name: str = "Mine", keywords: str = "alpha\nbeta") -> str:
    response = client.post("/api/daily/directions", json={"name": name, "keywords": keywords})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_get_directions_returns_the_documented_shape(client: TestClient) -> None:
    body = client.get("/api/daily/directions").json()
    assert len(body) == len(DIRECTIONS)
    assert set(body[0]) == {"id", "name", "keywords", "enabled", "position", "created_at"}
    assert isinstance(body[0]["keywords"], list)


def test_get_config_returns_the_documented_shape(client: TestClient) -> None:
    body = client.get("/api/daily/config").json()
    assert set(body) == {"categories", "max_per_day", "enabled", "seeded", "updated_at"}
    assert body["categories"] == list(ARXIV_CATEGORIES)
    assert body["seeded"] is True


def test_patch_config_accepts_a_string_or_a_list(client: TestClient) -> None:
    assert client.patch("/api/daily/config", json={"categories": "cs.RO, math.AP"}).json()[
        "categories"
    ] == ["cs.RO", "math.AP"]
    assert client.patch("/api/daily/config", json={"categories": ["cs.CV"]}).json()[
        "categories"
    ] == ["cs.CV"]


def test_patch_config_rejects_a_bogus_category_with_400(client: TestClient) -> None:
    assert (
        client.patch("/api/daily/config", json={"categories": "not a category!"}).status_code == 400
    )


def test_empty_patch_is_a_400_not_a_silent_no_op(client: TestClient) -> None:
    assert client.patch("/api/daily/config", json={}).status_code == 400
    direction_id = _create(client)
    assert client.patch(f"/api/daily/directions/{direction_id}", json={}).status_code == 400


def test_unknown_field_is_rejected_rather_than_ignored(client: TestClient) -> None:
    """``extra="forbid"``: a typo'd field silently doing nothing is how a user
    concludes the settings page is broken."""
    assert client.patch("/api/daily/config", json={"catgories": "cs.RO"}).status_code == 422


def test_reorder_endpoint_returns_the_new_order(client: TestClient) -> None:
    ids = [d["id"] for d in client.get("/api/daily/directions").json()]
    body = client.post(
        "/api/daily/directions/reorder", json={"direction_ids": list(reversed(ids))}
    ).json()
    assert [d["id"] for d in body] == list(reversed(ids))
    assert [d["position"] for d in body] == list(range(len(ids)))


def test_delete_returns_204_and_removes_the_row(client: TestClient) -> None:
    direction_id = _create(client)
    assert client.delete(f"/api/daily/directions/{direction_id}").status_code == 204
    assert direction_id not in [d["id"] for d in client.get("/api/daily/directions").json()]


# ------------------------------------------------------------------ ownership


def test_each_user_sees_only_their_own_directions(client: TestClient) -> None:
    _create(client, name="Owner Only")
    client.app.state.signed_in_as = OTHER
    names = [d["name"] for d in client.get("/api/daily/directions").json()]
    assert "Owner Only" not in names
    assert names == list(DIRECTIONS)  # OTHER got their own fresh seed


def test_another_users_direction_is_404_on_every_endpoint(client: TestClient) -> None:
    """404, never 403. A 403 confirms the id is real and turns the endpoint into
    an oracle for enumerating what other researchers follow."""
    victim_id = _create(client, name="Private Interest")
    client.app.state.signed_in_as = OTHER
    for response in (
        client.patch(f"/api/daily/directions/{victim_id}", json={"name": "Stolen"}),
        client.delete(f"/api/daily/directions/{victim_id}"),
        client.post("/api/daily/directions/reorder", json={"direction_ids": [victim_id]}),
    ):
        assert response.status_code == 404, response.text

    client.app.state.signed_in_as = OWNER
    assert "Private Interest" in [d["name"] for d in client.get("/api/daily/directions").json()]


def test_one_users_config_does_not_leak_into_anothers(client: TestClient) -> None:
    client.patch("/api/daily/config", json={"categories": "hep-th", "max_per_day": 5})
    client.app.state.signed_in_as = OTHER
    body = client.get("/api/daily/config").json()
    assert body["categories"] == list(ARXIV_CATEGORIES)
    assert body["max_per_day"] != 5


def test_names_may_collide_across_users(client: TestClient) -> None:
    """Uniqueness is per user. Two researchers both following "VLA" is normal,
    and a global constraint would leak the other's list through a 409."""
    _create(client, name="Shared Name")
    client.app.state.signed_in_as = OTHER
    assert (
        client.post(
            "/api/daily/directions", json={"name": "Shared Name", "keywords": "x"}
        ).status_code
        == 201
    )


def test_hostile_body_is_refused_before_it_reaches_the_service(client: TestClient) -> None:
    """A megabyte of keywords is rejected by the request model, so it is never
    split, lower-cased and de-duplicated first."""
    response = client.post(
        "/api/daily/directions", json={"name": "Huge", "keywords": "x" * 1_000_000}
    )
    assert response.status_code == 422, response.text


# ------------------------------------------------------- mounting, in the real app


def test_directions_routes_win_over_the_date_route() -> None:
    """``GET /api/daily/{date}``'s ``pattern=`` is Pydantic validation, applied
    *after* routing has picked a handler — it does not narrow Starlette's path
    regex. So ``/api/daily/directions`` reaches the date handler and 422s unless
    this router is included first. Pinned here because the failure is a 422 on a
    settings page, which reads like a client bug rather than a mounting bug.
    """
    from pharos.api import daily as daily_api

    def _app(*routers: object) -> TestClient:
        app = FastAPI()
        for r in routers:
            app.include_router(r)  # type: ignore[arg-type]

        def _override() -> Iterator[User]:
            with session_scope() as s:
                yield s.scalar(select(User).where(User.id == OWNER))

        app.dependency_overrides[current_user] = _override
        return TestClient(app)

    # Driven over HTTP rather than by inspecting the route table: what is being
    # asserted is which handler actually answers, and modern FastAPI does not
    # flatten included routers into ``app.routes`` for us to read.
    with _app(directions_api.router, daily_api.router) as correct:
        assert correct.get("/api/daily/directions").status_code == 200
    with _app(daily_api.router, directions_api.router) as wrong:
        assert wrong.get("/api/daily/directions").status_code == 422
