"""Strict, deliberately small models for the DeepSeek Harness SDK wire.

The upstream SDK is extensible. This boundary is not: a DSH process is a
constrained sidecar, so an event outside the reviewed vocabulary is a protocol
failure rather than something to ignore.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_IDENTIFIER_BYTES = 1024


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InitializeParams(WireModel):
    cwd: str
    provider: str
    model: str
    reasoningEffort: str | None = None
    maxTokens: int | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> InitializeParams:
        if not self.cwd or not self.provider or not self.model:
            raise ValueError("initialize cwd, provider and model must be non-empty")
        if self.reasoningEffort == "":
            raise ValueError("reasoningEffort must be non-empty")
        if self.maxTokens is not None and (
            not _nonnegative_int(self.maxTokens) or self.maxTokens == 0
        ):
            raise ValueError("maxTokens must be a positive integer")
        return self


class ServerInfo(WireModel):
    name: str
    version: str


class InitializeResult(WireModel):
    serverInfo: ServerInfo


class TextBlock(WireModel):
    type: Literal["text"]
    text: str

    @field_validator("text")
    @classmethod
    def validate_utf8(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("text must be valid UTF-8") from None
        return value


class ReasoningBlock(WireModel):
    type: Literal["reasoning"]
    text: str

    @field_validator("text")
    @classmethod
    def validate_utf8(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("reasoning must be valid UTF-8") from None
        return value


class ImageBlock(WireModel):
    type: Literal["image"]
    attachment: dict[str, Any]


class ToolCallBlock(WireModel):
    type: Literal["tool-call"]
    id: str
    name: str
    arguments: str


class ToolResultBlock(WireModel):
    type: Literal["tool-result"]
    toolCallId: str
    content: list[ContentBlock]
    isError: bool | None = None


ContentBlock = Annotated[
    TextBlock | ReasoningBlock | ImageBlock | ToolCallBlock | ToolResultBlock,
    Field(discriminator="type"),
]
CONTENT_BLOCK_ADAPTER = TypeAdapter(list[ContentBlock])
SAFE_CONTENT_BLOCK_ADAPTER = TypeAdapter(list[TextBlock | ReasoningBlock])


class SessionPromptParams(WireModel):
    sessionId: str
    # H1.5 is text-only. Image, tool and reasoning prompt capabilities require
    # a separately reviewed capability and never cross this boundary.
    contentBlocks: list[TextBlock] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def validate_session(self) -> SessionPromptParams:
        if not _bounded_utf8_text(self.sessionId, MAX_IDENTIFIER_BYTES):
            raise ValueError("sessionId must be non-empty")
        if not self.contentBlocks[0].text.strip():
            raise ValueError("prompt text must be non-empty")
        return self


class SessionPromptResult(WireModel):
    messageId: str

    @model_validator(mode="after")
    def validate_message(self) -> SessionPromptResult:
        if not _bounded_utf8_text(self.messageId, MAX_IDENTIFIER_BYTES):
            raise ValueError("messageId must be non-empty")
        return self


class TokenUsage(WireModel):
    inputTokens: int
    outputTokens: int
    totalTokens: int | None = None
    cacheReadTokens: int | None = None
    cacheWriteTokens: int | None = None
    reasoningTokens: int | None = None

    @model_validator(mode="after")
    def validate_counts(self) -> TokenUsage:
        values = self.model_dump()
        if any(not _nonnegative_int(value) for value in values.values() if value is not None):
            raise ValueError("usage values must be non-negative integers")
        if self.reasoningTokens is not None and self.reasoningTokens > self.outputTokens:
            raise ValueError("reasoningTokens cannot exceed outputTokens")
        known_prompt = self.inputTokens + (self.cacheReadTokens or 0) + (self.cacheWriteTokens or 0)
        known_total = known_prompt + self.outputTokens
        if known_total > MAX_SAFE_INTEGER:
            raise ValueError("usage aggregate exceeds the safe integer range")
        if self.totalTokens is not None:
            exact_prompt = self.totalTokens - self.outputTokens
            if exact_prompt < known_prompt:
                raise ValueError("totalTokens cannot be smaller than the known disjoint counts")
            if (
                self.cacheReadTokens is not None
                and self.cacheWriteTokens is not None
                and exact_prompt != known_prompt
            ):
                raise ValueError("totalTokens must equal all known disjoint counts")
        return self


# This is the pinned upstream KNOWN_SESSION_EVENT_TYPES vocabulary, reduced to
# zero-tool, zero-subagent lifecycle events. Future events fail closed.
SAFE_SESSION_EVENT_TYPES = frozenset(
    {
        "agent/inbox/spliced",
        "assistant/chunk",
        "assistant/message",
        "model/selection",
        "request/context",
        "request/header",
        "session/title",
        "step/end",
        "step/start",
        "turn/end",
        "turn/start",
        "user/message",
    }
)
SURFACE_EVENT_TYPES = frozenset({"user/message", "assistant/message", "tool/result"})


class SessionEvent(WireModel):
    type: str
    seq: int
    time: int
    data: dict[str, Any] | None = None
    sourceEventSeqs: list[int] | None = None
    surfaceOp: str | dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_event(self) -> SessionEvent:
        if self.type not in SAFE_SESSION_EVENT_TYPES:
            raise ValueError(f"event type is not admitted: {self.type}")
        if not _nonnegative_int(self.seq) or not _nonnegative_int(self.time):
            raise ValueError("event seq and time must be non-negative safe integers")
        if self.data is None:
            raise ValueError(f"{self.type} requires data")
        data = self.data
        if self.sourceEventSeqs is not None:
            if self.type not in SURFACE_EVENT_TYPES:
                raise ValueError("sourceEventSeqs is only valid on surface events")
            if any(not _nonnegative_int(value) for value in self.sourceEventSeqs):
                raise ValueError("sourceEventSeqs must be non-negative integers")
            if len(set(self.sourceEventSeqs)) != len(self.sourceEventSeqs):
                raise ValueError("sourceEventSeqs must not contain duplicates")
            if any(value >= self.seq for value in self.sourceEventSeqs):
                raise ValueError("sourceEventSeqs must reference earlier events")
        if self.type in {"user/message", "assistant/message"}:
            if self.surfaceOp is None:
                raise ValueError(f"{self.type} requires surfaceOp append")
            _validate_surface_op(self.surfaceOp)
        elif self.surfaceOp is not None:
            if self.type not in SURFACE_EVENT_TYPES:
                raise ValueError("surfaceOp is only valid on surface events")
            _validate_surface_op(self.surfaceOp)
        if self.type == "assistant/message":
            _validate_assistant_message(data)
        elif self.type == "assistant/chunk":
            _validate_assistant_chunk(data)
        elif self.type == "turn/end":
            _validate_turn_end(data)
        elif self.type == "agent/inbox/spliced":
            _validate_inbox_splice(data)
        elif self.type in {
            "turn/start",
            "step/start",
            "step/end",
            "user/message",
            "request/context",
            "request/header",
            "model/selection",
            "session/title",
        }:
            _validate_typed_event_data(self.type, data)
            if self.type == "session/title" and any(
                value >= self.seq for value in data["messageSeqs"]
            ):
                raise ValueError("session/title messageSeqs must reference earlier events")
        return self


class SessionEventNotification(WireModel):
    sessionId: str
    event: SessionEvent


class SessionStatusNotification(WireModel):
    sessionId: str
    status: Literal["idle", "running"]


class SubagentStartedNotification(WireModel):
    parentSessionId: str
    childSessionId: str


class SubagentFinishedNotification(WireModel):
    provider: str
    agentId: str
    parentSessionId: str
    childSessionId: str
    status: Literal["ok", "error"]
    stopReason: str
    lastAssistantMessage: list[ContentBlock] | None = None


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SAFE_INTEGER


def _bounded_utf8_text(value: Any, max_bytes: int) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return len(value.encode("utf-8")) <= max_bytes
    except UnicodeEncodeError:
        return False


def _validate_surface_op(value: str | dict[str, Any]) -> None:
    if value == "append":
        return
    # H1.5 only persists append-only surface projections.  Replacement
    # ranges are an editor/runtime capability and are intentionally not
    # admitted until their provenance and bounds have a reviewed contract.
    raise ValueError("surfaceOp must be append")


def _validate_assistant_message(data: dict[str, Any]) -> None:
    allowed = {"turn", "step", "message", "usage", "interrupted"}
    if (
        set(data) - allowed
        or not _nonnegative_int(data.get("turn"))
        or not _nonnegative_int(data.get("step"))
        or not isinstance(data.get("message"), dict)
    ):
        raise ValueError("malformed assistant/message data")
    message = data["message"]
    if set(message) - {"id", "role", "content", "source"}:
        raise ValueError("unknown assistant message field")
    if (
        not _bounded_utf8_text(message.get("id"), MAX_IDENTIFIER_BYTES)
        or message.get("role") != "assistant"
    ):
        raise ValueError("malformed assistant message identity")
    content = message.get("content")
    if not isinstance(content, list):
        raise ValueError("assistant message content must be an array")
    try:
        SAFE_CONTENT_BLOCK_ADAPTER.validate_python(content)
    except Exception as error:
        raise ValueError("assistant output allows only text and reasoning blocks") from error
    source = message.get("source")
    if not isinstance(source, dict) or set(source) - {"kind", "provider", "model"}:
        raise ValueError("malformed assistant message source")
    if (
        source.get("kind") != "model"
        or not isinstance(source.get("provider"), str)
        or not isinstance(source.get("model"), str)
        or not source["provider"]
        or not source["model"]
    ):
        raise ValueError("malformed assistant message provenance")
    if "interrupted" in data and data["interrupted"] is not True:
        raise ValueError("interrupted must be true when present")
    usage = data.get("usage")
    if usage is not None:
        try:
            TokenUsage.model_validate(usage)
        except Exception as error:
            raise ValueError("malformed assistant usage") from error


def _validate_assistant_chunk(data: dict[str, Any]) -> None:
    if (
        set(data) != {"turn", "step", "chunk"}
        or not _nonnegative_int(data.get("turn"))
        or not _nonnegative_int(data.get("step"))
        or not isinstance(data.get("chunk"), dict)
    ):
        raise ValueError("malformed assistant/chunk data")
    chunk = data["chunk"]
    kind = chunk.get("type")
    if kind in {"text-delta", "reasoning-delta"}:
        if set(chunk) != {"type", "index", "text"} or not _nonnegative_int(chunk.get("index")):
            raise ValueError("malformed assistant text chunk")
        if not isinstance(chunk.get("text"), str):
            raise ValueError("assistant chunk text must be a string")
        return
    if kind == "block-start":
        if set(chunk) != {"type", "index", "blockType"} or not _nonnegative_int(chunk.get("index")):
            raise ValueError("malformed assistant block-start")
        if chunk.get("blockType") not in {"text", "reasoning"}:
            raise ValueError("assistant chunk block type is not admitted")
        return
    if kind == "block-end":
        if set(chunk) != {"type", "index", "block"} or not _nonnegative_int(chunk.get("index")):
            raise ValueError("malformed assistant block-end")
        block = chunk.get("block")
        try:
            if not isinstance(block, dict) or block.get("type") not in {"text", "reasoning"}:
                raise ValueError
            SAFE_CONTENT_BLOCK_ADAPTER.validate_python([block])
        except Exception as error:
            raise ValueError("assistant chunk block is not admitted") from error
        return
    if kind == "usage":
        usage = chunk.get("usage")
        if set(chunk) != {"type", "usage"} or not isinstance(usage, dict):
            raise ValueError("malformed assistant usage chunk")
        try:
            TokenUsage.model_validate(usage)
        except Exception as error:
            raise ValueError("malformed assistant usage chunk") from error
        return
    if kind == "finish":
        reason = chunk.get("reason")
        # Adapter-private replay metadata is deliberately outside the H1.5
        # boundary.  It is opaque to DSH and may contain provider payloads we
        # have neither bounded nor approved for persistence.
        if set(chunk) != {"type", "reason"} or not isinstance(reason, dict):
            raise ValueError("malformed assistant finish chunk")
        if reason.get("kind") not in {"stop", "max-tokens", "error", "aborted"}:
            raise ValueError("assistant finish reason is not admitted")
        if reason.get("kind") in {"stop", "max-tokens"} and set(reason) != {"kind"}:
            raise ValueError("malformed assistant finish reason")
        if reason.get("kind") in {"error", "aborted"}:
            if set(reason) != {"kind", "failure"}:
                raise ValueError("malformed assistant finish reason")
            _validate_llm_failure(reason.get("failure"))
        return
    raise ValueError("assistant chunk type is not admitted")


def _validate_turn_end(data: dict[str, Any]) -> None:
    if set(data) != {"turn", "reason"} or not _nonnegative_int(data.get("turn")):
        raise ValueError("malformed turn/end data")
    reason = data["reason"]
    if not isinstance(reason, dict) or set(reason) - {"kind", "reason", "error"}:
        raise ValueError("malformed turn/end reason")
    kind = reason.get("kind")
    if kind not in {"completed", "blocked", "error", "max-tokens", "interrupted", "aborted"}:
        raise ValueError("unknown turn/end reason")
    if kind in {"completed", "blocked", "max-tokens", "interrupted"} and set(reason) != {"kind"}:
        raise ValueError("unexpected turn/end reason fields")
    if kind == "error":
        failure = reason.get("error")
        if set(reason) != {"kind", "error"} or not isinstance(failure, dict):
            raise ValueError("malformed turn/end error")
        try:
            _validate_llm_failure(failure)
        except ValueError as error:
            raise ValueError("malformed turn/end error") from error
    if kind == "aborted":
        nested = reason.get("reason")
        if set(reason) != {"kind", "reason"} or not isinstance(nested, dict):
            raise ValueError("malformed abort reason")
        if nested.get("kind") not in {"user", "parent", "hook", "disposed", "legacy"}:
            raise ValueError("unknown abort reason")
        if set(nested) - {"kind", "reason"}:
            raise ValueError("unexpected abort reason fields")
        if nested["kind"] == "hook" and not isinstance(nested.get("reason"), str):
            raise ValueError("hook abort reason must have text")
        if nested["kind"] != "hook" and set(nested) != {"kind"}:
            raise ValueError("unexpected abort reason fields")


def _validate_inbox_message(message: Any) -> None:
    if not isinstance(message, dict) or set(message) - {"id", "role", "content", "source"}:
        raise ValueError("malformed inbox message")
    if (
        not _bounded_utf8_text(message.get("id"), MAX_IDENTIFIER_BYTES)
        or message.get("role") != "user"
        or not isinstance(message.get("content"), list)
        or message.get("source") != {"kind": "user"}
    ):
        raise ValueError("malformed inbox message")
    try:
        blocks = SAFE_CONTENT_BLOCK_ADAPTER.validate_python(message["content"])
    except Exception as error:
        raise ValueError("inbox message allows only text and reasoning blocks") from error
    text_blocks = [block for block in blocks if isinstance(block, TextBlock)]
    if not text_blocks or any(not block.text.strip() for block in text_blocks):
        raise ValueError("user message text must be non-empty")


def _validate_inbox_splice(data: dict[str, Any]) -> None:
    if set(data) - {"target", "start", "removedCount", "inserted", "outcome"}:
        raise ValueError("unknown inbox splice field")
    if data.get("target") not in {"next-turn", "next-step"} or not _nonnegative_int(
        data.get("start")
    ):
        raise ValueError("malformed inbox splice")
    if "removedCount" in data and not _nonnegative_int(data["removedCount"]):
        raise ValueError("malformed inbox removedCount")
    if "outcome" in data and data["outcome"] != "canceled":
        raise ValueError("malformed inbox outcome")
    inserted = data.get("inserted")
    if not isinstance(inserted, list):
        raise ValueError("inbox splice inserted must be an array")
    for message in inserted:
        _validate_inbox_message(message)


def _validate_llm_failure(failure: Any) -> None:
    if not isinstance(failure, dict):
        raise ValueError("LlmFailure must be an object")
    allowed = {"message", "code", "status", "providerRetryAfterMs", "requestId"}
    if (
        set(failure) - allowed
        or not isinstance(failure.get("message"), str)
        or not failure["message"]
    ):
        raise ValueError("malformed LlmFailure")
    if not _bounded_utf8_text(failure.get("code"), MAX_IDENTIFIER_BYTES):
        raise ValueError("malformed LlmFailure")
    for key in ("status", "providerRetryAfterMs"):
        if key in failure and not _nonnegative_int(failure[key]):
            raise ValueError("malformed LlmFailure integer")
    if "requestId" in failure and not _bounded_utf8_text(
        failure["requestId"], MAX_IDENTIFIER_BYTES
    ):
        raise ValueError("malformed LlmFailure requestId")


def _validate_typed_event_data(event_type: str, data: dict[str, Any] | None) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"{event_type} requires an object")
    if event_type == "turn/start":
        if set(data) != {"turn"} or not _nonnegative_int(data.get("turn")):
            raise ValueError("malformed turn/start")
        return
    if event_type in {"step/start", "step/end"}:
        if (
            set(data) != {"turn", "step"}
            or not _nonnegative_int(data.get("turn"))
            or not _nonnegative_int(data.get("step"))
        ):
            raise ValueError(f"malformed {event_type}")
        return
    if event_type == "user/message":
        if set(data) - {"id", "role", "content", "source"}:
            raise ValueError("malformed user/message")
        _validate_inbox_message(data)
        return
    if event_type == "request/context":
        if (
            set(data) - {"provider", "model", "contextWindow"}
            or not isinstance(data.get("provider"), str)
            or not data["provider"]
            or not isinstance(data.get("model"), str)
            or not data["model"]
        ):
            raise ValueError("malformed request/context")
        if "contextWindow" in data and (
            not _nonnegative_int(data["contextWindow"]) or data["contextWindow"] == 0
        ):
            raise ValueError("malformed request/context capacity")
        return
    if event_type == "model/selection":
        if (
            set(data) - {"provider", "model", "reasoningEffort"}
            or not isinstance(data.get("provider"), str)
            or not data["provider"]
            or not isinstance(data.get("model"), str)
            or not data["model"]
        ):
            raise ValueError("malformed model/selection")
        if "reasoningEffort" in data and (
            not isinstance(data["reasoningEffort"], str) or not data["reasoningEffort"]
        ):
            raise ValueError("malformed model/selection effort")
        return
    if event_type == "session/title":
        if set(data) != {"title", "source", "messageSeqs"}:
            raise ValueError("malformed session/title")
        if not isinstance(data["title"], str) or not data["title"]:
            raise ValueError("malformed session/title title")
        source = data["source"]
        if source != {"kind": "fallback"}:
            raise ValueError("session/title source must be fallback")
        message_seqs = data["messageSeqs"]
        if (
            not isinstance(message_seqs, list)
            or any(not _nonnegative_int(value) for value in message_seqs)
            or len(set(message_seqs)) != len(message_seqs)
        ):
            raise ValueError("malformed session/title messageSeqs")
        return
    # request/header is deliberately reduced to a tool-less call snapshot.
    if set(data) - {"header", "reason", "startsSeries"} or data.get("reason") not in {
        "initial",
        "resume",
        "change",
        "series",
    }:
        raise ValueError("malformed request/header")
    header = data.get("header")
    if not isinstance(header, dict) or set(header) - {"config", "adapterDefaults", "system"}:
        raise ValueError("malformed request/header payload")
    config = header.get("config")
    if not isinstance(config, dict):
        raise ValueError("malformed request/header config")
    config_allowed = {"provider", "model", "reasoningEffort", "temperature", "maxTokens", "stop"}
    if set(config) - config_allowed:
        raise ValueError("malformed request/header config")
    if (
        not isinstance(config.get("provider"), str)
        or not config["provider"]
        or not isinstance(config.get("model"), str)
        or not config["model"]
    ):
        raise ValueError("malformed request/header config identity")
    if "reasoningEffort" in config and (
        not isinstance(config["reasoningEffort"], str) or not config["reasoningEffort"]
    ):
        raise ValueError("malformed request/header reasoning effort")
    if "temperature" in config:
        temperature = config["temperature"]
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or (isinstance(temperature, float) and not math.isfinite(temperature))
        ):
            raise ValueError("malformed request/header temperature")
    if "maxTokens" in config and (
        not _nonnegative_int(config["maxTokens"]) or config["maxTokens"] == 0
    ):
        raise ValueError("malformed request/header maxTokens")
    if "stop" in config and (
        not isinstance(config["stop"], list)
        or any(not isinstance(item, str) for item in config["stop"])
    ):
        raise ValueError("malformed request/header stop")
    defaults = header.get("adapterDefaults")
    if defaults is not None:
        if not isinstance(defaults, dict) or set(defaults) - {"reasoningEffort", "maxTokens"}:
            raise ValueError("malformed request/header adapterDefaults")
        if any(value is not True for value in defaults.values()):
            raise ValueError("malformed request/header adapterDefaults")
        if "reasoningEffort" in defaults and "reasoningEffort" not in config:
            raise ValueError("adapterDefaults reasoningEffort has no config value")
        if "maxTokens" in defaults and "maxTokens" not in config:
            raise ValueError("adapterDefaults maxTokens has no config value")
    if "system" in header and not isinstance(header["system"], str):
        raise ValueError("malformed request/header system")
    if "startsSeries" in data and data["startsSeries"] is not True:
        raise ValueError("malformed request/header series marker")


NOTIFICATION_MODELS: dict[str, type[WireModel]] = {
    "session.event": SessionEventNotification,
    "session.status": SessionStatusNotification,
    "subagent.started": SubagentStartedNotification,
    "subagent.finished": SubagentFinishedNotification,
}
REQUEST_METHODS = {"initialize", "session/prompt", "shutdown"}
NOTIFICATION_METHODS = set(NOTIFICATION_MODELS)


class PromptOutcome(WireModel):
    """Sanitized publishable result after a clean runtime shutdown.

    Raw SDK events may contain the user prompt, system text and model
    reasoning.  They are validation-only Attempt state and deliberately never
    escape through this result type.
    """

    messageId: str
    usage: TokenUsage
    output: list[TextBlock]
    deliveryState: Literal["acknowledged"] = "acknowledged"
