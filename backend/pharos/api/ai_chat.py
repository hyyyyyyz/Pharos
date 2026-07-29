"""Authenticated web API for paper-aware AI conversations."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session, get_settings
from pharos.config import Settings
from pharos.db.models import AiConversation, AiMessage, Paper, PaperAiContext, User
from pharos.services import ai_chat

router = APIRouter(prefix="/ai", tags=["ai-chat"])


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ProviderStatusOut(CamelModel):
    configured: bool
    has_credential: bool
    base_url: str
    model: str
    temperature: float
    max_output_tokens: int
    source: Literal["personal", "server", "none"]
    can_store_credential: bool


class ProviderSaveIn(CamelModel):
    base_url: Annotated[str, Field(min_length=1, max_length=1024)]
    model: Annotated[str, Field(min_length=1, max_length=200)]
    temperature: Annotated[float, Field(ge=0, le=2)] = 0.25
    max_output_tokens: Annotated[int, Field(ge=256, le=128_000)] = 4096
    api_key: Annotated[str, Field(max_length=4096)] | None = None


class PaperContextOut(CamelModel):
    document_key: str
    status: str
    char_count: int
    page_count: int | None
    has_summary: bool
    summary: str | None
    error: str | None
    updated_at_ms: int


class ConversationSummaryOut(CamelModel):
    id: str
    document_key: str
    document_kind: str
    document_title: str
    title: str
    source: str
    source_session_id: str | None
    created_at_ms: int
    updated_at_ms: int


class MessageOut(CamelModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    timestamp_ms: int
    model: str | None


class ConversationDetailOut(ConversationSummaryOut):
    messages: list[MessageOut]


class ConversationCreateIn(CamelModel):
    title: Annotated[str, Field(max_length=256)] | None = None


class SendMessageIn(CamelModel):
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    message: Annotated[str, Field(min_length=1, max_length=32_000)]


def _provider_out(value: ai_chat.ProviderView) -> ProviderStatusOut:
    return ProviderStatusOut(
        configured=value.configured,
        has_credential=value.has_credential,
        base_url=value.base_url,
        model=value.model,
        temperature=value.temperature,
        max_output_tokens=value.max_output_tokens,
        source=value.source,  # type: ignore[arg-type]
        can_store_credential=value.can_store_credential,
    )


def _context_out(paper_id: str, value: PaperAiContext) -> PaperContextOut:
    return PaperContextOut(
        document_key=f"paper:{paper_id}",
        status=value.status,
        char_count=value.char_count,
        page_count=value.page_count,
        has_summary=bool(value.summary),
        summary=value.summary,
        error=value.error,
        updated_at_ms=ai_chat.epoch_ms(value.updated_at or value.created_at),
    )


def _conversation_out(value: AiConversation, paper: Paper) -> ConversationSummaryOut:
    return ConversationSummaryOut(
        id=value.id,
        document_key=f"paper:{paper.id}",
        document_kind="paper",
        document_title=paper.title,
        title=value.title,
        source=value.source,
        source_session_id=value.source_session_id,
        created_at_ms=ai_chat.epoch_ms(value.created_at),
        updated_at_ms=ai_chat.epoch_ms(value.updated_at or value.created_at),
    )


def _message_out(value: AiMessage) -> MessageOut:
    return MessageOut(
        id=value.id,
        role=value.role,  # type: ignore[arg-type]
        content=value.content,
        timestamp_ms=ai_chat.epoch_ms(value.created_at),
        model=value.model,
    )


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, ai_chat.NotFound):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, ai_chat.InvalidProvider):
        return HTTPException(status_code=400, detail=str(error))
    if isinstance(error, ai_chat.ProviderUnavailable):
        return HTTPException(status_code=503, detail=str(error))
    if isinstance(error, ai_chat.ProviderFailure):
        return HTTPException(status_code=502, detail=str(error))
    if isinstance(error, ai_chat.ConversationBusy):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ai_chat.AiChatError):
        return HTTPException(status_code=400, detail=str(error))
    return HTTPException(status_code=500, detail="AI 对话服务暂时不可用。")


@router.get("/provider", response_model=ProviderStatusOut)
def get_provider(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProviderStatusOut:
    return _provider_out(ai_chat.provider_view(session, user_id=user.id, settings=settings))


@router.put("/provider", response_model=ProviderStatusOut)
def put_provider(
    payload: ProviderSaveIn,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProviderStatusOut:
    try:
        value = ai_chat.save_provider(
            session,
            user_id=user.id,
            settings=settings,
            base_url=payload.base_url,
            model=payload.model,
            temperature=payload.temperature,
            max_output_tokens=payload.max_output_tokens,
            api_key=payload.api_key,
        )
    except Exception as error:
        raise _http_error(error) from error
    return _provider_out(value)


@router.delete("/provider", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    ai_chat.clear_provider(session, user_id=user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/papers/{paper_id}/context", response_model=PaperContextOut | None)
def get_paper_context(
    paper_id: str,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> PaperContextOut | None:
    try:
        ai_chat.owned_paper(session, user_id=user.id, paper_id=paper_id)
    except Exception as error:
        raise _http_error(error) from error
    value = ai_chat.context_for(session, user_id=user.id, paper_id=paper_id)
    return _context_out(paper_id, value) if value is not None else None


@router.post("/papers/{paper_id}/prepare", response_model=PaperContextOut)
def prepare_paper(
    paper_id: str,
    background: BackgroundTasks,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PaperContextOut:
    try:
        started = ai_chat.ensure_context(
            session,
            user_id=user.id,
            paper_id=paper_id,
            settings=settings,
        )
        # The background task opens its own session.  Commit the status first so
        # it can see the row and the browser can poll it immediately.
        session.commit()
    except Exception as error:
        raise _http_error(error) from error
    if started.should_start:
        background.add_task(
            ai_chat.build_profile,
            user_id=user.id,
            paper_id=paper_id,
            expected_hash=started.context.content_hash,
            settings=settings,
        )
    return _context_out(paper_id, started.context)


@router.get(
    "/papers/{paper_id}/conversations",
    response_model=list[ConversationSummaryOut],
)
def list_paper_conversations(
    paper_id: str,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> list[ConversationSummaryOut]:
    try:
        paper = ai_chat.owned_paper(session, user_id=user.id, paper_id=paper_id)
        values = ai_chat.list_conversations(session, user_id=user.id, paper_id=paper_id)
    except Exception as error:
        raise _http_error(error) from error
    return [_conversation_out(value, paper) for value in values]


@router.post(
    "/papers/{paper_id}/conversations",
    response_model=ConversationSummaryOut,
    status_code=status.HTTP_201_CREATED,
)
def create_paper_conversation(
    paper_id: str,
    payload: ConversationCreateIn,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> ConversationSummaryOut:
    try:
        paper = ai_chat.owned_paper(session, user_id=user.id, paper_id=paper_id)
        value = ai_chat.create_conversation(
            session,
            user_id=user.id,
            paper_id=paper_id,
            title=payload.title,
        )
    except Exception as error:
        raise _http_error(error) from error
    return _conversation_out(value, paper)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> ConversationDetailOut:
    try:
        value = ai_chat.require_conversation(
            session,
            user_id=user.id,
            conversation_id=conversation_id,
        )
        paper = ai_chat.owned_paper(session, user_id=user.id, paper_id=value.paper_id)
        messages = ai_chat.conversation_messages(
            session,
            user_id=user.id,
            conversation_id=conversation_id,
        )
    except Exception as error:
        raise _http_error(error) from error
    summary = _conversation_out(value, paper).model_dump()
    return ConversationDetailOut(**summary, messages=[_message_out(item) for item in messages])


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    try:
        ai_chat.delete_conversation(
            session,
            user_id=user.id,
            conversation_id=conversation_id,
        )
    except Exception as error:
        raise _http_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/messages/stream")
def stream_message(
    conversation_id: str,
    payload: SendMessageIn,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    state: ai_chat.ChatRequestState | None = None
    try:
        state = ai_chat.prepare_chat_request(
            session,
            user_id=user.id,
            conversation_id=conversation_id,
            message=payload.message,
            settings=settings,
        )
        # The streaming generator runs after this endpoint returns and uses a
        # fresh session to persist the assistant turn.  Commit the user turn now.
        session.commit()
    except Exception as error:
        if state is not None:
            ai_chat.release_conversation_run(state.conversation_id)
        raise _http_error(error) from error
    return StreamingResponse(
        ai_chat.stream_chat_events(state, run_id=payload.run_id),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
