"""End-to-end HTTP contract for discovery, projects, evidence and AI reading."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import discovery as discovery_api
from pharos.api import projects as projects_api
from pharos.api.deps import current_user
from pharos.daily import reader
from pharos.db.models import (
    LiteratureResult,
    LiteratureSearch,
    ProjectArtifact,
    ProjectSource,
    ResearchProject,
    User,
)
from pharos.db.session import init_engine, session_scope
from pharos.services import discovery
from pharos.services.discovery import DiscoveredPaper, DiscoveryBatch
from sqlalchemy import delete

OWNER = "research-api-owner"
OTHER = "research-api-other"
USERS = (OWNER, OTHER)


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    init_engine(tmp_path_factory.mktemp("research-api") / "pharos.db")
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


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(projects_api.router)
    app.include_router(discovery_api.router)
    app.dependency_overrides[current_user] = lambda: User(id=OWNER, email="x", password_hash="x")
    return TestClient(app)


def _batch(*, errors: dict[str, str] | None = None) -> DiscoveryBatch:
    paper = DiscoveredPaper(
        source="arxiv",
        external_id="2607.00001",
        title="Evidence-Grounded Robot Learning",
        authors=("Ada Lovelace",),
        abstract=(
            "We introduce an evidence-grounded policy using calibrated retrieval. "
            "Experiments show 12.5% improvement over the frozen baseline."
        ),
        year=2026,
        venue="arXiv",
        url="https://arxiv.org/abs/2607.00001",
        pdf_url="https://arxiv.org/pdf/2607.00001",
        sources=("arxiv",),
        source_ids=(("arxiv", "2607.00001"),),
    )
    return DiscoveryBatch((paper,), errors or {})


def test_every_research_route_requires_authentication() -> None:
    app = FastAPI()
    app.include_router(projects_api.router)
    app.include_router(discovery_api.router)
    unauthenticated = TestClient(app)
    assert unauthenticated.get("/api/projects").status_code == 401
    assert unauthenticated.get("/api/discovery/searches").status_code == 401


def test_project_artifact_and_stage_workflow_over_http(client: TestClient) -> None:
    created = client.post(
        "/api/projects",
        json={"name": "VLA Reliability", "research_question": "When does routing fail?"},
    )
    assert created.status_code == 201
    project = created.json()
    assert project["stage"] == "discovery"
    assert project["automation_notice"].startswith("No compute runner is connected")

    advanced = client.post(f"/api/projects/{project['id']}/advance")
    assert advanced.status_code == 200
    assert advanced.json()["stage"] == "ideation"

    artifact = client.post(
        f"/api/projects/{project['id']}/artifacts",
        json={
            "stage": "planning",
            "type": "experiment_plan",
            "title": "Frozen-baseline pilot",
            "body": "Three seeds; this record does not claim the run happened.",
        },
    )
    assert artifact.status_code == 201
    artifact_id = artifact.json()["id"]
    assert artifact.json()["status"] == "draft"

    updated = client.patch(
        f"/api/projects/{project['id']}/artifacts/{artifact_id}",
        json={"status": "ready"},
    )
    assert updated.json()["status"] == "ready"

    moved_back = client.patch(
        f"/api/projects/{project['id']}", json={"stage": "discovery"}
    )
    assert moved_back.json()["stage"] == "discovery"


def test_search_is_persisted_reopenable_and_can_be_saved_as_evidence(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(discovery, "discover", lambda query, sources, limit: _batch())
    project = client.post("/api/projects", json={"name": "Evidence Project"}).json()

    response = client.post(
        "/api/discovery/search",
        json={
            "query": "evidence grounded robot learning",
            "project_id": project["id"],
            "sources": ["arxiv", "openalex"],
            "limit": 20,
        },
    )
    assert response.status_code == 201
    search = response.json()
    assert search["status"] == "complete"
    assert search["result_count"] == 1
    result = search["results"][0]
    assert result["analysis_mode"] == "rules"
    assert result["results"].startswith("Experiments show 12.5%")

    reopened = client.get(f"/api/discovery/searches/{search['id']}")
    assert reopened.status_code == 200
    assert reopened.json()["results"][0]["id"] == result["id"]

    saved = client.post(
        f"/api/projects/{project['id']}/sources",
        json={"result_id": result["id"], "note": "Evidence for the retrieval mechanism"},
    )
    assert saved.status_code == 200
    source_id = saved.json()["id"]
    patched = client.patch(
        f"/api/projects/{project['id']}/sources/{source_id}",
        json={"note": "Direct evidence; inspect assumptions before use"},
    )
    assert patched.json()["note"].startswith("Direct evidence")

    detail = client.get(f"/api/projects/{project['id']}").json()
    assert detail["source_count"] == 1
    assert detail["sources"][0]["result"]["title"] == result["title"]


def test_all_provider_failures_return_a_persisted_structured_error(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        discovery,
        "discover",
        lambda query, sources, limit: DiscoveryBatch(
            (), {"arxiv": "timed out", "openalex": "HTTP 503"}
        ),
    )
    response = client.post(
        "/api/discovery/search",
        json={"query": "robot learning", "sources": ["arxiv", "openalex"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "error"
    assert body["results"] == []
    assert body["errors"] == {"arxiv": "timed out", "openalex": "HTTP 503"}
    assert client.get(f"/api/discovery/searches/{body['id']}").json()["status"] == "error"


def test_input_limits_and_unknown_provider_fail_before_network(client: TestClient) -> None:
    assert client.post("/api/discovery/search", json={"query": "x"}).status_code == 422
    assert (
        client.post(
            "/api/discovery/search",
            json={"query": "robot", "sources": ["made-up-provider"]},
        ).status_code
        == 422
    )


def test_ai_analysis_unavailable_keeps_rule_fields_unchanged(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(discovery, "discover", lambda query, sources, limit: _batch())
    result = client.post(
        "/api/discovery/search", json={"query": "robot learning", "sources": ["arxiv"]}
    ).json()["results"][0]

    def unavailable(*args, **kwargs):
        raise reader.ReaderUnavailable("no chat provider is configured")

    monkeypatch.setattr(reader, "read_paper", unavailable)
    response = client.post(f"/api/discovery/results/{result['id']}/analyze")
    assert response.status_code == 409

    with session_scope() as session:
        stored = session.get(LiteratureResult, result["id"])
        assert stored.analysis_mode == "rules"
        assert stored.core_trick == result["core_trick"]


def test_invalid_ai_core_trick_does_not_overwrite_rule_fields(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(discovery, "discover", lambda query, sources, limit: _batch())
    result = client.post(
        "/api/discovery/search", json={"query": "robot learning", "sources": ["arxiv"]}
    ).json()["results"][0]

    def invalid(*args, **kwargs):
        raise reader.InvalidReading("highlights.innovation is not Chinese")

    monkeypatch.setattr(reader, "read_paper", invalid)
    response = client.post(f"/api/discovery/results/{result['id']}/analyze")
    assert response.status_code == 503

    with session_scope() as session:
        stored = session.get(LiteratureResult, result["id"])
        assert stored.title == result["title"]
        assert stored.analysis_mode == "rules"
        assert stored.analysis_model is None
        assert stored.core_trick == result["core_trick"]
        assert stored.method == result["method"]
        assert stored.results == result["results"]


def test_ai_analysis_maps_only_a_validated_real_reading(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(discovery, "discover", lambda query, sources, limit: _batch())
    result = client.post(
        "/api/discovery/search", json={"query": "robot learning", "sources": ["arxiv"]}
    ).json()["results"][0]
    original_title = result["title"]
    monkeypatch.setattr(
        reader,
        "read_paper",
        lambda *args, **kwargs: reader.Reading(
            summary_zh="这篇工作研究检索校准对机器人策略可靠性的影响，并报告了摘要中的结果。",
            highlights={
                "contribution": "提出有证据约束的机器人学习方法。",
                "innovation": "将校准检索接入策略决策。",
                "method": "使用冻结基线比较校准检索模块。",
                "results": "摘要报告相对基线提升 12.5%。",
            },
            scores={"recency": 10.0, "popularity": 5.0, "quality": 7.0, "recommendation": 7.0},
            model="test-real-provider-model",
        ),
    )

    response = client.post(f"/api/discovery/results/{result['id']}/analyze")

    assert response.status_code == 200
    analyzed = response.json()
    assert analyzed["analysis_mode"] == "llm"
    assert analyzed["analysis_model"] == "test-real-provider-model"
    assert analyzed["title"] == original_title
    assert analyzed["summary_zh"].startswith("这篇工作")
    assert analyzed["core_trick"] == "将校准检索接入策略决策。"
    assert len(analyzed["core_trick"]) <= 180
    assert any("一" <= char <= "鿿" for char in analyzed["core_trick"])
