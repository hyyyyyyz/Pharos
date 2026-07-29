"""Web paper AI: credential hygiene, ownership, preparation, and streaming."""

from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pharos.api import ai_chat as ai_chat_api
from pharos.api.deps import current_user, get_settings
from pharos.config import Settings
from pharos.db.models import (
    AiConversation,
    AiMessage,
    AiProviderPreference,
    Paper,
    PaperAiContext,
    User,
)
from pharos.db.session import init_engine, session_scope
from pharos.services import ai_chat
from pharos.services.credentials import CredentialCipher, CredentialError
from sqlalchemy import delete, select

OWNER = "web-ai-owner"
OTHER = "web-ai-other"
PAPER = "web-ai-paper"
OTHER_PAPER = "web-ai-other-paper"
SECRET = "stable-web-ai-credential-secret-for-tests"
API_KEY = "sk-web-ai-secret-never-return-this"


def settings() -> Settings:
    return Settings(
        _env_file=None,
        credential_secret=SECRET,
        chat_provider="",
    )


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    init_engine(tmp_path_factory.mktemp("web-ai-db") / "pharos.db")
    with session_scope() as session:
        for user_id in (OWNER, OTHER):
            if session.get(User, user_id) is None:
                session.add(
                    User(
                        id=user_id,
                        email=f"{user_id}@example.test",
                        password_hash="x",
                    )
                )
        if session.get(Paper, PAPER) is None:
            session.add(
                Paper(
                    id=PAPER,
                    user_id=OWNER,
                    title="A Paper About Robust World Models",
                    authors="Ada Researcher; Lin Scientist",
                    abstract="A compact abstract about latent dynamics.",
                    full_text=(
                        "Introduction. The unique-trick is a dual-state latent memory. "
                        "Method. It keeps constant memory during long-horizon control. "
                        "Experiments. Ablations verify the memory and asynchronous decoder."
                    ),
                    orig_sha256="a" * 64,
                    orig_filename="world-model.pdf",
                    page_count=12,
                )
            )
        if session.get(Paper, OTHER_PAPER) is None:
            session.add(
                Paper(
                    id=OTHER_PAPER,
                    user_id=OTHER,
                    title="Another User's Private Paper",
                    full_text="private text",
                    orig_sha256="b" * 64,
                    orig_filename="private.pdf",
                )
            )


@pytest.fixture(autouse=True)
def _clean() -> None:
    with session_scope() as session:
        session.execute(delete(AiMessage).where(AiMessage.user_id.in_((OWNER, OTHER))))
        session.execute(delete(AiConversation).where(AiConversation.user_id.in_((OWNER, OTHER))))
        session.execute(delete(PaperAiContext).where(PaperAiContext.user_id.in_((OWNER, OTHER))))
        session.execute(
            delete(AiProviderPreference).where(AiProviderPreference.user_id.in_((OWNER, OTHER)))
        )


def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )


def _save_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_dns(monkeypatch)
    with session_scope() as session:
        ai_chat.save_provider(
            session,
            user_id=OWNER,
            settings=settings(),
            base_url="https://relay.example.test/v1",
            model="research-model",
            temperature=0.2,
            max_output_tokens=2048,
            api_key=API_KEY,
        )


def test_personal_provider_is_encrypted_and_never_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_provider(monkeypatch)
    with session_scope() as session:
        row = session.get(AiProviderPreference, OWNER)
        assert row is not None
        assert row.api_key.startswith("fernet:v1:")
        assert API_KEY not in row.api_key

        view = ai_chat.provider_view(session, user_id=OWNER, settings=settings())
        assert view.configured is True
        assert view.has_credential is True
        assert API_KEY not in repr(view)

        runtime = ai_chat.effective_provider(session, user_id=OWNER, settings=settings())
        assert runtime is not None
        assert runtime.api_key == API_KEY


def test_ai_and_zotero_ciphertexts_use_distinct_derivations() -> None:
    cfg = settings()
    zotero_ciphertext = CredentialCipher.from_settings(cfg).protect(API_KEY)
    ai_ciphertext = CredentialCipher.for_ai_provider(cfg).protect(API_KEY)
    assert zotero_ciphertext != ai_ciphertext
    with pytest.raises(CredentialError):
        CredentialCipher.for_ai_provider(cfg).reveal(zotero_ciphertext)


def test_personal_provider_rejects_loopback() -> None:
    with session_scope() as session, pytest.raises(ai_chat.InvalidProvider):
        ai_chat.save_provider(
            session,
            user_id=OWNER,
            settings=settings(),
            base_url="https://127.0.0.1/v1",
            model="model",
            temperature=0.25,
            max_output_tokens=4096,
            api_key=API_KEY,
        )


def test_context_uses_server_owned_text_and_hides_other_users() -> None:
    with session_scope() as session:
        started = ai_chat.ensure_context(
            session,
            user_id=OWNER,
            paper_id=PAPER,
            settings=settings(),
        )
        assert started.should_start is False
        assert started.context.status == "indexed"
        assert started.context.char_count > 100
        assert started.context.content_hash

        with pytest.raises(ai_chat.NotFound):
            ai_chat.ensure_context(
                session,
                user_id=OWNER,
                paper_id=OTHER_PAPER,
                settings=settings(),
            )


def test_background_preparation_builds_a_reusable_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_provider(monkeypatch)
    monkeypatch.setattr(
        ai_chat,
        "complete",
        lambda _provider, messages: (
            "## 一句话结论\n论文通过 dual-state latent memory 保持常数内存。"
            if "unique-trick" in messages[-1]["content"]
            else "missing context"
        ),
    )
    with session_scope() as session:
        started = ai_chat.ensure_context(
            session,
            user_id=OWNER,
            paper_id=PAPER,
            settings=settings(),
        )
        assert started.should_start is True
        assert started.context.status == "understanding"
        digest = started.context.content_hash

    ai_chat.build_profile(
        user_id=OWNER,
        paper_id=PAPER,
        expected_hash=digest,
        settings=settings(),
    )
    with session_scope() as session:
        context = ai_chat.context_for(session, user_id=OWNER, paper_id=PAPER)
        assert context is not None
        assert context.status == "ready"
        assert "dual-state" in (context.summary or "")


def test_conversations_are_strictly_owner_and_paper_scoped() -> None:
    with session_scope() as session:
        conversation = ai_chat.create_conversation(
            session,
            user_id=OWNER,
            paper_id=PAPER,
        )
        assert ai_chat.list_conversations(session, user_id=OWNER, paper_id=PAPER) == [conversation]
        with pytest.raises(ai_chat.NotFound):
            ai_chat.require_conversation(
                session,
                user_id=OTHER,
                conversation_id=conversation.id,
            )
        with pytest.raises(ai_chat.NotFound):
            ai_chat.create_conversation(
                session,
                user_id=OWNER,
                paper_id=OTHER_PAPER,
            )


def test_stream_persists_visible_turns_and_emits_ndjson(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_provider(monkeypatch)
    with session_scope() as session:
        context = ai_chat.ensure_context(
            session,
            user_id=OWNER,
            paper_id=PAPER,
            settings=settings(),
        ).context
        context.summary = "已知核心机制是 dual-state latent memory。"
        context.status = "ready"
        conversation = ai_chat.create_conversation(
            session,
            user_id=OWNER,
            paper_id=PAPER,
        )
        conversation_id = conversation.id
        state = ai_chat.prepare_chat_request(
            session,
            user_id=OWNER,
            conversation_id=conversation_id,
            message="核心 trick 是什么？",
            settings=settings(),
        )

    monkeypatch.setattr(
        ai_chat,
        "stream_completion",
        lambda _provider, messages: iter(
            ["核心机制是", " dual-state latent memory。"]
            if "dual-state" in messages[0]["content"]
            else ["上下文丢失"]
        ),
    )
    events = [json.loads(line) for line in ai_chat.stream_chat_events(state, run_id="run-1")]
    assert [event["type"] for event in events] == ["started", "delta", "delta", "done"]
    assert events[-1]["message"]["content"] == "核心机制是 dual-state latent memory。"

    with session_scope() as session:
        messages = ai_chat.conversation_messages(
            session,
            user_id=OWNER,
            conversation_id=conversation_id,
        )
        assert [(message.role, message.content) for message in messages] == [
            ("user", "核心 trick 是什么？"),
            ("assistant", "核心机制是 dual-state latent memory。"),
        ]
        assert (
            session.scalar(select(AiConversation.title).where(AiConversation.id == conversation_id))
            == "核心 trick 是什么？"
        )


def test_http_api_is_mounted_shape_compatible_and_persistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_provider(monkeypatch)
    monkeypatch.setattr(ai_chat, "complete", lambda *_args, **_kwargs: "网页论文理解档案")
    monkeypatch.setattr(
        ai_chat,
        "stream_completion",
        lambda *_args, **_kwargs: iter(["网页端", "回答"]),
    )

    app = FastAPI()
    api_router = APIRouter(prefix="/api")
    api_router.include_router(ai_chat_api.router)
    app.include_router(api_router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=OWNER)
    app.dependency_overrides[get_settings] = settings

    with TestClient(app) as client:
        provider = client.get("/api/ai/provider")
        assert provider.status_code == 200
        assert provider.json()["configured"] is True
        assert provider.json()["baseUrl"] == "https://relay.example.test/v1"
        assert API_KEY not in provider.text

        prepared = client.post(f"/api/ai/papers/{PAPER}/prepare")
        assert prepared.status_code == 200
        assert prepared.json()["documentKey"] == f"paper:{PAPER}"
        context = client.get(f"/api/ai/papers/{PAPER}/context").json()
        assert context["status"] == "ready"
        assert context["hasSummary"] is True

        created = client.post(
            f"/api/ai/papers/{PAPER}/conversations",
            json={"title": None},
        )
        assert created.status_code == 201
        conversation_id = created.json()["id"]

        streamed = client.post(
            f"/api/ai/conversations/{conversation_id}/messages/stream",
            json={"runId": "web-run", "message": "请解释核心方法"},
        )
        assert streamed.status_code == 200
        events = [json.loads(line) for line in streamed.iter_lines()]
        assert [event["type"] for event in events] == [
            "started",
            "delta",
            "delta",
            "done",
        ]

        detail = client.get(f"/api/ai/conversations/{conversation_id}").json()
        assert [message["role"] for message in detail["messages"]] == [
            "user",
            "assistant",
        ]
        assert detail["messages"][-1]["content"] == "网页端回答"
