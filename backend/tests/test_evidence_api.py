"""HTTP contract for the Evidence Ledger.

Two properties are asserted here that the service tests cannot reach: the status
codes a client branches on (409 for "that quotation is not in this paper", 404 —
never 403 — for someone else's row), and the fact that none of these endpoints
answers an anonymous request. The route census in ``test_app_routes.py`` proves
the second against the assembled application; this file proves it again for the
router in isolation, so a regression is caught wherever it is introduced.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import evidence as evidence_api
from pharos.api.deps import current_user
from pharos.db.models import Evidence, Paper, PaperChunk, ResearchProject, User
from pharos.db.session import init_engine, session_scope
from sqlalchemy import delete

OWNER = "evidence-api-owner"
OTHER = "evidence-api-other"
USERS = (OWNER, OTHER)

PAGE_ONE = (
    "We introduce Pharos, an evidence-grounded reading system. "
    "The transformer baseline achieves 92.1% accuracy on the held-out split."
)
PAGE_TWO = "Related work on retrieval augmentation is extensive."


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    init_engine(tmp_path_factory.mktemp("evidence-api") / "pharos.db")
    with session_scope() as session:
        for user_id in USERS:
            if session.get(User, user_id) is None:
                session.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="x"))


@pytest.fixture(autouse=True)
def _clean() -> None:
    with session_scope() as session:
        session.execute(delete(Evidence).where(Evidence.user_id.in_(USERS)))
        session.execute(delete(PaperChunk).where(PaperChunk.user_id.in_(USERS)))
        session.execute(delete(ResearchProject).where(ResearchProject.user_id.in_(USERS)))
        session.execute(delete(Paper).where(Paper.user_id.in_(USERS)))


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(evidence_api.router)
    app.dependency_overrides[current_user] = lambda: User(id=OWNER, email="x", password_hash="x")
    return TestClient(app)


def _paper(user_id: str = OWNER, *, pages: tuple[str, ...] = (PAGE_ONE, PAGE_TWO)) -> str:
    with session_scope() as session:
        paper = Paper(
            user_id=user_id,
            title="Evidence-Grounded Reading",
            orig_sha256="sha-api",
            orig_filename="paper.pdf",
            abstract="We introduce Pharos.",
        )
        session.add(paper)
        session.flush()
        for index, text in enumerate(pages, start=1):
            session.add(
                PaperChunk(user_id=user_id, paper_id=paper.id, page_no=index, ordinal=0, text=text)
            )
        return paper.id


def test_no_evidence_endpoint_answers_an_anonymous_request() -> None:
    app = FastAPI()
    app.include_router(evidence_api.router)
    anon = TestClient(app)
    assert anon.get("/api/evidence").status_code == 401
    assert anon.post("/api/evidence", json={}).status_code == 401
    assert anon.post("/api/evidence/resolve", json={}).status_code == 401
    assert anon.get("/api/evidence/whatever").status_code == 401
    assert anon.patch("/api/evidence/whatever", json={}).status_code == 401
    assert anon.delete("/api/evidence/whatever").status_code == 401


def test_a_quote_is_created_with_the_page_it_was_found_on(client: TestClient) -> None:
    paper_id = _paper()
    created = client.post(
        "/api/evidence",
        json={
            "paper_id": paper_id,
            "kind": "quote",
            "text": "The transformer baseline\nachieves 92.1% accuracy",
            "statement": "The baseline is strong.",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert (body["kind"], body["locator"], body["page_no"]) == ("quote", "page", 1)
    assert body["chunk_id"] is not None
    assert body["provider"] is None

    fetched = client.get(f"/api/evidence/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["page_no"] == 1


def test_a_quote_the_paper_does_not_contain_is_a_409_not_a_silent_downgrade(
    client: TestClient,
) -> None:
    paper_id = _paper()
    refused = client.post(
        "/api/evidence",
        json={
            "paper_id": paper_id,
            "kind": "quote",
            "text": "The transformer baseline reaches 92.1 percent accuracy",
        },
    )
    assert refused.status_code == 409
    assert "does not appear" in refused.json()["detail"]
    assert client.get("/api/evidence").json() == []


def test_resolve_previews_a_placement_without_writing_one(client: TestClient) -> None:
    paper_id = _paper()
    placed = client.post(
        "/api/evidence/resolve",
        json={"paper_id": paper_id, "quote": "Related work on retrieval augmentation"},
    )
    assert placed.status_code == 200
    assert placed.json() == {
        "outcome": "page",
        "page_no": 2,
        "chunk_id": placed.json()["chunk_id"],
    }

    absent = client.post(
        "/api/evidence/resolve",
        json={"paper_id": paper_id, "quote": "A sentence this paper never contained."},
    )
    assert absent.json()["outcome"] == "not_in_paper"
    assert absent.json()["page_no"] is None
    assert client.get("/api/evidence").json() == []


def test_a_note_cannot_claim_an_unextracted_page(client: TestClient) -> None:
    paper_id = _paper()
    refused = client.post(
        "/api/evidence",
        json={"paper_id": paper_id, "kind": "note", "text": "Page 47 says so.", "page_no": 47},
    )
    assert refused.status_code == 400
    assert "no extracted text" in refused.json()["detail"]


def test_provenance_is_required_one_way_and_refused_the_other(client: TestClient) -> None:
    paper_id = _paper()
    bare_inference = client.post(
        "/api/evidence",
        json={"paper_id": paper_id, "kind": "model_inference", "text": "The gain is real."},
    )
    assert bare_inference.status_code == 400
    assert "provenance" in bare_inference.json()["detail"]

    dressed_note = client.post(
        "/api/evidence",
        json={
            "paper_id": paper_id,
            "kind": "note",
            "text": "My own reading.",
            "provider": "openai",
            "model": "gpt-x",
            "workflow_version": "v1",
            "input_sha256": "a" * 64,
        },
    )
    assert dressed_note.status_code == 400
    assert "human-authored" in dressed_note.json()["detail"]

    accepted = client.post(
        "/api/evidence",
        json={
            "paper_id": paper_id,
            "kind": "model_inference",
            "text": "The gain is real.",
            "provider": "openai",
            "model": "gpt-x",
            "workflow_version": "v1",
            "input_sha256": "a" * 64,
        },
    )
    assert accepted.status_code == 201
    assert accepted.json()["locator"] == "unlocated"


def test_patching_a_quote_moves_its_page_and_refuses_an_unfounded_edit(
    client: TestClient,
) -> None:
    paper_id = _paper()
    row = client.post(
        "/api/evidence",
        json={
            "paper_id": paper_id,
            "kind": "quote",
            "text": "We introduce Pharos, an evidence-grounded reading system.",
        },
    ).json()
    assert row["page_no"] == 1

    moved = client.patch(
        f"/api/evidence/{row['id']}",
        json={"text": "Related work on retrieval augmentation is extensive."},
    )
    assert moved.status_code == 200
    assert moved.json()["page_no"] == 2

    unfounded = client.patch(
        f"/api/evidence/{row['id']}", json={"text": "Pharos proves every claim automatically."}
    )
    assert unfounded.status_code == 409
    assert client.get(f"/api/evidence/{row['id']}").json()["page_no"] == 2


def test_kind_and_provenance_are_rejected_at_the_edge(client: TestClient) -> None:
    paper_id = _paper()
    row = client.post(
        "/api/evidence", json={"paper_id": paper_id, "kind": "note", "text": "A thought."}
    ).json()
    # ``extra="forbid"`` — 422 from the model, before the service is reached.
    assert client.patch(f"/api/evidence/{row['id']}", json={"kind": "quote"}).status_code == 422
    assert (
        client.patch(f"/api/evidence/{row['id']}", json={"provider": "openai"}).status_code == 422
    )


def test_listing_filters_and_a_stranger_row_is_404_never_403(client: TestClient) -> None:
    paper_id = _paper()
    theirs_paper = _paper(OTHER)
    with session_scope() as session:
        theirs = Evidence(
            user_id=OTHER,
            paper_id=theirs_paper,
            kind="note",
            locator="unlocated",
            text="Private.",
        )
        session.add(theirs)
        session.flush()
        theirs_id = theirs.id

    client.post(
        "/api/evidence",
        json={"paper_id": paper_id, "kind": "quote", "text": "We introduce Pharos,"},
    )
    client.post("/api/evidence", json={"paper_id": paper_id, "kind": "note", "text": "Mine."})

    assert len(client.get("/api/evidence").json()) == 2
    assert len(client.get("/api/evidence", params={"kind": "quote"}).json()) == 1
    assert len(client.get("/api/evidence", params={"locator": "page"}).json()) == 1
    assert client.get("/api/evidence", params={"paper_id": theirs_paper}).status_code == 404

    for response in (
        client.get(f"/api/evidence/{theirs_id}"),
        client.patch(f"/api/evidence/{theirs_id}", json={"statement": "mine now"}),
        client.delete(f"/api/evidence/{theirs_id}"),
    ):
        assert response.status_code == 404


def test_evidence_is_deleted_with_204(client: TestClient) -> None:
    paper_id = _paper()
    row = client.post(
        "/api/evidence", json={"paper_id": paper_id, "kind": "note", "text": "A thought."}
    ).json()
    assert client.delete(f"/api/evidence/{row['id']}").status_code == 204
    assert client.get(f"/api/evidence/{row['id']}").status_code == 404
