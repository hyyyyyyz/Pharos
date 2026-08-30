"""Contract tests for the bounded per-Attempt DSH gateway."""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast

import pharos.harness.dsh_gateway as dsh_gateway_module
import pytest
from pharos.harness.contracts import AttemptErrorClass, DeliveryState
from pharos.harness.dsh_gateway import (
    CANARY_PROFILE,
    CANARY_ROUTE_KEY,
    DshGatewayError,
    DshGatewayFactory,
    DshKnownFailure,
    DshModelResult,
    DshRuntimeConfig,
)
from pharos.harness.model_gateway import (
    AttemptContext,
    GatewayLifecycleError,
    RuntimeGatewayFactory,
)
from pharos.harness.protocol import PromptOutcome, TextBlock, TokenUsage
from pharos.harness.transport import (
    AttemptTransportConfig,
    HarnessTimeoutError,
    HarnessTurnError,
)
from tests.harness.dsh_runtime_fixture import make_provisioned_runtime

SHA = "a" * 64
NOW_US = 1_700_000_000_000_000


def context(attempt_id: str, **updates: object) -> AttemptContext:
    values: dict[str, object] = {
        "run_id": "run-1",
        "step_id": "step-1",
        "attempt_id": attempt_id,
        "attempt_no": 1,
        "scope_type": "user",
        "scope_id": "owner-1",
        "lease_owner": "worker-1",
        "workflow_key": "harness.canary",
        "workflow_version": 1,
        "workflow_definition_sha256": SHA,
        "definition_binding_sha256": SHA,
        "run_policy_sha256": SHA,
        "role": "canary_dsh_actor@1",
        "runtime_kind": "dsh",
        "role_definition_sha256": SHA,
        "model_profile_identity": CANARY_PROFILE,
        "model_profile_sha256": SHA,
        "model_route_key": CANARY_ROUTE_KEY,
        "model_route_sha256": SHA,
        "usage_source": "system_shared",
        "input_sha256": SHA,
        "deadline_at_us": 1_700_000_001_000_000,
        "provider": "pharos-fake",
        "model": "pharos-fake-canary",
    }
    values.update(updates)
    return AttemptContext(**values)  # type: ignore[arg-type]


class FakeTransport:
    def __init__(self, _config: object, *, fail_close_times: int = 0) -> None:
        self.config = _config
        self.delivery_state = DeliveryState.NOT_STARTED
        self.initialized: list[dict[str, object]] = []
        self.prompts: list[tuple[str, str]] = []
        self.close_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0
        self.fail_close_times = fail_close_times
        self.observer: object = None

    @property
    def pid(self) -> int | None:
        return None

    def start(self) -> int:
        return 1001

    def initialize(self, **kwargs: object) -> None:
        self.initialized.append(kwargs)

    def prompt(self, session_id: str, text: str) -> PromptOutcome:
        if callable(self.observer):
            cast(Any, self.observer)(DeliveryState.SENT)
        self.prompts.append((session_id, text))
        self.delivery_state = DeliveryState.ACKNOWLEDGED
        result = PromptOutcome(
            messageId="message-1",
            usage=TokenUsage(inputTokens=8, outputTokens=7),
            output=[TextBlock(type="text", text='{"ok":true}')],
        )
        if callable(self.observer):
            cast(Any, self.observer)(DeliveryState.ACKNOWLEDGED)
        return result

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls <= self.fail_close_times:
            raise OSError("injected cleanup failure")

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1


def factory(
    tmp_path: Path, transports: list[FakeTransport] | None = None, **kwargs: object
) -> DshGatewayFactory:
    config = DshRuntimeConfig(
        argv=("/bin/true", "--profile", "sdk"),
        cwd=str(tmp_path),
        allow_unpersisted=True,
        **cast(Any, kwargs),
    )

    def make(_config: AttemptTransportConfig) -> FakeTransport:
        transport = FakeTransport(_config)
        if transports is not None:
            transports.append(transport)
        return transport

    return DshGatewayFactory(config, transport_factory=make, clock_us=lambda: NOW_US)


def test_factory_requires_dsh_route_and_immutable_context(tmp_path: Path) -> None:
    gateway = factory(tmp_path)
    assert gateway.durable_runtime is False
    with pytest.raises(ValueError, match="runtime_kind"):
        gateway.open(dataclasses.replace(context("a"), runtime_kind="in_process_fake"))
    with pytest.raises(ValueError, match="route"):
        gateway.open(dataclasses.replace(context("b"), model="other"))
    with pytest.raises(ValueError, match="profile provenance"):
        gateway.open(dataclasses.replace(context("c"), model_profile_identity="wrong@1"))
    with pytest.raises(DshGatewayError, match="deadline has expired"):
        gateway.open(
            dataclasses.replace(context("d"), deadline_at_us=NOW_US - 1)
        )


@pytest.mark.parametrize("profile", ["evil", "../sdk", "sdk/../evil", "/tmp/sdk"])
def test_dsh_profile_is_fixed_and_minimal_path_is_legal(
    tmp_path: Path, profile: str
) -> None:
    config = DshRuntimeConfig(
        argv=("/bin/true", "--profile", "sdk"),
        cwd=str(tmp_path),
        env={"PATH": "/bin"},
        allow_unpersisted=True,
    )
    assert config.profile == "sdk"
    assert config.env == {"PATH": "/bin"}
    with pytest.raises(ValueError, match="authenticated sdk profile"):
        DshRuntimeConfig(
            argv=("/bin/true", "--profile", profile),
            profile=profile,
            cwd=str(tmp_path),
            allow_unpersisted=True,
        )


@pytest.mark.parametrize(
    "name",
    [
        "DEEPSEEK_API_KEY",
        "deepseek-api-key",
        "OPENAI__API__KEY",
        "HTTP_PROXY",
        "https-proxy",
        "NODE_OPTIONS",
        "node-path",
        "PYTHONPATH",
        "LD_PRELOAD",
        "x_authorization_token_x",
    ],
)
def test_dsh_env_rejects_credentials_proxy_and_injection_names(
    tmp_path: Path, name: str
) -> None:
    with pytest.raises(ValueError):
        DshRuntimeConfig(
            argv=("/bin/true", "--profile", "sdk"),
            cwd=str(tmp_path),
            env={name: "attacker-controlled"},
            env_allowlist=frozenset({"PATH", "LANG", "LC_ALL", name}),
            allow_unpersisted=True,
        )


def test_dsh_env_allowlist_cannot_be_expanded_with_harmless_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="allowlist is fixed"):
        DshRuntimeConfig(
            argv=("/bin/true", "--profile", "sdk"),
            cwd=str(tmp_path),
            env={"SAFE_EXTRA": "1"},
            env_allowlist=frozenset({"PATH", "LANG", "LC_ALL", "SAFE_EXTRA"}),
            allow_unpersisted=True,
        )


def test_complete_projects_canonical_json_and_preserves_usage_delivery(tmp_path: Path) -> None:
    transports: list[FakeTransport] = []
    handle = cast(Any, factory(tmp_path, transports).open(context("attempt-1")))
    result = handle.complete({"z": 1, "a": [True, None]})

    assert isinstance(result, DshModelResult)
    assert result.output == {"ok": True}
    assert result.input_tokens == 8
    assert result.output_tokens == 7
    assert result.provider_request_id is None
    assert result.runtime_message_id == "message-1"
    assert result.usage == TokenUsage(inputTokens=8, outputTokens=7)
    assert result.delivery_state is DeliveryState.ACKNOWLEDGED
    assert result.deliveryState == "acknowledged"
    assert transports[0].prompts == [("attempt-1", '{"a":[true,null],"z":1}')]


def test_acknowledged_invalid_json_is_a_known_usage_failure(tmp_path: Path) -> None:
    class InvalidJsonTransport(FakeTransport):
        def prompt(self, session_id: str, text: str) -> PromptOutcome:
            outcome = super().prompt(session_id, text)
            return outcome.model_copy(
                update={"output": [TextBlock(type="text", text="not-json")]}
            )

    config = DshRuntimeConfig(
        argv=("/bin/true", "--profile", "sdk"),
        cwd=str(tmp_path),
        allow_unpersisted=True,
    )
    handle = cast(
        Any,
        DshGatewayFactory(
            config,
            transport_factory=InvalidJsonTransport,
            clock_us=lambda: NOW_US,
        ).open(
            context("attempt-1")
        ),
    )

    with pytest.raises(DshKnownFailure) as captured:
        handle.complete({"ok": True})
    assert captured.value.error_class is AttemptErrorClass.validation
    assert captured.value.result.input_tokens == 8
    assert captured.value.result.output_tokens == 7
    assert captured.value.result.provider_request_id is None
    assert captured.value.result.runtime_message_id == "message-1"
    assert handle.delivery_state is DeliveryState.ACKNOWLEDGED
    handle.close()


def test_acknowledged_usage_above_frozen_max_tokens_is_a_budget_failure(
    tmp_path: Path,
) -> None:
    handle = cast(
        Any,
        factory(tmp_path).open(context("attempt-budget", max_output_tokens=6)),
    )
    with pytest.raises(DshKnownFailure) as captured:
        handle.complete({"ok": True})
    assert captured.value.error_class is AttemptErrorClass.budget
    assert captured.value.result.output_tokens == 7
    assert captured.value.result.runtime_message_id == "message-1"
    assert handle.delivery_state is DeliveryState.ACKNOWLEDGED
    handle.close()


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ({"kind": "max-tokens"}, AttemptErrorClass.budget),
        (
            {"kind": "error", "error": {"message": "x", "code": "MISSING_CREDENTIAL"}},
            AttemptErrorClass.auth,
        ),
        (
            {"kind": "error", "error": {"message": "x", "code": "INVALID_REQUEST"}},
            AttemptErrorClass.configuration,
        ),
        (
            {"kind": "error", "error": {"message": "x", "code": "TIMEOUT"}},
            AttemptErrorClass.timeout,
        ),
        (
            {"kind": "error", "error": {"message": "x", "code": "SERVER"}},
            AttemptErrorClass.provider,
        ),
        ({"kind": "aborted", "reason": {"kind": "user"}}, AttemptErrorClass.cancelled),
        ({"kind": "aborted", "reason": {"kind": "parent"}}, AttemptErrorClass.provider),
    ],
)
def test_known_turn_reason_mapping_never_invents_user_cancellation(
    tmp_path: Path,
    reason: dict[str, object],
    expected: AttemptErrorClass,
) -> None:
    class TurnFailureTransport(FakeTransport):
        def prompt(self, session_id: str, text: str) -> PromptOutcome:
            del session_id, text
            self.delivery_state = DeliveryState.ACKNOWLEDGED
            raise HarnessTurnError(
                reason,
                message_id="message-known-failure",
                usage=TokenUsage(inputTokens=4, outputTokens=3),
                output=(),
            )

    handle = cast(
        Any,
        DshGatewayFactory(
            DshRuntimeConfig(
                argv=("/bin/true", "--profile", "sdk"),
                cwd=str(tmp_path),
                allow_unpersisted=True,
            ),
            transport_factory=TurnFailureTransport,
            clock_us=lambda: NOW_US,
        ).open(context("attempt-known-turn")),
    )

    with pytest.raises(DshKnownFailure) as captured:
        handle.complete({"ok": True})
    assert captured.value.error_class is expected
    assert captured.value.result.input_tokens == 4
    assert captured.value.result.output_tokens == 3
    assert captured.value.result.runtime_message_id == "message-known-failure"
    handle.close()


@pytest.mark.parametrize(
    ("delivery", "expected"),
    [
        (DeliveryState.NOT_STARTED, AttemptErrorClass.timeout),
        (DeliveryState.SENT, AttemptErrorClass.indeterminate),
    ],
)
def test_transport_timeout_classification_preserves_delivery_safety(
    tmp_path: Path,
    delivery: DeliveryState,
    expected: AttemptErrorClass,
) -> None:
    class TimeoutTransport(FakeTransport):
        def initialize(self, **kwargs: object) -> None:
            super().initialize(**kwargs)
            if delivery is DeliveryState.NOT_STARTED:
                raise HarnessTimeoutError("injected pre-delivery timeout")

        def prompt(self, session_id: str, text: str) -> PromptOutcome:
            del session_id, text
            self.delivery_state = DeliveryState.SENT
            error = HarnessTimeoutError("injected post-send timeout")
            error.delivery_state = DeliveryState.SENT
            raise error

    handle = cast(
        Any,
        DshGatewayFactory(
            DshRuntimeConfig(
                argv=("/bin/true", "--profile", "sdk"),
                cwd=str(tmp_path),
                allow_unpersisted=True,
            ),
            transport_factory=TimeoutTransport,
            clock_us=lambda: NOW_US,
        ).open(context("attempt-timeout")),
    )
    with pytest.raises(DshGatewayError) as captured:
        handle.complete({"ok": True})
    assert captured.value.error_class is expected
    assert handle.delivery_state is delivery
    handle.close()


@pytest.mark.parametrize("payload", [{"value": float("nan")}, {1: "non-string-key"}])
def test_complete_rejects_non_strict_payload_without_transport_call(
    tmp_path: Path, payload: dict
) -> None:
    transports: list[FakeTransport] = []
    handle = cast(Any, factory(tmp_path, transports).open(context("attempt-1")))
    with pytest.raises((TypeError, DshGatewayError)):
        handle.complete(payload)
    assert transports[0].initialized == []
    handle.close()


def test_cancel_is_attempt_local_and_close_is_idempotent(tmp_path: Path) -> None:
    transports: list[FakeTransport] = []
    gateway = factory(tmp_path, transports)
    first = cast(Any, gateway.open(context("attempt-1")))
    second = cast(Any, gateway.open(context("attempt-2")))
    first.cancel()
    assert transports[0].close_calls == 1
    assert first.state == "cancelled"
    with pytest.raises(GatewayLifecycleError):
        first.complete({})
    second.complete({"attempt": 2})
    assert transports[1].prompts[0][0] == "attempt-2"
    second.close()
    second.close()
    assert transports[1].close_calls == 1


def test_cancel_in_flight_escalates_to_kill_and_leaves_reap_to_completion_owner(
    tmp_path: Path,
) -> None:
    entered = Event()
    release = Event()
    transports: list[FakeTransport] = []

    class BlockingTransport(FakeTransport):
        def prompt(self, session_id: str, text: str) -> PromptOutcome:
            entered.set()
            assert release.wait(2)
            return super().prompt(session_id, text)

    def make(config: AttemptTransportConfig) -> FakeTransport:
        transport = BlockingTransport(config)
        transports.append(transport)
        return transport

    config = DshRuntimeConfig(
        argv=("/bin/true", "--profile", "sdk"),
        cwd=str(tmp_path),
        allow_unpersisted=True,
        term_timeout_seconds=0.05,
    )
    handle = cast(
        Any,
        DshGatewayFactory(
            config,
            transport_factory=make,
            clock_us=lambda: NOW_US,
        ).open(context("attempt-1")),
    )
    errors: list[BaseException] = []

    def complete() -> None:
        try:
            handle.complete({"ok": True})
        except BaseException as error:  # noqa: BLE001 - assert the lifecycle winner
            errors.append(error)

    worker = Thread(target=complete)
    worker.start()
    assert entered.wait(2)
    handle.cancel()
    assert transports[0].terminate_calls == 1
    assert transports[0].close_calls == 0
    deadline = time.monotonic() + 1.0
    while transports[0].kill_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert transports[0].kill_calls == 1
    release.set()
    worker.join(2)
    assert not worker.is_alive()
    assert isinstance(errors[0], GatewayLifecycleError)
    handle.close()
    assert transports[0].close_calls == 1


def test_cancel_after_ack_cannot_be_overwritten_by_late_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered_parse = Event()
    release_parse = Event()
    original_parse = dsh_gateway_module._parse_strict_json

    def blocked_parse(value: str) -> object:
        entered_parse.set()
        assert release_parse.wait(2)
        return original_parse(value)

    monkeypatch.setattr(dsh_gateway_module, "_parse_strict_json", blocked_parse)
    transports: list[FakeTransport] = []
    handle = cast(Any, factory(tmp_path, transports).open(context("attempt-race")))
    errors: list[BaseException] = []

    def complete() -> None:
        try:
            handle.complete({"ok": True})
        except BaseException as error:  # noqa: BLE001 - assert the lifecycle winner
            errors.append(error)

    worker = Thread(target=complete)
    worker.start()
    assert entered_parse.wait(2)
    assert handle.delivery_state is DeliveryState.ACKNOWLEDGED
    handle.cancel()
    release_parse.set()
    worker.join(2)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], GatewayLifecycleError)
    assert handle.state == "cancelled"
    assert handle.usage == TokenUsage(inputTokens=8, outputTokens=7)
    handle.close()


def test_cleanup_failure_is_observable_and_retryable(tmp_path: Path) -> None:
    transports: list[FakeTransport] = []
    config = DshRuntimeConfig(
        argv=("/bin/true", "--profile", "sdk"), cwd=str(tmp_path), allow_unpersisted=True
    )

    def make(_config: AttemptTransportConfig) -> FakeTransport:
        transport = FakeTransport(_config, fail_close_times=1)
        transports.append(transport)
        return transport

    handle = cast(
        Any,
        DshGatewayFactory(
            config,
            transport_factory=make,
            clock_us=lambda: NOW_US,
        ).open(context("attempt-1")),
    )
    handle.complete({"ok": True})
    private_dir = handle.private_dir
    with pytest.raises(OSError):
        handle.close()
    assert handle.cleanup_error is not None
    assert handle.reaped_child_pid is None
    assert private_dir.exists()
    with pytest.raises(OSError):
        handle.close()
    handle.retry_cleanup()
    assert handle.cleanup_error is None
    assert handle.reaped_child_pid == 1001
    assert not private_dir.exists()
    assert transports[0].close_calls == 2


def test_each_attempt_gets_a_private_0700_directory_and_private_environment(
    tmp_path: Path,
) -> None:
    configs: list[object] = []

    def make(config: AttemptTransportConfig) -> FakeTransport:
        configs.append(config)
        return FakeTransport(config)

    gateway = DshGatewayFactory(
        DshRuntimeConfig(
            argv=("/bin/true", "--profile", "sdk"),
            cwd=str(tmp_path),
            env={"LANG": "C"},
            allow_unpersisted=True,
        ),
        transport_factory=make,
        clock_us=lambda: NOW_US,
    )
    first = cast(Any, gateway.open(context("attempt-1")))
    second = cast(Any, gateway.open(context("attempt-2")))
    assert first.private_dir != second.private_dir
    assert first.private_dir.stat().st_mode & 0o777 == 0o700
    assert second.private_dir.stat().st_mode & 0o777 == 0o700
    assert first.transport.config.env["HOME"].startswith(str(first.private_dir))  # type: ignore[attr-defined]
    assert first.transport.config.env["DSH_HOME"].startswith(str(first.private_dir))  # type: ignore[attr-defined]
    assert first.transport.config.env["TMPDIR"] == str(first.private_dir / "tmp")  # type: ignore[attr-defined]
    assert first.transport.config.env["TMP"] == str(first.private_dir / "tmp")  # type: ignore[attr-defined]
    assert first.transport.config.env["TEMP"] == str(first.private_dir / "tmp")  # type: ignore[attr-defined]
    assert first.transport.config.env["NODE_ENV"] == "production"  # type: ignore[attr-defined]
    assert first.transport.config.env["DSH_TELEMETRY_DISABLED"] == "1"  # type: ignore[attr-defined]
    assert (first.private_dir / "tmp").stat().st_mode & 0o777 == 0o700
    assert "PATH" not in first.transport.config.env  # type: ignore[attr-defined]
    first.close()
    second.close()


def test_durable_factory_forces_real_transport_and_private_authenticated_patch(
    tmp_path: Path,
) -> None:
    runtime = make_provisioned_runtime(tmp_path)

    class Persistence:
        def reserve_launch(self, _context: AttemptContext, _launch: object) -> None:
            return None

        def attach_pid(self, _context: AttemptContext, _pid: int) -> None:
            return None

        def observe_delivery(
            self, _context: AttemptContext, _state: DeliveryState
        ) -> None:
            return None

    config = DshRuntimeConfig(
        argv=(
            str(runtime["node"]),
            str(runtime["cli"]),
            "--profile",
            "sdk",
            "--patch",
            str(runtime["patch"]),
        ),
        cwd=str(tmp_path),
        patch=str(runtime["patch"]),
        prepared_dsh_home=str(runtime["template"]),
        runtime_manifest=str(runtime["manifest"]),
        upstream_commit=runtime["upstream_commit"],
        runtime_hash=runtime["runtime_hash"],
        profile_hash=runtime["profile_hash"],
        policy_hash=runtime["policy_hash"],
        expected_model_profile_sha256=SHA,
        expected_model_route_sha256=SHA,
    )
    with pytest.raises(ValueError, match="sealed AttemptTransport"):
        DshGatewayFactory(
            config,
            transport_factory=FakeTransport,
            persistence=Persistence(),
            clock_us=lambda: NOW_US,
        )
    gateway = DshGatewayFactory(
        config,
        persistence=Persistence(),
        clock_us=lambda: NOW_US,
    )
    assert gateway.durable_runtime is True
    handle = cast(Any, gateway.open(context("attempt-durable")))
    private_patch = Path(handle.transport.config.argv[-1])
    original = private_patch.read_bytes()
    assert private_patch == handle.private_dir / "dsh-home/pharos-safe.cordis.patch.yml"
    runtime["patch"].chmod(0o644)
    runtime["patch"].write_text("- id: tool-bash\n  disabled: false\n", encoding="utf-8")
    assert private_patch.read_bytes() == original
    assert handle.transport.config.argv[-1] == str(private_patch)
    handle.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda argv, _patch: (*argv, "--help"),
        lambda argv, patch: (*argv[:4], f"--patch={patch}"),
        lambda argv, patch: (argv[0], argv[1], "--patch", patch, "--profile", "sdk"),
    ],
)
def test_production_runtime_rejects_unsealed_argv_shapes(
    tmp_path: Path,
    mutate: Any,
) -> None:
    runtime = make_provisioned_runtime(tmp_path)
    canonical = (
        str(runtime["node"]),
        str(runtime["cli"]),
        "--profile",
        "sdk",
        "--patch",
        str(runtime["patch"]),
    )
    with pytest.raises(ValueError, match="production argv|--patch"):
        DshRuntimeConfig(
            argv=mutate(canonical, str(runtime["patch"])),
            cwd=str(tmp_path),
            patch=str(runtime["patch"]),
            prepared_dsh_home=str(runtime["template"]),
            runtime_manifest=str(runtime["manifest"]),
            upstream_commit=runtime["upstream_commit"],
            runtime_hash=runtime["runtime_hash"],
            profile_hash=runtime["profile_hash"],
            policy_hash=runtime["policy_hash"],
            expected_model_profile_sha256=SHA,
            expected_model_route_sha256=SHA,
        )


def test_durable_factory_reserves_attaches_and_observes_delivery(tmp_path: Path) -> None:
    runtime = make_provisioned_runtime(tmp_path)
    events: list[tuple[str, object]] = []

    class Persistence:
        def reserve_launch(self, _context: AttemptContext, launch: object) -> None:
            events.append(("reserve", launch))

        def attach_pid(self, _context: AttemptContext, pid: int) -> None:
            events.append(("attach", pid))

        def observe_delivery(self, _context: AttemptContext, state: DeliveryState) -> None:
            events.append(("delivery", state))

    transports: list[FakeTransport] = []

    def make(config: AttemptTransportConfig, *, delivery_observer: object = None) -> FakeTransport:
        transport = FakeTransport(config)
        transport.observer = delivery_observer
        transports.append(transport)
        return transport

    config = DshRuntimeConfig(
        argv=(
            str(runtime["node"]),
            str(runtime["cli"]),
            "--profile",
            "sdk",
            "--patch",
            str(runtime["patch"]),
        ),
        cwd=str(tmp_path),
        patch=str(runtime["patch"]),
        prepared_dsh_home=str(runtime["template"]),
        runtime_manifest=str(runtime["manifest"]),
        upstream_commit=runtime["upstream_commit"],
        runtime_hash=runtime["runtime_hash"],
        profile_hash=runtime["profile_hash"],
        policy_hash=runtime["policy_hash"],
        expected_model_profile_sha256=SHA,
        expected_model_route_sha256=SHA,
        allow_unpersisted=True,
    )
    gateway = DshGatewayFactory(
        config,
        transport_factory=make,
        persistence=Persistence(),
        clock_us=lambda: NOW_US,
    )
    assert gateway.durable_runtime is False
    handle = cast(Any, gateway.open(context("attempt-1")))
    private_patch = Path(handle.transport.config.argv[-1])
    assert private_patch == handle.private_dir / "dsh-home/pharos-safe.cordis.patch.yml"
    assert private_patch.read_bytes() == runtime["patch"].read_bytes()
    assert private_patch != runtime["patch"]
    authenticated_patch = private_patch.read_bytes()
    runtime["patch"].chmod(0o644)
    runtime["patch"].write_text("- id: tool-bash\n  disabled: false\n", encoding="utf-8")
    assert private_patch.read_bytes() == authenticated_patch
    assert handle.transport.config.argv[-1] == str(private_patch)
    handle.complete({"ok": True})
    assert [item[0] for item in events] == ["reserve", "attach", "delivery", "delivery"]
    assert events[2:] == [
        ("delivery", DeliveryState.SENT),
        ("delivery", DeliveryState.ACKNOWLEDGED),
    ]
    handle.close()


def test_runtime_router_rechecks_and_freezes_durable_factory_assembly(tmp_path: Path) -> None:
    runtime = make_provisioned_runtime(tmp_path)

    class Persistence:
        def reserve_launch(self, _context: AttemptContext, _launch: object) -> None:
            return None

        def attach_pid(self, _context: AttemptContext, _pid: int) -> None:
            return None

        def observe_delivery(
            self, _context: AttemptContext, _state: DeliveryState
        ) -> None:
            return None

    factory = DshGatewayFactory(
        DshRuntimeConfig(
            argv=(
                str(runtime["node"]),
                str(runtime["cli"]),
                "--profile",
                "sdk",
                "--patch",
                str(runtime["patch"]),
            ),
            cwd=str(tmp_path),
            patch=str(runtime["patch"]),
            prepared_dsh_home=str(runtime["template"]),
            runtime_manifest=str(runtime["manifest"]),
            upstream_commit=runtime["upstream_commit"],
            runtime_hash=runtime["runtime_hash"],
            profile_hash=runtime["profile_hash"],
            policy_hash=runtime["policy_hash"],
            expected_model_profile_sha256=SHA,
            expected_model_route_sha256=SHA,
        ),
        persistence=Persistence(),
        clock_us=lambda: NOW_US,
    )
    router = RuntimeGatewayFactory({"dsh": factory})
    assert router.has_durable_dsh_runtime is True
    with pytest.raises(AttributeError, match="immutable"):
        factory._transport_factory = FakeTransport  # type: ignore[assignment]
    with pytest.raises(AttributeError, match="immutable"):
        factory.config = DshRuntimeConfig(  # type: ignore[misc]
            argv=("/bin/true", "--profile", "sdk"),
            cwd=str(tmp_path),
            allow_unpersisted=True,
        )

    # Even an object-level bypass cannot inherit the router's original
    # admission decision: every DSH open rechecks the concrete assembly.
    object.__setattr__(factory, "_transport_factory", FakeTransport)
    assert router.has_durable_dsh_runtime is False
    with pytest.raises(TypeError, match="no longer durably configured"):
        router.open(context("attempt-mutated"))
