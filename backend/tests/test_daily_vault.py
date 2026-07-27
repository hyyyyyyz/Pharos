"""Portable Daily Vault export, validation, ownership, and merge semantics."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import daily_vault as vault_api
from pharos.api.deps import current_user
from pharos.daily.vault import DailyVaultArchive, build_archive, import_archive
from pharos.db.models import DailyPaper, DailyRun, User, UserDailyConfig, UserDirection
from pharos.db.session import init_engine, session_scope
from pydantic import ValidationError
from sqlalchemy import delete, select

OWNER = "daily-vault-owner"
RESTORE_USER = "daily-vault-restore"
DATE = "2099-07-27"
ARXIV_PREFIX = "vault-test."


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    init_engine(tmp_path_factory.mktemp("daily-vault-db") / "pharos.db")
    with session_scope() as session:
        for user_id in (OWNER, RESTORE_USER):
            if session.get(User, user_id) is None:
                session.add(
                    User(
                        id=user_id,
                        email=f"{user_id}@example.test",
                        password_hash="not-a-real-hash",
                    )
                )


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            session.execute(
                delete(UserDirection).where(UserDirection.user_id.in_((OWNER, RESTORE_USER)))
            )
            session.execute(
                delete(UserDailyConfig).where(
                    UserDailyConfig.user_id.in_((OWNER, RESTORE_USER))
                )
            )
            session.execute(
                delete(DailyPaper).where(DailyPaper.arxiv_id.like(f"{ARXIV_PREFIX}%"))
            )
            session.execute(delete(DailyRun).where(DailyRun.date == DATE))

    wipe()
    yield
    wipe()


def _seed_owner() -> None:
    now = dt.datetime(2099, 7, 27, 1, 2, tzinfo=dt.UTC)
    with session_scope() as session:
        session.add(
            UserDailyConfig(
                user_id=OWNER,
                categories="cs.AI,cs.CL",
                max_per_day=20,
                enabled=True,
                seeded=True,
                updated_at=now,
            )
        )
        session.add(
            UserDirection(
                user_id=OWNER,
                name="Vault research",
                # The trailing space is intentional and must survive a round trip.
                keywords="vault-keyword\nword-boundary ",
                enabled=True,
                position=0,
                created_at=now,
            )
        )
        session.add(
            DailyPaper(
                arxiv_id=f"{ARXIV_PREFIX}0001",
                date=DATE,
                title="A vault-keyword paper",
                authors="Ada Lovelace; Alan Turing",
                abstract="Portable research data.",
                categories="cs.AI,cs.CL",
                matched_domain="shared legacy value",
                matched_keywords="legacy",
                arxiv_url="https://arxiv.org/abs/2099.00001",
                pdf_url="https://arxiv.org/pdf/2099.00001",
                published_at=now,
                read_status="done",
                summary_zh="一份可迁移的每日论文快照。",
                highlights=json.dumps({"innovation": "目录恢复"}, ensure_ascii=False),
                scores=json.dumps(
                    {
                        "relevance": 1.0,
                        "recency": 8.0,
                        "popularity": 7.0,
                        "quality": 9.0,
                        "recommendation": 3.0,
                    }
                ),
                score_recommendation=3.0,
                read_model="test-model",
                read_at=now,
                imported_paper_id="private-library-id",
                created_at=now,
            )
        )
        session.add(
            DailyRun(
                date=DATE,
                status="done",
                fetched=1,
                read_done=1,
                read_failed=0,
                started_at=now,
                finished_at=now,
            )
        )


def _archive() -> DailyVaultArchive:
    _seed_owner()
    with session_scope() as session:
        return build_archive(session, user_id=OWNER)


def test_export_is_portable_personal_and_secret_free() -> None:
    archive = _archive()

    assert archive.kind == "pharos.daily.archive"
    assert archive.schema_version == 1
    assert archive.profile.settings.categories == ["cs.AI", "cs.CL"]
    assert archive.profile.directions[0].keywords[-1] == "word-boundary "
    assert len(archive.days) == 1

    paper = archive.days[0].papers[0]
    assert paper.rank == 1
    assert paper.matched_direction == "Vault research"
    assert paper.matched_keywords == ["vault-keyword"]
    # The exported relevance/recommendation are recomputed for this account,
    # not copied from the old shared rubric's deliberately wrong 1.0/3.0.
    assert paper.scores is not None
    assert paper.scores["relevance"] != 1.0
    assert paper.scores["recommendation"] != 3.0

    payload = archive.model_dump_json()
    assert "private-library-id" not in payload
    assert '"user_id"' not in payload
    assert "api_key" not in payload
    assert '"id"' not in payload


def test_import_restores_profile_and_is_idempotent() -> None:
    archive = _archive()
    archived_paper = archive.days[0].papers[0]
    archived_paper.arxiv_id = f"{ARXIV_PREFIX}9001"

    with session_scope() as session:
        first = import_archive(session, user_id=RESTORE_USER, archive=archive)
    assert first.papers_added == 1
    assert first.papers_updated == 0
    assert first.directions_restored == 1
    assert first.profile_restored is True

    with session_scope() as session:
        direction = session.scalar(
            select(UserDirection).where(UserDirection.user_id == RESTORE_USER)
        )
        assert direction is not None
        assert direction.keywords.endswith("word-boundary ")
        config = session.get(UserDailyConfig, RESTORE_USER)
        assert config is not None
        assert config.categories == "cs.AI,cs.CL"

        paper = session.scalar(
            select(DailyPaper).where(DailyPaper.arxiv_id == archived_paper.arxiv_id)
        )
        assert paper is not None
        assert paper.matched_domain is None
        assert paper.imported_paper_id is None
        stored_scores = json.loads(paper.scores or "{}")
        assert stored_scores == {"recency": 8.0, "popularity": 7.0, "quality": 9.0}

    with session_scope() as session:
        second = import_archive(session, user_id=RESTORE_USER, archive=archive)
    assert second.papers_added == 0
    assert second.papers_updated == 0
    assert second.papers_unchanged == 1


def test_import_never_overwrites_a_completed_server_reading() -> None:
    archive = _archive()
    archived_paper = archive.days[0].papers[0]
    archived_paper.arxiv_id = f"{ARXIV_PREFIX}9002"

    with session_scope() as session:
        session.add(
            DailyPaper(
                arxiv_id=archived_paper.arxiv_id,
                date=DATE,
                title=archived_paper.title,
                read_status="done",
                summary_zh="服务器上的更新解读",
                created_at=dt.datetime.now(dt.UTC),
            )
        )

    with session_scope() as session:
        result = import_archive(session, user_id=RESTORE_USER, archive=archive)
    assert result.papers_updated == 1  # missing metadata was filled

    with session_scope() as session:
        row = session.scalar(
            select(DailyPaper).where(DailyPaper.arxiv_id == archived_paper.arxiv_id)
        )
        assert row is not None
        assert row.summary_zh == "服务器上的更新解读"


def test_validation_rejects_unsafe_urls_and_ambiguous_duplicates() -> None:
    source = _archive().model_dump(mode="json")
    archive = json.loads(json.dumps(source))
    archive["days"][0]["papers"][0]["pdf_url"] = "file:///etc/passwd"
    with pytest.raises(ValidationError):
        DailyVaultArchive.model_validate(archive)

    archive = json.loads(json.dumps(source))
    archive["days"].append(archive["days"][0])
    with pytest.raises(ValidationError):
        DailyVaultArchive.model_validate(archive)


def test_http_export_route_is_not_swallowed_by_the_date_route() -> None:
    _seed_owner()
    app = FastAPI()
    app.include_router(vault_api.router)

    def signed_in() -> Iterator[User]:
        with session_scope() as session:
            user = session.get(User, OWNER)
            assert user is not None
            yield user

    app.dependency_overrides[current_user] = signed_in
    with TestClient(app) as client:
        response = client.get("/api/daily/vault/export")
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "pharos.daily.archive"
