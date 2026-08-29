from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import pytest
from pharos.harness.protocol import SessionEvent, TokenUsage
from pharos.harness.transport import (
    AttemptTransport,
    AttemptTransportConfig,
    HarnessProcessError,
    HarnessProtocolError,
    HarnessTimeoutError,
    HarnessTransportError,
    HarnessTurnError,
)
from pydantic import ValidationError

FIXTURE = Path(__file__).with_name("fixtures") / "fake_dsh_runtime.py"
FAKE_ROUTES = frozenset({("pharos-fake", "fake")})


def make_transport(tmp_path: Path, mode: str = "ok", **limits: int | float) -> AttemptTransport:
    env = {"FAKE_MODE": mode}
    allowed = frozenset(env)
    config = AttemptTransportConfig(
        argv=(sys.executable, "-u", str(FIXTURE), "--profile", "sdk"),
        cwd=str(tmp_path),
        allowed_routes=FAKE_ROUTES,
        env=env,
        env_allowlist=allowed,
        initialize_timeout_seconds=0.75,
        prompt_timeout_seconds=0.75,
        idle_timeout_seconds=0.75,
        shutdown_timeout_seconds=0.75,
        term_timeout_seconds=0.75,
        kill_timeout_seconds=0.75,
        reap_timeout_seconds=1.0,
        **limits,
    )
    return AttemptTransport(config)


def wait_until(predicate: Callable[[], bool], timeout_seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_one_attempt_official_wire_and_shutdown(tmp_path: Path) -> None:
    transport = make_transport(tmp_path)
    assert (
        transport.initialize(provider="pharos-fake", model="fake").serverInfo.name
        == "deepseek-harness-sdk-runtime"
    )
    result = transport.prompt("session-1", "hello")
    assert result.messageId == "m"
    assert result.output[0].text == "hello"
    assert result.usage.inputTokens == 1
    assert result.usage.outputTokens == 1
    assert result.deliveryState == "acknowledged"
    assert transport.process is not None
    assert transport.process.poll() == 0
    assert transport.process.stdin is not None and transport.process.stdin.closed
    assert transport.process.stdout is not None and transport.process.stdout.closed
    assert transport.process.stderr is not None and transport.process.stderr.closed
    transport.shutdown()


@pytest.mark.parametrize(
    "mode",
    [
        "pollution",
        "wrong-session",
        "gap",
        "unknown-status",
        "duplicate",
        "receipt-late",
        "response-before-receipt",
    ],
)
def test_protocol_violations_fail_closed_and_reap(tmp_path: Path, mode: str) -> None:
    transport = make_transport(tmp_path, mode)
    if mode == "pollution":
        with pytest.raises(HarnessProtocolError):
            transport.initialize(provider="pharos-fake", model="fake")
        assert transport.process is not None and transport.process.poll() is not None
        return
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError):
        transport.prompt("session-1", "hello")
    assert transport.process is not None and transport.process.poll() is not None


def test_prompt_rejects_non_text_or_multiple_blocks(tmp_path: Path) -> None:
    transport = make_transport(tmp_path)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError):
        transport.prompt(
            "session-1", [{"type": "tool-call", "id": "x", "name": "n", "arguments": "{}"}]
        )
    assert transport.process is not None and transport.process.poll() is not None


def test_prompt_validation_is_typed_and_does_not_echo_input(tmp_path: Path) -> None:
    transport = make_transport(tmp_path)
    transport.initialize(provider="pharos-fake", model="fake")
    secret = "credential-do-not-echo"
    with pytest.raises(HarnessProtocolError, match="text block|invalid prompt") as error:
        transport.prompt(
            "session-1",
            [{"type": "text", "text": {"secret": secret}}],  # type: ignore[dict-item]
        )
    assert secret not in str(error.value)
    assert transport.process is not None and transport.process.poll() is not None


def test_close_before_spawn_is_terminal(tmp_path: Path) -> None:
    transport = make_transport(tmp_path)
    transport.close()
    with pytest.raises(HarnessTransportError):
        transport.initialize(provider="pharos-fake", model="fake")


def test_config_uses_one_canonical_cwd_and_rejects_profile_variants(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    config = AttemptTransportConfig(
        argv=(sys.executable, "-u", str(FIXTURE), "--profile", "sdk"),
        cwd=str(link),
        allowed_routes=FAKE_ROUTES,
        env={"FAKE_MODE": "ok"},
        env_allowlist=frozenset({"FAKE_MODE"}),
    )
    assert config.cwd == str(real.resolve())
    with pytest.raises(ValueError):
        AttemptTransportConfig(
            argv=(sys.executable, "--profile=sdk"),
            cwd=str(real),
            allowed_routes=FAKE_ROUTES,
            env_allowlist=frozenset(),
        )
    with pytest.raises(ValueError):
        AttemptTransportConfig(
            argv=(sys.executable, "--profile", "sdk", "--profile", "sdk"),
            cwd=str(real),
            allowed_routes=FAKE_ROUTES,
            env_allowlist=frozenset(),
        )


def test_timeout_bool_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AttemptTransportConfig(
            argv=(sys.executable, "--profile", "sdk"),
            cwd=str(tmp_path),
            allowed_routes=FAKE_ROUTES,
            initialize_timeout_seconds=True,
        )


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_deadlines_are_rejected(tmp_path: Path, value: float) -> None:
    with pytest.raises(ValueError, match="deadlines must be positive"):
        AttemptTransportConfig(
            argv=(sys.executable, "--profile", "sdk"),
            cwd=str(tmp_path),
            allowed_routes=FAKE_ROUTES,
            initialize_timeout_seconds=value,
        )


def test_launch_policy_copies_mutable_source_containers(tmp_path: Path) -> None:
    argv = [sys.executable, "-u", str(FIXTURE), "--profile", "sdk"]
    env = {"FAKE_MODE": "ok"}
    allowlist = {"FAKE_MODE"}
    routes = {("pharos-fake", "fake")}
    config = AttemptTransportConfig(
        argv=argv,  # type: ignore[arg-type]
        cwd=str(tmp_path),
        allowed_routes=routes,  # type: ignore[arg-type]
        env=env,
        env_allowlist=allowlist,  # type: ignore[arg-type]
    )
    argv[-1] = "evil"
    env["OPENAI_API_KEY"] = "must-not-cross"
    allowlist.add("OPENAI_API_KEY")
    routes.add(("deepseek-official", "deepseek-chat"))
    assert config.argv[-1] == "sdk"
    assert dict(config.env) == {"FAKE_MODE": "ok"}
    assert config.env_allowlist == frozenset({"FAKE_MODE"})
    assert config.allowed_routes == FAKE_ROUTES


@pytest.mark.parametrize(
    ("provider", "model"),
    [("deepseek-official", "deepseek-chat"), ("pharos-fake", "wrong")],
)
def test_exact_parent_route_fence_rejects_before_spawn(
    tmp_path: Path, provider: str, model: str
) -> None:
    transport = make_transport(tmp_path)
    with pytest.raises(HarnessProtocolError, match="route is not admitted"):
        transport.initialize(provider=provider, model=model)
    assert transport.process is None


def test_prompt_bounds_reject_large_sequences_and_invalid_utf8_then_reap(tmp_path: Path) -> None:
    large_blocks = [{"type": "text", "text": "x"}] * 10_000
    transport = make_transport(tmp_path)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError, match="exactly one text block"):
        transport.prompt("session-1", large_blocks)
    assert transport.process is not None and transport.process.poll() is not None

    invalid_utf8 = make_transport(tmp_path)
    invalid_utf8.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError, match="valid UTF-8"):
        invalid_utf8.prompt("session-1", "bad\ud800text")
    assert invalid_utf8.process is not None and invalid_utf8.process.poll() is not None

    oversized = make_transport(tmp_path, max_frame_bytes=1024, max_event_bytes=512)
    oversized.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError, match="outbound bound"):
        oversized.prompt("session-1", "x" * 1025)
    assert oversized.process is not None and oversized.process.poll() is not None


def test_dynamic_invalid_session_type_is_typed_and_reaped(tmp_path: Path) -> None:
    transport = make_transport(tmp_path)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError, match="invalid prompt parameters"):
        transport.prompt(123, "hello")  # type: ignore[arg-type]
    assert transport.process is not None and transport.process.poll() is not None


def test_sent_prompt_timeout_exposes_delivery_evidence(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "hang-prompt")
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessTimeoutError) as error:
        transport.prompt("session-1", "hello")
    assert error.value.delivery_state == "sent"
    assert transport.delivery_state == "sent"
    assert transport.process is not None and transport.process.poll() is not None


def test_hang_is_bounded_and_early_exit_is_typed(tmp_path: Path) -> None:
    hanging = make_transport(tmp_path, "hang-init")
    with pytest.raises(HarnessTimeoutError):
        hanging.initialize(provider="pharos-fake", model="fake")
    assert hanging.process is not None and hanging.process.poll() is not None

    dead = make_transport(tmp_path, "early-exit")
    with pytest.raises(HarnessProcessError):
        dead.initialize(provider="pharos-fake", model="fake")
    assert dead.process is not None and dead.process.poll() is not None


def test_server_identity_version_is_pinned(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "wrong-server-version")
    with pytest.raises(HarnessProtocolError, match="server identity"):
        transport.initialize(provider="pharos-fake", model="fake")


def test_malformed_initialize_result_is_a_sanitized_transport_error(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "malformed-init-result")
    with pytest.raises(HarnessProtocolError, match="malformed initialize response"):
        transport.initialize(provider="pharos-fake", model="fake")
    assert transport.process is not None and transport.process.poll() is not None


def test_explicit_model_call_configuration_is_frozen(tmp_path: Path) -> None:
    transport = make_transport(tmp_path)
    transport.initialize(
        provider="pharos-fake",
        model="fake",
        reasoning_effort="high",
        max_tokens=64,
    )
    assert transport.prompt("session-1", "hello").output[0].text == "hello"
    transport.shutdown()


@pytest.mark.parametrize("mode", ["wrong-reasoning-effort", "wrong-max-tokens"])
def test_runtime_cannot_change_explicit_model_call_configuration(tmp_path: Path, mode: str) -> None:
    transport = make_transport(tmp_path, mode)
    transport.initialize(
        provider="pharos-fake",
        model="fake",
        reasoning_effort="high",
        max_tokens=64,
    )
    with pytest.raises(HarnessProtocolError, match="reasoning effort|token limit"):
        transport.prompt("session-1", "hello")


@pytest.mark.parametrize("mode", ["duplicate-json-key", "deep-json"])
def test_noncanonical_or_pathological_json_is_bounded_and_reaped(tmp_path: Path, mode: str) -> None:
    transport = make_transport(tmp_path, mode)
    with pytest.raises(HarnessProtocolError, match="invalid JSON|nesting limit"):
        transport.initialize(provider="pharos-fake", model="fake")
    assert transport.process is not None and transport.process.poll() is not None


def test_shutdown_rejects_a_runtime_that_exited_early(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "exit-after-init")
    transport.initialize(provider="pharos-fake", model="fake")
    assert wait_until(
        lambda: transport.process is not None and transport.process.poll() is not None
    )
    with pytest.raises(HarnessProcessError, match="before the shutdown"):
        transport.shutdown()


def test_stderr_bound_is_fail_closed(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "stderr", max_stderr_bytes=32)
    with pytest.raises(HarnessProtocolError):
        transport.initialize(provider="pharos-fake", model="fake")
    assert transport.process is not None and transport.process.poll() is not None


def test_shutdown_rejects_late_notifications(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "shutdown-notification")
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError):
        transport.shutdown()
    assert transport.process is not None and transport.process.poll() is not None


def test_shutdown_rejects_stdout_after_response(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "late-stdout")
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError, match="late stdout"):
        transport.shutdown()
    assert transport.process is not None and transport.process.poll() is not None


def test_prompt_rejects_nonzero_exit_after_valid_shutdown_response(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "nonzero-after-shutdown")
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProcessError, match="nonzero after shutdown") as error:
        transport.prompt("session-1", "hello")
    assert error.value.delivery_state == "acknowledged"
    assert transport.process is not None and transport.process.returncode == 17


def test_prompt_does_not_publish_before_clean_shutdown_and_eof(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "late-event-after-idle")
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError, match="notification received during shutdown"):
        transport.prompt("session-1", "hello")
    assert transport.process is not None and transport.process.poll() is not None


def test_output_bound_is_fail_closed(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "large-output", max_output_bytes=64)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError):
        transport.prompt("session-1", "hello")
    assert transport.process is not None and transport.process.poll() is not None


@pytest.mark.parametrize(
    "mode",
    [
        "wrong-receipt-id",
        "wrong-receipt-content",
        "duplicate-receipt",
        "turn-mismatch",
        "wrong-source",
        "wrong-source-model",
    ],
)
def test_receipt_and_turn_identity_are_strict(tmp_path: Path, mode: str) -> None:
    transport = make_transport(tmp_path, mode)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError):
        transport.prompt("session-1", "hello")


@pytest.mark.parametrize(
    "mode",
    [
        "duplicate-running",
        "duplicate-idle",
        "finish-replay-state",
        "interrupted-completed",
        "empty-request-id",
    ],
)
def test_duplicate_lifecycle_and_unapproved_metadata_fail_closed(tmp_path: Path, mode: str) -> None:
    transport = make_transport(tmp_path, mode)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError):
        transport.prompt("session-1", "hello")
    assert transport.process is not None and transport.process.poll() is not None


@pytest.mark.parametrize(
    ("mode", "reason_kind", "has_usage"),
    [
        ("blocked", "blocked", False),
        ("error", "error", True),
        ("finish-aborted", "error", True),
        ("max-tokens", "max-tokens", True),
        ("interrupted", "interrupted", False),
        ("aborted", "aborted", False),
    ],
)
def test_all_noncompleted_turn_reasons_preserve_sanitized_accounting_evidence(
    tmp_path: Path, mode: str, reason_kind: str, has_usage: bool
) -> None:
    transport = make_transport(tmp_path, mode)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessTurnError) as error:
        transport.prompt("session-1", "hello")
    assert error.value.reason["kind"] == reason_kind
    assert error.value.message_id == "m"
    assert error.value.delivery_state == "acknowledged"
    assert (error.value.usage is not None) is has_usage
    if mode in {"error", "finish-aborted"}:
        assert [block.text for block in error.value.output] == ["hello"]
        assert "message" not in error.value.reason.get("error", {})


@pytest.mark.parametrize("mode", ["assistant-tool", "reasoning-only"])
def test_completed_nontext_output_is_rejected(tmp_path: Path, mode: str) -> None:
    transport = make_transport(tmp_path, mode)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError):
        transport.prompt("session-1", "hello")


@pytest.mark.parametrize("mode", ["unknown-event", "tool-event", "bad-surface"])
def test_unreviewed_event_vocabulary_fails_closed(tmp_path: Path, mode: str) -> None:
    transport = make_transport(tmp_path, mode)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError):
        transport.prompt("session-1", "hello")
    assert transport.process is not None and transport.process.poll() is not None


def test_event_total_bound_is_independent_from_single_event_bound(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, max_event_bytes=512, max_total_event_bytes=512)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError, match="buffer limit"):
        transport.prompt("session-1", "hello")


def test_event_total_bound_counts_raw_whitespace_padded_wire_bytes(tmp_path: Path) -> None:
    limits = {"max_event_bytes": 4096, "max_total_event_bytes": 12_000}
    ordinary = make_transport(tmp_path, **limits)
    ordinary.initialize(provider="pharos-fake", model="fake")
    assert ordinary.prompt("session-1", "hello").output[0].text == "hello"

    padded = make_transport(tmp_path, "padded-events", **limits)
    padded.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError, match="buffer limit"):
        padded.prompt("session-1", "hello")


def test_only_completed_turn_is_success(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "blocked")
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessTurnError) as error:
        transport.prompt("session-1", "hello")
    assert error.value.reason == {"kind": "blocked"}
    assert transport.process is not None and transport.process.poll() is not None


def test_legal_inbox_removal_after_receipt_is_accepted(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "removal")
    transport.initialize(provider="pharos-fake", model="fake")
    result = transport.prompt("session-1", "hello")
    assert result.output[0].text == "hello"
    transport.shutdown()


def test_real_runtime_session_title_shape_is_admitted(tmp_path: Path) -> None:
    transport = make_transport(tmp_path, "session-title")
    transport.initialize(provider="pharos-fake", model="fake")
    result = transport.prompt("session-1", "hello")
    assert result.output[0].text == "hello"
    transport.shutdown()


@pytest.mark.parametrize("mode", ["missing-title", "model-selection"])
def test_reviewed_optional_real_runtime_events_are_admitted(tmp_path: Path, mode: str) -> None:
    transport = make_transport(tmp_path, mode)
    transport.initialize(provider="pharos-fake", model="fake")
    result = transport.prompt("session-1", "hello")
    assert result.output[0].text == "hello"
    transport.shutdown()


@pytest.mark.parametrize("mode", ["chunk-usage-mismatch", "missing-usage"])
def test_completed_usage_is_required_and_consistent(tmp_path: Path, mode: str) -> None:
    transport = make_transport(tmp_path, mode)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError):
        transport.prompt("session-1", "hello")
    assert transport.process is not None and transport.process.poll() is not None


def test_surface_sources_must_reference_earlier_events_and_append_only() -> None:
    with pytest.raises(ValidationError, match="earlier events"):
        SessionEvent.model_validate(
            {
                "type": "assistant/message",
                "seq": 1,
                "time": 0,
                "sourceEventSeqs": [1],
                "surfaceOp": "append",
                "data": {
                    "turn": 0,
                    "step": 0,
                    "message": {
                        "id": "a",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "ok"}],
                        "source": {
                            "kind": "model",
                            "provider": "pharos-fake",
                            "model": "fake",
                        },
                    },
                    "usage": {"inputTokens": 1, "outputTokens": 1},
                },
            }
        )


@pytest.mark.parametrize(
    "mode",
    [
        "missing-removal",
        "missing-context",
        "wrong-header-route",
        "wrong-context-route",
        "duplicate-usage",
        "duplicate-finish",
        "missing-surface",
        "title-future-seq",
        "infinite-temperature",
        "late-event-after-turn-end",
        "surrogate-output",
        "wrong-source-seqs",
        "empty-output",
        "whitespace-output",
    ],
)
def test_real_lifecycle_relational_violations_fail_closed(tmp_path: Path, mode: str) -> None:
    transport = make_transport(tmp_path, mode)
    transport.initialize(provider="pharos-fake", model="fake")
    with pytest.raises(HarnessProtocolError):
        transport.prompt("session-1", "hello")
    assert transport.process is not None and transport.process.poll() is not None


def test_wire_integers_and_usage_are_bounded_to_safe_accounting() -> None:
    with pytest.raises(ValidationError, match="safe integers"):
        SessionEvent.model_validate(
            {
                "type": "turn/start",
                "seq": 0,
                "time": 2**53,
                "data": {"turn": 1},
            }
        )
    with pytest.raises(ValidationError, match="totalTokens"):
        TokenUsage.model_validate({"inputTokens": 3, "outputTokens": 2, "totalTokens": 4})
    with pytest.raises(ValidationError, match="known disjoint counts"):
        TokenUsage.model_validate(
            {
                "inputTokens": 1,
                "cacheReadTokens": 100,
                "outputTokens": 1,
                "totalTokens": 2,
            }
        )
    with pytest.raises(ValidationError, match="must equal"):
        TokenUsage.model_validate(
            {
                "inputTokens": 1,
                "cacheReadTokens": 2,
                "cacheWriteTokens": 3,
                "outputTokens": 1,
                "totalTokens": 8,
            }
        )
    with pytest.raises(ValidationError, match="reasoningTokens"):
        TokenUsage.model_validate({"inputTokens": 1, "outputTokens": 1, "reasoningTokens": 2})


def test_request_header_rejects_nonfinite_numbers_after_valid_json_parsing() -> None:
    with pytest.raises(ValidationError, match="temperature"):
        SessionEvent.model_validate(
            {
                "type": "request/header",
                "seq": 0,
                "time": 0,
                "data": {
                    "header": {
                        "config": {
                            "provider": "pharos-fake",
                            "model": "fake",
                            "temperature": float("inf"),
                        }
                    },
                    "reason": "initial",
                },
            }
        )


def test_every_safe_event_requires_typed_data() -> None:
    with pytest.raises(ValidationError, match="requires data"):
        SessionEvent.model_validate({"type": "turn/start", "seq": 0, "time": 0})
    with pytest.raises(ValidationError, match="surfaceOp must be append"):
        SessionEvent.model_validate(
            {
                "type": "assistant/message",
                "seq": 1,
                "time": 0,
                "surfaceOp": {"op": "replace", "start": 0, "end": 1},
                "data": {
                    "turn": 0,
                    "step": 0,
                    "message": {
                        "id": "a",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "ok"}],
                        "source": {
                            "kind": "model",
                            "provider": "pharos-fake",
                            "model": "fake",
                        },
                    },
                    "usage": {"inputTokens": 1, "outputTokens": 1},
                },
            }
        )


def test_process_group_cleanup_handles_leader_exit(tmp_path: Path) -> None:
    pid_file = tmp_path / "orphan.pid"
    transport = make_transport(tmp_path, "orphan")
    transport.config = replace(
        transport.config,
        env={"FAKE_MODE": "orphan", "ORPHAN_PID_FILE": str(pid_file)},
        env_allowlist=frozenset({"FAKE_MODE", "ORPHAN_PID_FILE"}),
    )
    orphan_pid: int | None = None
    try:
        transport.initialize(provider="pharos-fake", model="fake")
        assert wait_until(pid_file.exists)
        orphan_pid = int(pid_file.read_text())
        with pytest.raises(HarnessTimeoutError):
            transport.shutdown()
        assert not _pid_exists(orphan_pid)
    finally:
        with suppress(HarnessTransportError):
            transport.close()
        if orphan_pid is not None and _pid_exists(orphan_pid):
            with suppress(ProcessLookupError):
                os.kill(orphan_pid, signal.SIGKILL)


def _pid_exists(pid: int) -> bool:
    return bool(
        subprocess.run(
            ["ps", "-p", str(pid), "-o", "pid="], capture_output=True, check=False
        ).stdout.strip()
    )


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("orphan-exit-after-init", HarnessProcessError),
        ("orphan-clean-stdio", HarnessProcessError),
    ],
)
def test_process_group_cleanup_covers_early_leader_and_closed_pipes(
    tmp_path: Path, mode: str, error: type[HarnessTransportError]
) -> None:
    pid_file = tmp_path / f"{mode}.pid"
    transport = make_transport(tmp_path, mode)
    transport.config = replace(
        transport.config,
        env={"FAKE_MODE": mode, "ORPHAN_PID_FILE": str(pid_file)},
        env_allowlist=frozenset({"FAKE_MODE", "ORPHAN_PID_FILE"}),
    )
    orphan_pid: int | None = None
    try:
        transport.initialize(provider="pharos-fake", model="fake")
        assert wait_until(pid_file.exists)
        orphan_pid = int(pid_file.read_text())
        if mode == "orphan-exit-after-init":
            assert wait_until(
                lambda: transport.process is not None and transport.process.poll() is not None
            )
        with pytest.raises(error):
            transport.shutdown()
        assert not _pid_exists(orphan_pid)
    finally:
        with suppress(HarnessTransportError):
            transport.close()
        if orphan_pid is not None and _pid_exists(orphan_pid):
            with suppress(ProcessLookupError):
                os.kill(orphan_pid, signal.SIGKILL)


def test_spawn_does_not_race_on_getpgid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pharos.harness.transport.os.getpgid",
        lambda _pid: (_ for _ in ()).throw(ProcessLookupError()),
    )
    transport = make_transport(tmp_path)
    transport.initialize(provider="pharos-fake", model="fake")
    transport.shutdown()


def test_closed_transport_never_signals_a_stale_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = make_transport(tmp_path)
    transport.initialize(provider="pharos-fake", model="fake")
    transport.prompt("session-1", "hello")
    assert transport._pgid is None
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "pharos.harness.transport.os.killpg",
        lambda pgid, signal_number: calls.append((pgid, signal_number)),
    )
    transport._pgid = transport.pid
    transport.terminate()
    transport.kill()
    assert calls == []


def test_permission_error_cannot_masquerade_as_clean_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = make_transport(tmp_path)
    transport.initialize(provider="pharos-fake", model="fake")
    with monkeypatch.context() as patch:
        patch.setattr(
            "pharos.harness.transport.os.killpg",
            lambda *_args: (_ for _ in ()).throw(PermissionError()),
        )
        with pytest.raises(HarnessProcessError, match="permission denied"):
            transport.kill()
    transport.close()


def test_cleanup_failure_remains_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = make_transport(tmp_path, "hang-init")
    original = transport._terminate_ladder
    calls = 0

    def fail_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HarnessTimeoutError("synthetic cleanup failure")
        original()

    monkeypatch.setattr(transport, "_terminate_ladder", fail_once)
    with pytest.raises(HarnessTimeoutError, match="runtime request deadline") as error:
        transport.initialize(provider="pharos-fake", model="fake")
    assert error.value.cleanup_error_type == "HarnessTimeoutError"
    assert transport._closed is False
    transport.close()
    assert transport._closed is True


def test_env_allowlist_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AttemptTransportConfig(
            argv=(sys.executable, "-u", str(FIXTURE), "--profile", "sdk"),
            cwd=str(tmp_path),
            allowed_routes=FAKE_ROUTES,
            env={"NOT_ALLOWED": "1"},
        )


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        ((sys.executable, "bad\x00arg", "--profile", "sdk"), {}),
        ((sys.executable, "--profile", "sdk"), {"BAD=NAME": "value"}),
    ],
)
def test_launch_values_rejected_by_execve_are_rejected_during_config(
    tmp_path: Path, argv: tuple[str, ...], env: dict[str, str]
) -> None:
    with pytest.raises(ValueError):
        AttemptTransportConfig(
            argv=argv,
            cwd=str(tmp_path),
            allowed_routes=FAKE_ROUTES,
            env=env,
            env_allowlist=frozenset(env),
        )


def test_stdin_write_timeout_keeps_its_timeout_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = make_transport(tmp_path)
    transport._spawn()
    with monkeypatch.context() as patch:

        def blocked_write(*_args: object) -> int:
            raise BlockingIOError

        patch.setattr("pharos.harness.transport.os.write", blocked_write)
        patch.setattr("pharos.harness.transport.select.select", lambda *_args: ([], [], []))
        with pytest.raises(HarnessTimeoutError, match="stdin write"):
            transport._write(
                {"jsonrpc": "2.0", "id": "attempt-1", "method": "shutdown"},
                timeout=0.01,
            )
    transport.kill()
    transport.reap()
