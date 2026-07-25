"""Owner isolation and durable project/source/artifact workflow semantics."""

from __future__ import annotations

import pytest
from pharos.db.models import (
    LiteratureResult,
    LiteratureSearch,
    ProjectArtifact,
    ProjectSource,
    ResearchProject,
    User,
)
from pharos.db.session import init_engine, session_scope
from pharos.services import projects
from sqlalchemy import delete, select

OWNER = "research-project-owner"
OTHER = "research-project-other"
USERS = (OWNER, OTHER)


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    init_engine(tmp_path_factory.mktemp("research-projects") / "pharos.db")
    with session_scope() as session:
        for user_id in USERS:
            if session.get(User, user_id) is None:
                session.add(
                    User(
                        id=user_id,
                        email=f"{user_id}@example.test",
                        password_hash="x",
                    )
                )


@pytest.fixture(autouse=True)
def _clean() -> None:
    with session_scope() as session:
        session.execute(delete(ProjectArtifact).where(ProjectArtifact.user_id.in_(USERS)))
        session.execute(delete(ProjectSource).where(ProjectSource.user_id.in_(USERS)))
        session.execute(delete(LiteratureResult).where(LiteratureResult.user_id.in_(USERS)))
        session.execute(delete(LiteratureSearch).where(LiteratureSearch.user_id.in_(USERS)))
        session.execute(delete(ResearchProject).where(ResearchProject.user_id.in_(USERS)))


def _project(user_id: str = OWNER, name: str = "Robot Foundation Models") -> str:
    with session_scope() as session:
        return projects.create_project(session, user_id=user_id, name=name).id


def _result(
    user_id: str = OWNER, title: str = "A Paper", project_id: str | None = None
) -> str:
    with session_scope() as session:
        search = LiteratureSearch(
            user_id=user_id,
            project_id=project_id,
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
            abstract="We propose a method. Experiments improve success by 5%.",
            sources='["arxiv"]',
            source_ids='{"arxiv":"2607.00001"}',
            rank=1,
            analysis_mode="rules",
            analysis_warning="heuristic",
        )
        search.results.append(result)
        session.add(search)
        session.flush()
        return result.id


def test_project_queries_hide_another_users_project_as_not_found() -> None:
    theirs = _project(OTHER)
    with session_scope() as session, pytest.raises(projects.NotFound):
        projects.require_project(session, theirs, user_id=OWNER)


def test_nested_project_output_excludes_misowned_rows_even_on_an_owned_project() -> None:
    project_id = _project()
    their_result = _result(OTHER, "Smuggled result")
    with session_scope() as session:
        session.add(
            ProjectSource(
                user_id=OTHER,
                project_id=project_id,
                result_id=their_result,
                note="must not leak",
            )
        )
        session.add(
            ProjectArtifact(
                user_id=OTHER,
                project_id=project_id,
                stage="ideation",
                type="hypothesis",
                title="must not leak",
            )
        )
    with session_scope() as session:
        row = projects.require_project(session, project_id, user_id=OWNER)
        assert row.sources == []
        assert row.artifacts == []


def test_project_can_advance_and_move_back_after_a_failed_experiment() -> None:
    project_id = _project()
    with session_scope() as session:
        row = projects.advance_project(session, user_id=OWNER, project_id=project_id)
        assert row.stage == "ideation"
        row = projects.update_project(
            session,
            user_id=OWNER,
            project_id=project_id,
            changes={"stage": "discovery"},
        )
        assert row.stage == "discovery"


def test_complete_project_cannot_advance_past_terminal_stage() -> None:
    project_id = _project()
    with session_scope() as session:
        projects.update_project(
            session,
            user_id=OWNER,
            project_id=project_id,
            changes={"stage": "complete"},
        )
    with session_scope() as session, pytest.raises(projects.Conflict):
        projects.advance_project(session, user_id=OWNER, project_id=project_id)


def test_saved_source_is_idempotent_and_its_evidence_note_is_editable() -> None:
    project_id = _project()
    result_id = _result()
    with session_scope() as session:
        first = projects.add_source(
            session,
            user_id=OWNER,
            project_id=project_id,
            result_id=result_id,
            note="Supports the mechanism hypothesis",
        )
        second = projects.add_source(
            session,
            user_id=OWNER,
            project_id=project_id,
            result_id=result_id,
            note="A duplicate request must not create another row",
        )
        assert second.id == first.id
        assert second.note == "Supports the mechanism hypothesis"
        updated = projects.update_source_note(
            session,
            user_id=OWNER,
            project_id=project_id,
            source_id=first.id,
            note="Direct evidence on page 4",
        )
        assert updated.note == "Direct evidence on page 4"

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(ProjectSource).where(ProjectSource.project_id == project_id)
            )
        )
        assert len(rows) == 1


def test_cannot_add_another_users_search_result() -> None:
    project_id = _project()
    their_result = _result(OTHER, "Their private result")
    with session_scope() as session, pytest.raises(projects.NotFound):
        projects.add_source(
            session,
            user_id=OWNER,
            project_id=project_id,
            result_id=their_result,
        )


def test_concurrent_duplicate_source_add_recovers_as_idempotent_success() -> None:
    project_id = _project()
    result_id = _result()
    with session_scope() as session:
        real_scalar = session.scalar
        raced = False

        def racing_scalar(statement, *args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal raced
            if not raced and "project_sources" in str(statement):
                raced = True
                with session_scope() as rival:
                    rival.add(
                        ProjectSource(
                            user_id=OWNER,
                            project_id=project_id,
                            result_id=result_id,
                            note="winner",
                        )
                    )
                return None
            return real_scalar(statement, *args, **kwargs)

        session.scalar = racing_scalar  # type: ignore[method-assign]
        row = projects.add_source(
            session,
            user_id=OWNER,
            project_id=project_id,
            result_id=result_id,
            note="loser",
        )
        assert row.note == "winner"

    with session_scope() as session:
        assert len(
            list(
                session.scalars(
                    select(ProjectSource).where(ProjectSource.project_id == project_id)
                )
            )
        ) == 1


def test_artifact_crud_persists_real_workflow_content() -> None:
    project_id = _project()
    with session_scope() as session:
        artifact = projects.create_artifact(
            session,
            user_id=OWNER,
            project_id=project_id,
            stage="planning",
            type="experiment_plan",
            title="Test the riskiest routing assumption",
            body="Run three seeds against the frozen baseline; no execution claimed yet.",
        )
        artifact_id = artifact.id
        updated = projects.update_artifact(
            session,
            user_id=OWNER,
            project_id=project_id,
            artifact_id=artifact_id,
            changes={"status": "ready"},
        )
        assert updated.status == "ready"

    with session_scope() as session:
        rows = projects.list_artifacts(session, user_id=OWNER, project_id=project_id)
        assert [row.title for row in rows] == ["Test the riskiest routing assumption"]
        projects.delete_artifact(
            session,
            user_id=OWNER,
            project_id=project_id,
            artifact_id=artifact_id,
        )

    with session_scope() as session:
        assert session.get(ProjectArtifact, artifact_id) is None


def test_delete_project_cascades_searches_sources_and_artifacts() -> None:
    project_id = _project()
    result_id = _result(project_id=project_id)
    with session_scope() as session:
        search_id = session.get(LiteratureResult, result_id).search_id
        source = projects.add_source(
            session,
            user_id=OWNER,
            project_id=project_id,
            result_id=result_id,
        )
        artifact = projects.create_artifact(
            session,
            user_id=OWNER,
            project_id=project_id,
            stage="ideation",
            type="hypothesis",
            title="A falsifiable hypothesis",
        )
        source_id, artifact_id = source.id, artifact.id
        projects.delete_project(session, user_id=OWNER, project_id=project_id)

    with session_scope() as session:
        assert session.get(ResearchProject, project_id) is None
        assert session.get(ProjectSource, source_id) is None
        assert session.get(ProjectArtifact, artifact_id) is None
        assert session.get(LiteratureSearch, search_id) is None
        assert session.get(LiteratureResult, result_id) is None
