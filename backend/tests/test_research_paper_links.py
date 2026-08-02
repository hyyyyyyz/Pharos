"""Linking a project's literature sources to the library papers they actually are.

The link is what upgrades a project's evidence from an abstract to a page, so
the tests that matter here are about *identity*: only an identifier may create
one automatically, only the owner may create one by hand, and the states a
client can tell apart have to be the states the schema can actually represent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import projects as projects_api
from pharos.api.deps import current_user
from pharos.api.research_schemas import source_out
from pharos.db.models import (
    LiteratureResult,
    LiteratureSearch,
    Paper,
    ProjectArtifact,
    ProjectSource,
    ResearchProject,
    User,
)
from pharos.db.session import init_engine, session_scope
from pharos.services import projects
from sqlalchemy import delete, select

OWNER = "paper-link-owner"
OTHER = "paper-link-other"
USERS = (OWNER, OTHER)


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    init_engine(tmp_path_factory.mktemp("paper-links") / "pharos.db")
    with session_scope() as session:
        for user_id in USERS:
            if session.get(User, user_id) is None:
                session.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="x"))


@pytest.fixture(autouse=True)
def _clean() -> None:
    with session_scope() as session:
        session.execute(delete(ProjectArtifact).where(ProjectArtifact.user_id.in_(USERS)))
        session.execute(delete(ProjectSource).where(ProjectSource.user_id.in_(USERS)))
        session.execute(delete(LiteratureResult).where(LiteratureResult.user_id.in_(USERS)))
        session.execute(delete(LiteratureSearch).where(LiteratureSearch.user_id.in_(USERS)))
        session.execute(delete(ResearchProject).where(ResearchProject.user_id.in_(USERS)))
        session.execute(delete(Paper).where(Paper.user_id.in_(USERS)))


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(projects_api.router)
    app.dependency_overrides[current_user] = lambda: User(id=OWNER, email="x", password_hash="x")
    return TestClient(app)


# ------------------------------------------------------------------ fixtures


def _project(user_id: str = OWNER) -> str:
    with session_scope() as session:
        return projects.create_project(session, user_id=user_id, name="Evidence Ledger").id


def _result(
    *,
    user_id: str = OWNER,
    title: str = "Evidence-Grounded Robot Learning",
    doi: str | None = None,
    arxiv_id: str | None = "2607.00001",
) -> str:
    with session_scope() as session:
        search = LiteratureSearch(
            user_id=user_id,
            query="robot learning",
            sources='["arxiv"]',
            status="complete",
            result_count=1,
            errors="{}",
        )
        result = LiteratureResult(
            user_id=user_id,
            dedup_key=f"title:{title.lower().replace(' ', '')}",
            title=title,
            authors="[]",
            abstract="We propose a method.",
            doi=doi,
            sources='["arxiv"]',
            source_ids=projects.dump_json({"arxiv": arxiv_id} if arxiv_id else {}),
            rank=1,
        )
        search.results.append(result)
        session.add(search)
        session.flush()
        return result.id


def _paper(
    *,
    user_id: str = OWNER,
    paper_id: str | None = None,
    title: str = "Evidence-Grounded Robot Learning",
    doi: str | None = None,
    arxiv_id: str | None = None,
    trashed: bool = False,
) -> str:
    paper_id = paper_id or f"pp-{user_id[:4]}-{arxiv_id or doi or title}"[:32]
    with session_scope() as session:
        session.add(
            Paper(
                id=paper_id,
                user_id=user_id,
                title=title,
                orig_sha256=f"sha-{paper_id}",
                orig_filename=f"{paper_id}.pdf",
                page_count=12,
                doi=doi,
                arxiv_id=arxiv_id,
                deleted_at=datetime.now(UTC) if trashed else None,
            )
        )
    return paper_id


def _add(project_id: str, result_id: str, user_id: str = OWNER) -> str:
    with session_scope() as session:
        return projects.add_source(
            session, user_id=user_id, project_id=project_id, result_id=result_id
        ).id


def _linked_paper_id(source_id: str) -> str | None:
    with session_scope() as session:
        return session.scalar(select(ProjectSource.paper_id).where(ProjectSource.id == source_id))


# ----------------------------------------------------- owner scoping on link


def test_linking_to_another_users_paper_is_not_found_and_writes_nothing() -> None:
    """The highest-risk operation here: a client-supplied id from the papers
    table being attached to a row in the project-sources table. Another user's
    paper must be indistinguishable from one that does not exist, and the
    refusal must leave the source exactly as it was."""
    project = _project()
    source = _add(project, _result())
    theirs = _paper(user_id=OTHER, paper_id="pp-theirs", arxiv_id="2607.99999")

    with session_scope() as session, pytest.raises(projects.NotFound):
        projects.link_source_paper(
            session,
            user_id=OWNER,
            project_id=project,
            source_id=source,
            paper_id=theirs,
        )

    assert _linked_paper_id(source) is None


def test_a_missing_paper_and_another_users_paper_answer_identically() -> None:
    """If the two differed, the API would confirm which ids name a real paper
    and a probe could enumerate someone else's library one guess at a time."""
    project = _project()
    source = _add(project, _result())
    theirs = _paper(user_id=OTHER, paper_id="pp-theirs", arxiv_id="2607.99999")

    messages = []
    for paper_id in (theirs, "pp-does-not-exist"):
        with session_scope() as session:
            with pytest.raises(projects.NotFound) as caught:
                projects.link_source_paper(
                    session,
                    user_id=OWNER,
                    project_id=project,
                    source_id=source,
                    paper_id=paper_id,
                )
            messages.append(str(caught.value))
    assert messages[0] == messages[1]


def test_linking_a_trashed_paper_is_not_found() -> None:
    project = _project()
    source = _add(project, _result())
    trashed = _paper(paper_id="pp-trashed", arxiv_id="2607.00002", trashed=True)

    with session_scope() as session, pytest.raises(projects.NotFound):
        projects.link_source_paper(
            session, user_id=OWNER, project_id=project, source_id=source, paper_id=trashed
        )


def test_linking_a_source_in_another_users_project_is_not_found() -> None:
    theirs_project = _project(OTHER)
    theirs_source = _add(theirs_project, _result(user_id=OTHER), user_id=OTHER)
    mine = _paper(paper_id="pp-mine", arxiv_id="2607.00003")

    with session_scope() as session, pytest.raises(projects.NotFound):
        projects.link_source_paper(
            session,
            user_id=OWNER,
            project_id=theirs_project,
            source_id=theirs_source,
            paper_id=mine,
        )


def test_paper_id_must_be_a_string() -> None:
    project = _project()
    source = _add(project, _result())
    with session_scope() as session, pytest.raises(projects.Invalid):
        projects.link_source_paper(
            session, user_id=OWNER, project_id=project, source_id=source, paper_id=17
        )


# ---------------------------------------------------------- link and unlink


def test_link_then_relink_then_unlink() -> None:
    project = _project()
    source = _add(project, _result())
    first = _paper(paper_id="pp-first", arxiv_id="2607.10001")
    second = _paper(paper_id="pp-second", arxiv_id="2607.10002")

    with session_scope() as session:
        row = projects.link_source_paper(
            session, user_id=OWNER, project_id=project, source_id=source, paper_id=first
        )
        assert row.paper_id == first
    # A user correcting a wrong match is the reason re-linking is allowed.
    with session_scope() as session:
        projects.link_source_paper(
            session, user_id=OWNER, project_id=project, source_id=source, paper_id=second
        )
    assert _linked_paper_id(source) == second

    with session_scope() as session:
        projects.unlink_source_paper(
            session, user_id=OWNER, project_id=project, source_id=source
        )
    assert _linked_paper_id(source) is None
    # Idempotent: the caller asked for a state, not a transition.
    with session_scope() as session:
        projects.unlink_source_paper(
            session, user_id=OWNER, project_id=project, source_id=source
        )
    assert _linked_paper_id(source) is None


def test_linking_leaves_the_note_and_the_result_untouched() -> None:
    project = _project()
    result = _result()
    with session_scope() as session:
        source = projects.add_source(
            session,
            user_id=OWNER,
            project_id=project,
            result_id=result,
            note="Cited for the retrieval ablation.",
        ).id
    paper = _paper(paper_id="pp-note", arxiv_id="2607.10003")

    with session_scope() as session:
        row = projects.link_source_paper(
            session, user_id=OWNER, project_id=project, source_id=source, paper_id=paper
        )
        assert row.note == "Cited for the retrieval ablation."
        assert row.result_id == result


# --------------------------------------------------------- automatic linking


def test_add_source_links_on_a_shared_arxiv_id() -> None:
    project = _project()
    paper = _paper(paper_id="pp-arxiv", arxiv_id="2607.00001")
    source = _add(project, _result(arxiv_id="2607.00001"))
    assert _linked_paper_id(source) == paper


def test_add_source_links_across_an_arxiv_version_suffix() -> None:
    """``2301.12345`` and ``2301.12345v2`` are the same paper."""
    project = _project()
    paper = _paper(paper_id="pp-versioned", arxiv_id="2301.12345v2")
    source = _add(project, _result(arxiv_id="2301.12345"))
    assert _linked_paper_id(source) == paper


def test_add_source_links_across_doi_case() -> None:
    """A discovery hit's DOI is always lower-cased; a library paper's may not be
    — CrossRef enrichment and a manual correction both keep what they were
    given. An exact comparison would miss exactly the hand-corrected papers."""
    project = _project()
    paper = _paper(paper_id="pp-doi", doi="10.1145/ABC.2026.42")
    source = _add(project, _result(doi="10.1145/abc.2026.42", arxiv_id=None))
    assert _linked_paper_id(source) == paper


def test_a_legacy_arxiv_id_matches_regardless_of_subject_class_case() -> None:
    project = _project()
    paper = _paper(paper_id="pp-legacy", arxiv_id="math.GT/0309136")
    source = _add(project, _result(arxiv_id="math.gt/0309136"))
    assert _linked_paper_id(source) == paper


def test_a_shared_title_alone_never_links() -> None:
    """A title is not an identity. Two versions of one paper share a title and
    have different page numbers, which is the thing this link exists to get
    right."""
    project = _project()
    _paper(paper_id="pp-title", title="Evidence-Grounded Robot Learning")
    source = _add(project, _result(title="Evidence-Grounded Robot Learning", arxiv_id=None))
    assert _linked_paper_id(source) is None


def test_two_library_papers_with_one_doi_are_ambiguous_and_do_not_link() -> None:
    """The preprint and the version of record carry the same DOI and different
    pages; the library does not say which one the source is."""
    project = _project()
    _paper(paper_id="pp-preprint", doi="10.1145/abc.2026.42")
    _paper(paper_id="pp-published", doi="10.1145/abc.2026.42")
    source = _add(project, _result(doi="10.1145/abc.2026.42", arxiv_id=None))
    assert _linked_paper_id(source) is None


def test_identifiers_that_disagree_do_not_link() -> None:
    project = _project()
    _paper(paper_id="pp-by-doi", doi="10.1145/abc.2026.42")
    _paper(paper_id="pp-by-arxiv", arxiv_id="2607.00001")
    source = _add(project, _result(doi="10.1145/abc.2026.42", arxiv_id="2607.00001"))
    assert _linked_paper_id(source) is None


def test_autolink_never_reaches_another_users_library() -> None:
    project = _project()
    _paper(user_id=OTHER, paper_id="pp-theirs", arxiv_id="2607.00001")
    source = _add(project, _result(arxiv_id="2607.00001"))
    assert _linked_paper_id(source) is None


def test_autolink_ignores_a_trashed_paper() -> None:
    project = _project()
    _paper(paper_id="pp-binned", arxiv_id="2607.00001", trashed=True)
    source = _add(project, _result(arxiv_id="2607.00001"))
    assert _linked_paper_id(source) is None


def test_an_openalex_work_id_is_not_a_paper_identity() -> None:
    """``source_ids`` holds one id per provider; only arXiv's names the paper."""
    project = _project()
    _paper(paper_id="pp-oa", arxiv_id="W2741809807")
    result = _result(arxiv_id=None)
    with session_scope() as session:
        row = session.get(LiteratureResult, result)
        assert row is not None
        row.source_ids = projects.dump_json({"openalex": "W2741809807"})
    source = _add(project, result)
    assert _linked_paper_id(source) is None


def test_re_adding_a_source_retries_the_match() -> None:
    """The PDF usually arrives after the source was saved."""
    project = _project()
    result = _result(arxiv_id="2607.00001")
    source = _add(project, result)
    assert _linked_paper_id(source) is None

    paper = _paper(paper_id="pp-later", arxiv_id="2607.00001")
    assert _add(project, result) == source
    assert _linked_paper_id(source) == paper


# ------------------------------------------------------------- backfill pass


def test_autolink_project_sources_backfills_and_reports_only_what_changed() -> None:
    project = _project()
    matched = _add(project, _result(arxiv_id="2607.00001"))
    unmatched = _add(project, _result(title="Something Else", arxiv_id="2607.55555"))
    paper = _paper(paper_id="pp-backfill", arxiv_id="2607.00001")

    with session_scope() as session:
        changed = projects.autolink_project_sources(
            session, user_id=OWNER, project_id=project
        )
        assert [row.id for row in changed] == [matched]
    assert _linked_paper_id(matched) == paper
    assert _linked_paper_id(unmatched) is None

    # Nothing new to do the second time round.
    with session_scope() as session:
        assert projects.autolink_project_sources(session, user_id=OWNER, project_id=project) == []


def test_autolink_never_overwrites_a_hand_made_link() -> None:
    project = _project()
    source = _add(project, _result(arxiv_id="2607.00001"))
    _paper(paper_id="pp-would-match", arxiv_id="2607.00001")
    chosen = _paper(paper_id="pp-chosen", arxiv_id="2607.77777")

    with session_scope() as session:
        projects.link_source_paper(
            session, user_id=OWNER, project_id=project, source_id=source, paper_id=chosen
        )
    with session_scope() as session:
        assert projects.autolink_project_sources(session, user_id=OWNER, project_id=project) == []
    assert _linked_paper_id(source) == chosen


def test_autolink_on_another_users_project_is_not_found() -> None:
    theirs = _project(OTHER)
    with session_scope() as session, pytest.raises(projects.NotFound):
        projects.autolink_project_sources(session, user_id=OWNER, project_id=theirs)


def test_autolink_leaves_another_users_sources_alone() -> None:
    """Two projects, two owners, one shared arXiv id in only one library."""
    mine = _project()
    theirs = _project(OTHER)
    my_source = _add(mine, _result(arxiv_id="2607.00001"))
    their_source = _add(theirs, _result(user_id=OTHER, arxiv_id="2607.00001"), user_id=OTHER)
    paper = _paper(paper_id="pp-only-mine", arxiv_id="2607.00001")

    with session_scope() as session:
        projects.autolink_project_sources(session, user_id=OWNER, project_id=mine)
    assert _linked_paper_id(my_source) == paper
    assert _linked_paper_id(their_source) is None


# ------------------------------------------------------------- the wire shape


def test_the_three_link_states_a_client_can_distinguish() -> None:
    project = _project()
    source = _add(project, _result())
    paper = _paper(paper_id="pp-states", title="A Real PDF", arxiv_id="2607.31337")

    with session_scope() as session:
        row = projects.require_source(
            session, user_id=OWNER, project_id=project, source_id=source
        )
        # 1. Not linked: abstract-only, nothing to cite a page from.
        assert source_out(row).paper_id is None
        assert source_out(row).paper is None

        # 2. Linked to a paper the user has.
        projects.link_source_paper(
            session, user_id=OWNER, project_id=project, source_id=source, paper_id=paper
        )
        out = source_out(row)
        assert out.paper_id == paper
        assert out.paper is not None
        assert out.paper.title == "A Real PDF"
        assert out.paper.page_count == 12
        assert out.paper.deleted_at is None

    # 3. Linked, but the paper has been trashed. The link survives, and
    #    ``deleted_at`` is what tells the client the anchoring is at risk.
    with session_scope() as session:
        trashed = session.get(Paper, paper)
        assert trashed is not None
        trashed.deleted_at = datetime.now(UTC)
    with session_scope() as session:
        row = projects.require_source(
            session, user_id=OWNER, project_id=project, source_id=source
        )
        out = source_out(row)
        assert out.paper_id == paper
        assert out.paper is not None
        assert out.paper.deleted_at is not None


def test_purging_the_paper_collapses_the_link_into_not_linked() -> None:
    """ON DELETE SET NULL keeps no tombstone, so "never linked" and "linked to a
    paper that has since been permanently deleted" are the same wire value.
    This is a limit of the schema, pinned here so it is a known one."""
    project = _project()
    source = _add(project, _result())
    paper = _paper(paper_id="pp-doomed", arxiv_id="2607.31338")
    with session_scope() as session:
        projects.link_source_paper(
            session, user_id=OWNER, project_id=project, source_id=source, paper_id=paper
        )

    with session_scope() as session:
        session.delete(session.get(Paper, paper))

    assert _linked_paper_id(source) is None
    with session_scope() as session:
        row = projects.require_source(
            session, user_id=OWNER, project_id=project, source_id=source
        )
        # The source and its note survive; only the page anchoring is lost.
        assert source_out(row).paper_id is None
        assert row.result_id is not None


def test_project_output_carries_every_sources_link() -> None:
    project = _project()
    paper = _paper(paper_id="pp-in-project", arxiv_id="2607.00001")
    linked = _add(project, _result(arxiv_id="2607.00001"))
    _add(project, _result(title="Unlinked Work", arxiv_id="2607.44444"))

    with session_scope() as session:
        from pharos.api.research_schemas import project_out

        out = project_out(projects.require_project(session, project, user_id=OWNER))
    by_id = {row.id: row for row in out.sources}
    assert by_id[linked].paper_id == paper
    assert by_id[linked].paper is not None
    assert sum(1 for row in out.sources if row.paper_id is None) == 1


# ------------------------------------------------------------- HTTP contract


def test_http_link_unlink_round_trip(client: TestClient) -> None:
    project = _project()
    source = _add(project, _result())
    paper = _paper(paper_id="pp-http", title="A Real PDF", arxiv_id="2607.20001")

    response = client.put(
        f"/api/projects/{project}/sources/{source}/paper", json={"paper_id": paper}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["paper_id"] == paper
    assert body["paper"]["title"] == "A Real PDF"
    assert body["paper"]["deleted_at"] is None

    response = client.delete(f"/api/projects/{project}/sources/{source}/paper")
    assert response.status_code == 200
    assert response.json()["paper_id"] is None
    assert response.json()["paper"] is None


def test_http_link_to_another_users_paper_is_404(client: TestClient) -> None:
    project = _project()
    source = _add(project, _result())
    theirs = _paper(user_id=OTHER, paper_id="pp-http-theirs", arxiv_id="2607.20002")

    response = client.put(
        f"/api/projects/{project}/sources/{source}/paper", json={"paper_id": theirs}
    )
    assert response.status_code == 404
    assert _linked_paper_id(source) is None


def test_http_autolink_returns_only_the_sources_it_changed(client: TestClient) -> None:
    project = _project()
    matched = _add(project, _result(arxiv_id="2607.00001"))
    _add(project, _result(title="Unmatched", arxiv_id="2607.20003"))
    paper = _paper(paper_id="pp-http-auto", arxiv_id="2607.00001")

    response = client.post(f"/api/projects/{project}/sources/autolink")
    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body] == [matched]
    assert body[0]["paper_id"] == paper


def test_http_autolink_on_an_unknown_project_is_404(client: TestClient) -> None:
    assert client.post("/api/projects/no-such-project/sources/autolink").status_code == 404


def test_http_link_rejects_an_unknown_field(client: TestClient) -> None:
    project = _project()
    source = _add(project, _result())
    response = client.put(
        f"/api/projects/{project}/sources/{source}/paper",
        json={"paper_id": "pp-x", "note": "sneaky"},
    )
    assert response.status_code == 422
