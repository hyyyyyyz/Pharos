"""Deterministic SDK-wire peer used only by transport contract tests.

The successful path mirrors the event order emitted by the pinned official
DSH loader and the out-of-tree ``pharos-fake`` adapter. Named modes each
violate one boundary so tests do not accidentally prove a simplified wire.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import suppress
from typing import Any

mode = os.environ.get("FAKE_MODE", "ok")
orphan_pid_file = os.environ.get("ORPHAN_PID_FILE")
prompt_bytes_file = os.environ.get("PROMPT_BYTES_FILE")
initialize_params: dict[str, Any] = {}

if mode == "early-exit":
    raise SystemExit(17)
if mode == "stderr":
    sys.stderr.write("x" * 2048)
    sys.stderr.flush()


def write(frame: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(frame, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def event(
    session: str,
    seq: int,
    event_type: str,
    data: dict[str, Any] | None = None,
    **extra: object,
) -> None:
    value: dict[str, Any] = {"type": event_type, "seq": seq, "time": 0}
    if data is not None:
        value["data"] = data
    value.update(extra)
    frame = {
        "jsonrpc": "2.0",
        "method": "session.event",
        "params": {"sessionId": session, "event": value},
    }
    if mode in {"padded-events", "infinite-temperature"}:
        encoded = json.dumps(frame, separators=(",", ":"))
        if mode == "padded-events":
            encoded = " " * 1024 + encoded
        elif event_type == "request/header":
            # Valid JSON whose finite Python float overflows during parsing;
            # unlike ``Infinity``, this reaches the typed numeric guard.
            encoded = encoded.replace("Infinity", "1e400")
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()
        return
    write(frame)


def status(session: str, value: str) -> None:
    write(
        {
            "jsonrpc": "2.0",
            "method": "session.status",
            "params": {"sessionId": session, "status": value},
        }
    )


def prompt_response(request_id: object) -> None:
    write({"jsonrpc": "2.0", "id": request_id, "result": {"messageId": "m"}})


def fork_orphan(*, exit_leader: bool, close_stdio: bool = False) -> None:
    child = os.fork()
    if child == 0:
        if close_stdio:
            for descriptor in (0, 1, 2):
                with suppress(OSError):
                    os.close(descriptor)
        time.sleep(60)
        raise SystemExit(0)
    if orphan_pid_file:
        with open(orphan_pid_file, "w", encoding="ascii") as handle:
            handle.write(str(child))
            handle.flush()
    if exit_leader:
        raise SystemExit(0)


def handle_initialize(request_id: object) -> None:
    if mode == "hang-init":
        time.sleep(60)
        return
    if mode == "pollution":
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
    if mode == "duplicate-json-key":
        encoded_id = json.dumps(request_id)
        sys.stdout.write(
            '{"jsonrpc":"2.0","id":' + encoded_id + ',"id":' + encoded_id + ',"result":{}}\n'
        )
        sys.stdout.flush()
        return
    if mode == "deep-json":
        nested = "[" * 1100 + "0" + "]" * 1100
        sys.stdout.write(
            '{"jsonrpc":"2.0","id":'
            + json.dumps(request_id)
            + ',"result":{"nested":'
            + nested
            + "}}\n"
        )
        sys.stdout.flush()
        return
    write(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "serverInfo": {
                    "name": "deepseek-harness-sdk-runtime",
                    "version": (
                        1
                        if mode == "malformed-init-result"
                        else "wrong"
                        if mode == "wrong-server-version"
                        else "0.0.1"
                    ),
                }
            },
        }
    )
    if mode == "orphan":
        fork_orphan(exit_leader=False)
    if mode == "orphan-exit-after-init":
        fork_orphan(exit_leader=True)
    if mode == "orphan-clean-stdio":
        fork_orphan(exit_leader=False, close_stdio=True)
    if mode == "exit-after-init":
        raise SystemExit(0)


def direct_user(message_id: str, text: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "role": "user",
        "content": [{"type": "text", "text": text}],
        "source": {"kind": "user"},
    }


def emit_prompt(request_id: object, frame: dict[str, Any]) -> None:
    session = frame["params"]["sessionId"]
    prompt_text = frame["params"]["contentBlocks"][0]["text"]
    if mode == "hang-prompt":
        time.sleep(60)
        return
    if mode == "wrong-session":
        session = "wrong"

    receipt_id = "wrong" if mode == "wrong-receipt-id" else "m"
    receipt_text = "different" if mode == "wrong-receipt-content" else prompt_text
    receipt_seq = 1 if mode == "gap" else 0
    if mode == "response-before-receipt":
        prompt_response(request_id)
    if mode == "receipt-late":
        status(session, "running")
    event(
        session,
        receipt_seq,
        "agent/inbox/spliced",
        {
            "target": "next-turn",
            "start": 0,
            "inserted": [direct_user(receipt_id, receipt_text)],
        },
    )
    seq = receipt_seq + 1
    if mode == "duplicate-receipt":
        event(
            session,
            seq,
            "agent/inbox/spliced",
            {
                "target": "next-turn",
                "start": 0,
                "inserted": [direct_user("m", prompt_text)],
            },
        )
        seq += 1
    if mode == "unknown-status":
        status(session, "paused")
        return
    if mode != "receipt-late":
        status(session, "running")
    if mode == "duplicate-running":
        status(session, "running")

    if mode in {"unknown-event", "tool-event"}:
        event(
            session,
            seq,
            "future/event" if mode == "unknown-event" else "tool/call",
            (
                {}
                if mode == "unknown-event"
                else {
                    "turn": 1,
                    "step": 1,
                    "callId": "c",
                    "name": "x",
                    "arguments": "{}",
                }
            ),
        )
        return

    event(session, seq, "turn/start", {"turn": 1})
    seq += 1
    if mode != "missing-removal":
        event(
            session,
            seq,
            "agent/inbox/spliced",
            {"target": "next-turn", "start": 0, "removedCount": 1, "inserted": []},
        )
        seq += 1

    # The official server acknowledges enqueueing before the model step ends.
    prompt_response(request_id)
    if mode == "crash-after-receipt":
        raise SystemExit(17)
    if mode == "hang-after-receipt":
        time.sleep(60)

    if mode in {"blocked", "aborted", "interrupted"}:
        reason = (
            {"kind": "aborted", "reason": {"kind": "user"}} if mode == "aborted" else {"kind": mode}
        )
        event(session, seq, "turn/end", {"turn": 1, "reason": reason})
        status(session, "idle")
        return

    event(session, seq, "step/start", {"turn": 1, "step": 1})
    seq += 1
    event(
        session,
        seq,
        "user/message",
        direct_user("m", prompt_text),
        surfaceOp="append",
    )
    user_seq = seq
    seq += 1
    if mode != "missing-title":
        title_seqs = [seq] if mode == "title-future-seq" else [user_seq]
        event(
            session,
            seq,
            "session/title",
            {
                "title": "canary",
                "source": {"kind": "fallback"},
                "messageSeqs": title_seqs,
            },
        )
        seq += 1
    if mode == "model-selection":
        event(
            session,
            seq,
            "model/selection",
            {"provider": "pharos-fake", "model": "fake"},
        )
        seq += 1

    header_provider = "wrong" if mode == "wrong-header-route" else "pharos-fake"
    header_config: dict[str, Any] = {
        "provider": header_provider,
        "model": "fake",
        "maxTokens": (
            int(initialize_params.get("maxTokens", 128)) + 1
            if mode == "wrong-max-tokens"
            else initialize_params.get("maxTokens", 128)
        ),
    }
    if "reasoningEffort" in initialize_params:
        header_config["reasoningEffort"] = (
            "low" if mode == "wrong-reasoning-effort" else initialize_params["reasoningEffort"]
        )
    if mode == "infinite-temperature":
        header_config["temperature"] = 1e400
    header: dict[str, Any] = {"config": header_config}
    if "maxTokens" not in initialize_params:
        header["adapterDefaults"] = {"maxTokens": True}
    event(
        session,
        seq,
        "request/header",
        {
            "header": header,
            "reason": "initial",
        },
    )
    seq += 1
    if mode != "missing-context":
        event(
            session,
            seq,
            "request/context",
            {
                "provider": "wrong" if mode == "wrong-context-route" else "pharos-fake",
                "model": "fake",
            },
        )
        seq += 1

    text = (
        ""
        if mode == "empty-output"
        else "   "
        if mode == "whitespace-output"
        else "\ud800"
        if mode == "surrogate-output"
        else "x" * 2048
        if mode == "large-output"
        else "hello"
    )
    block_type = "reasoning" if mode == "reasoning-only" else "text"
    delta_type = "reasoning-delta" if block_type == "reasoning" else "text-delta"
    chunk_seqs: list[int] = []

    def chunk(value: dict[str, Any]) -> None:
        nonlocal seq
        event(session, seq, "assistant/chunk", {"turn": 1, "step": 1, "chunk": value})
        chunk_seqs.append(seq)
        seq += 1

    chunk({"type": "block-start", "index": 0, "blockType": block_type})
    chunk({"type": delta_type, "index": 0, "text": text})
    chunk({"type": "block-end", "index": 0, "block": {"type": block_type, "text": text}})
    chunk_usage = {
        "inputTokens": 2 if mode == "chunk-usage-mismatch" else 1,
        "outputTokens": 1,
        "totalTokens": 3 if mode == "chunk-usage-mismatch" else 2,
    }
    chunk({"type": "usage", "usage": chunk_usage})
    if mode == "duplicate-usage":
        chunk({"type": "usage", "usage": chunk_usage})

    finish_reason: dict[str, Any] = {"kind": "stop"}
    turn_reason: dict[str, Any] = {"kind": "completed"}
    if mode == "max-tokens":
        finish_reason = {"kind": "max-tokens"}
        turn_reason = {"kind": "max-tokens"}
    elif mode == "error":
        failure = {"message": "fake failure", "code": "fake"}
        finish_reason = {"kind": "error", "failure": failure}
        turn_reason = {"kind": "error", "error": failure}
    elif mode == "finish-aborted":
        failure = {"message": "fake abort", "code": "ABORTED"}
        finish_reason = {"kind": "aborted", "failure": failure}
        turn_reason = {"kind": "error", "error": failure}
    elif mode == "empty-request-id":
        failure = {"message": "fake failure", "code": "fake", "requestId": ""}
        finish_reason = {"kind": "error", "failure": failure}
        turn_reason = {"kind": "error", "error": failure}

    finish: dict[str, Any] = {"type": "finish", "reason": finish_reason}
    if mode == "finish-replay-state":
        finish["replayState"] = {"opaque": "not-admitted"}
    chunk(finish)
    if mode == "duplicate-finish":
        chunk(finish)

    assistant_content: list[dict[str, Any]] = [{"type": block_type, "text": text}]
    if mode == "assistant-tool":
        assistant_content = [{"type": "tool-call", "id": "c", "name": "x", "arguments": "{}"}]
    assistant_usage = {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}
    assistant_extra: dict[str, Any] = {}
    if mode == "interrupted-completed":
        assistant_extra["interrupted"] = True
    if mode != "missing-usage":
        assistant_extra["usage"] = assistant_usage
    assistant_surface: object | None = (
        None
        if mode == "missing-surface"
        else {"op": "replace", "start": 0, "end": 0}
        if mode == "bad-surface"
        else "append"
    )
    extra: dict[str, Any] = {
        "sourceEventSeqs": chunk_seqs[:-1] if mode == "wrong-source-seqs" else chunk_seqs
    }
    if assistant_surface is not None:
        extra["surfaceOp"] = assistant_surface
    if mode not in {"error", "finish-aborted", "empty-request-id"}:
        event(
            session,
            seq,
            "assistant/message",
            {
                "turn": 2 if mode == "turn-mismatch" else 1,
                "step": 1,
                "message": {
                    "id": "a",
                    "role": "assistant",
                    "content": assistant_content,
                    "source": {
                        "kind": "model",
                        "provider": "wrong" if mode == "wrong-source" else "pharos-fake",
                        "model": "wrong" if mode == "wrong-source-model" else "fake",
                    },
                },
                **assistant_extra,
            },
            **extra,
        )
        seq += 1
    event(session, seq, "step/end", {"turn": 1, "step": 1})
    seq += 1
    event(session, seq, "turn/end", {"turn": 1, "reason": turn_reason})
    seq += 1
    if mode == "late-event-after-turn-end":
        event(
            session,
            seq,
            "session/title",
            {"title": "late", "source": {"kind": "fallback"}, "messageSeqs": []},
        )
    status(session, "idle")
    if mode == "late-event-after-idle":
        time.sleep(0.05)
        event(
            session,
            seq,
            "session/title",
            {"title": "too late", "source": {"kind": "fallback"}, "messageSeqs": []},
        )
    if mode == "duplicate-idle":
        status(session, "idle")
    if mode == "duplicate":
        prompt_response(request_id)


for line in sys.stdin:
    if not line.strip():
        continue
    frame = json.loads(line)
    method = frame.get("method")
    request_id = frame.get("id")
    if method == "initialize":
        initialize_params = dict(frame.get("params") or {})
        handle_initialize(request_id)
    elif method == "session/prompt":
        if prompt_bytes_file:
            with open(prompt_bytes_file, "w", encoding="ascii") as handle:
                handle.write(str(len(line.encode("utf-8")) + 1))
        emit_prompt(request_id, frame)
    elif method == "shutdown":
        if mode == "shutdown-notification":
            status("late", "idle")
        write({"jsonrpc": "2.0", "id": request_id, "result": {}})
        if mode == "late-stdout":
            time.sleep(0.02)
            sys.stdout.write("late\\n")
            sys.stdout.flush()
        if mode == "nonzero-after-shutdown":
            raise SystemExit(17)
        raise SystemExit(0)
