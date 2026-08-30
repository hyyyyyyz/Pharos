"""The fail-closed per-Attempt gateway for the DeepSeek Harness runtime.

This module is deliberately a small adapter around :class:`AttemptTransport`.
It owns no workflow state and does not expose any DSH capability other than a
single text prompt.  In particular, a DSH process never inherits the parent
environment and every handle gets a new private runtime directory.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Condition, Lock, Timer
from types import MappingProxyType
from typing import Any, Protocol

from pydantic import ValidationError

from pharos.harness.contracts import AttemptErrorClass, DeliveryState, GatewayError
from pharos.harness.fakes import ModelResult
from pharos.harness.model_gateway import (
    AttemptContext,
    GatewayFactory,
    GatewayHandle,
    GatewayKnownFailure,
    GatewayLifecycleError,
)
from pharos.harness.protocol import PromptOutcome, TokenUsage
from pharos.harness.runtime_manifest import (
    VerifiedRuntimeManifest,
    verify_runtime_manifest,
)
from pharos.harness.transport import (
    AttemptTransport,
    AttemptTransportConfig,
    HarnessTimeoutError,
    HarnessTransportError,
    HarnessTurnError,
)

CANARY_ROUTE = ("pharos-fake", "pharos-fake-canary")
CANARY_PROFILE = "pharos-fake-canary@1"
CANARY_ROUTE_KEY = "pharos-fake-canary-dsh"
CANARY_PROFILE_NAME = "sdk"
CANARY_PROTOCOL = "pharos.dsh.stdio@1"
_DSH_ENV_ALLOWLIST = frozenset({"PATH", "LANG", "LC_ALL"})
# Keep this deny check in addition to the exact allowlist so a future caller
# cannot accidentally turn a newly added variable into a credential or code
# injection channel.
_DANGEROUS_ENV_TOKENS = (
    "apikey",
    "accesstoken",
    "authtoken",
    "authorization",
    "credential",
    "password",
    "privatekey",
    "secret",
    "token",
    "proxy",
    "nodeoptions",
    "nodepath",
    "pythonpath",
    "pythonhome",
    "pythonstartup",
    "ldpreload",
    "ldlibrarypath",
    "dyldinsertlibraries",
    "dyldlibrarypath",
    "perl5lib",
    "rubyopt",
    "javatooleoptions",
)


def _is_dangerous_env_name(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return any(token in normalized for token in _DANGEROUS_ENV_TOKENS)


@dataclass(frozen=True, slots=True)
class DshLaunch:
    """The immutable launch facts reserved before a child is spawned."""

    runtime_session_id: str
    deadline_at: int
    upstream_commit: str
    runtime_hash: str
    profile_hash: str
    policy_hash: str
    protocol_version: str = CANARY_PROTOCOL


class DshPersistence(Protocol):
    """Durable Attempt seam; implementations must be owner/generation fenced."""

    def reserve_launch(self, context: AttemptContext, launch: DshLaunch) -> None: ...

    def attach_pid(self, context: AttemptContext, pid: int) -> None: ...

    def observe_delivery(self, context: AttemptContext, state: DeliveryState) -> bool | None: ...


class DshGatewayError(GatewayError):
    """A malformed or otherwise unusable DSH gateway result."""

    def __init__(
        self,
        message: str,
        *,
        error_class: AttemptErrorClass = AttemptErrorClass.bug,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class


class DshKnownFailure(GatewayKnownFailure):
    """A provider turn ended with validated usage and known classification."""

    def __init__(
        self,
        message: str,
        *,
        error_class: AttemptErrorClass,
        usage: TokenUsage,
        runtime_message_id: str,
        provider_request_id: str | None = None,
    ) -> None:
        self.usage = usage
        super().__init__(
            message,
            error_class=error_class,
            result=ModelResult(
                output=None,
                input_tokens=usage.inputTokens,
                output_tokens=usage.outputTokens,
                provider_request_id=provider_request_id,
                runtime_message_id=runtime_message_id,
            ),
        )


_AUTH_FAILURE_CODES = frozenset(
    {"AUTH", "INVALID_CREDENTIAL", "MISSING_CREDENTIAL", "UNAUTHORIZED"}
)
_CONFIGURATION_FAILURE_CODES = frozenset(
    {
        "CONTEXT_WINDOW_EXCEEDED",
        "INVALID_REQUEST",
        "REQUEST_EXTENSION",
        "UNSUPPORTED_CONTENT",
        "UNSUPPORTED_REASONING_EFFORT",
    }
)
_TIMEOUT_FAILURE_CODES = frozenset({"TIMEOUT"})


def _classify_known_turn(reason: Mapping[str, Any]) -> AttemptErrorClass:
    """Map authenticated DSH reason fields into Pharos's closed taxonomy."""

    kind = reason.get("kind")
    if kind == "max-tokens":
        return AttemptErrorClass.budget
    if kind == "aborted":
        nested = reason.get("reason")
        # Only an explicit user abort is equivalent to a persisted local
        # cancellation. Parent, hook, disposal and legacy aborts are runtime
        # failures and must never be presented as a user decision.
        if isinstance(nested, Mapping) and nested.get("kind") == "user":
            return AttemptErrorClass.cancelled
        return AttemptErrorClass.provider
    if kind != "error":
        return AttemptErrorClass.provider
    failure = reason.get("error")
    if not isinstance(failure, Mapping):
        return AttemptErrorClass.provider
    code = failure.get("code")
    normalized = code.upper() if isinstance(code, str) else ""
    status = failure.get("status")
    if normalized in _AUTH_FAILURE_CODES or status in {401, 403}:
        return AttemptErrorClass.auth
    if normalized in _CONFIGURATION_FAILURE_CODES:
        return AttemptErrorClass.configuration
    if normalized in _TIMEOUT_FAILURE_CODES:
        return AttemptErrorClass.timeout
    return AttemptErrorClass.provider


class _DshHandleState(Enum):
    OPEN = "open"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class DshTransport(Protocol):
    """The intentionally tiny transport seam used by the factory."""

    @property
    def delivery_state(self) -> DeliveryState: ...

    @property
    def pid(self) -> int | None: ...

    def start(self) -> int: ...

    def initialize(
        self,
        *,
        provider: str,
        model: str,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
    ) -> Any: ...

    def prompt(self, session_id: str, text: str) -> PromptOutcome: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def close(self) -> None: ...


TransportFactory = Callable[[AttemptTransportConfig], DshTransport]


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} is not allowed")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _strict_json_text(value: object, *, label: str) -> str:
    """Return canonical JSON, rejecting values Python's encoder coerces."""

    def check(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise DshGatewayError(f"{label} contains a non-string JSON key")
                check(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check(child)
        elif isinstance(item, float) and (item != item or item in {float("inf"), float("-inf")}):
            raise DshGatewayError(f"{label} contains a non-finite number")
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise DshGatewayError(f"{label} contains a non-JSON value")

    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a dict")
    check(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise DshGatewayError(f"{label} is not strict JSON") from error


def _parse_strict_json(text: str) -> Any:
    if not isinstance(text, str) or not text.strip():
        raise DshGatewayError("DSH output must be non-empty JSON text")
    try:
        return json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_pairs,
        )
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as error:
        raise DshGatewayError("DSH output is not strict JSON") from error


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _private_tree_sha256(root: Path) -> str:
    """Hash a private mutable copy without following links or special files."""
    records: list[dict[str, str]] = []
    files = 0
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (
            stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        ):
            raise ValueError("private DSH_HOME contains a link or special file")
        if stat.S_ISDIR(info.st_mode):
            continue
        files += 1
        total_bytes += info.st_size
        if files > 4096 or total_bytes > 16 * 1024 * 1024:
            raise ValueError("private DSH_HOME exceeds its bounded template limit")
        digest = hashlib.sha256()
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                info.st_dev,
                info.st_ino,
            ):
                raise ValueError("private DSH_HOME changed while it was hashed")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        finally:
            os.close(descriptor)
        records.append(
            {"path": path.relative_to(root).as_posix(), "sha256": digest.hexdigest()}
        )
    if files == 0:
        raise ValueError("private DSH_HOME must contain a runtime template")
    return hashlib.sha256(_canonical_json(records)).hexdigest()


def _copy_dsh_home(template: Path, destination: Path) -> str:
    """Copy a bounded template with no-follow reads and return its content hash."""
    if not template.is_dir() or template.is_symlink():
        raise ValueError("prepared_dsh_home must be a real directory")
    files = 0
    total_bytes = 0
    for source in [template, *template.rglob("*")]:
        relative = source.relative_to(template)
        source_info = source.lstat()
        if stat.S_ISLNK(source_info.st_mode) or not (
            stat.S_ISDIR(source_info.st_mode) or stat.S_ISREG(source_info.st_mode)
        ):
            raise ValueError("prepared_dsh_home contains a link or special file")
        target = destination / relative
        if stat.S_ISDIR(source_info.st_mode):
            target.mkdir(mode=0o700, exist_ok=True)
            os.chmod(target, 0o700)
            continue
        files += 1
        size = source_info.st_size
        total_bytes += size
        if files > 4096 or total_bytes > 16 * 1024 * 1024:
            raise ValueError("prepared_dsh_home exceeds its bounded template limit")
        target.parent.mkdir(mode=0o700, exist_ok=True)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                source_info.st_dev,
                source_info.st_ino,
            ):
                raise ValueError("prepared_dsh_home changed while it was copied")
            with os.fdopen(descriptor, "rb", closefd=False) as reader, target.open(
                "xb"
            ) as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
        finally:
            os.close(descriptor)
        os.chmod(target, 0o600)
    if files == 0:
        raise ValueError("prepared_dsh_home must contain a runtime template")
    return _private_tree_sha256(destination)


@dataclass(frozen=True, slots=True)
class DshRuntimeConfig:
    """Immutable, allowlisted launch policy for DSH Attempts.

    ``argv`` is the complete executable invocation, including the one fixed
    profile pair and (when supplied) one fixed absolute patch pair.  It is not
    augmented from a user request.  ``cwd`` is only a parent for per-Attempt
    private directories; the child never runs in this shared directory.
    """

    argv: tuple[str, ...]
    cwd: str = field(default_factory=tempfile.gettempdir)
    profile: str = CANARY_PROFILE_NAME
    patch: str | None = None
    prepared_dsh_home: str | None = None
    runtime_manifest: str | None = None
    allow_unpersisted: bool = False
    allowed_routes: frozenset[tuple[str, str]] = frozenset({CANARY_ROUTE})
    env: Mapping[str, str] = field(default_factory=dict)
    env_allowlist: frozenset[str] = frozenset({"PATH", "LANG", "LC_ALL"})
    expected_server_version: str = "0.0.1"
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    initialize_timeout_seconds: float = 5.0
    prompt_timeout_seconds: float = 30.0
    idle_timeout_seconds: float = 30.0
    shutdown_timeout_seconds: float = 2.0
    term_timeout_seconds: float = 1.0
    kill_timeout_seconds: float = 1.0
    reap_timeout_seconds: float = 2.0
    max_frame_bytes: int = 256 * 1024
    max_buffer_bytes: int = 512 * 1024
    max_json_depth: int = 64
    max_event_bytes: int = 128 * 1024
    max_total_event_bytes: int = 512 * 1024
    max_events: int = 1024
    max_output_bytes: int = 256 * 1024
    max_stderr_bytes: int = 64 * 1024
    delivery_observer_timeout_seconds: float = 1.0
    protocol_version: str = CANARY_PROTOCOL
    runtime_hash: str | None = None
    profile_hash: str | None = None
    policy_hash: str | None = None
    upstream_commit: str | None = None
    expected_model_profile_identity: str | None = CANARY_PROFILE
    expected_model_profile_sha256: str | None = None
    expected_model_route_key: str | None = CANARY_ROUTE_KEY
    expected_model_route_sha256: str | None = None
    expected_usage_source: str = "system_shared"

    def __post_init__(self) -> None:
        argv = tuple(self.argv)
        if not argv or any(
            not isinstance(value, str) or not value or "\x00" in value for value in argv
        ):
            raise ValueError("argv must be a non-empty fixed string tuple")
        object.__setattr__(self, "argv", argv)
        if not os.path.isabs(argv[0]):
            raise ValueError("argv[0] must be an absolute executable path")
        if self.profile != CANARY_PROFILE_NAME:
            raise ValueError("DSH profile must be the authenticated sdk profile")
        profile_indexes = [index for index, value in enumerate(argv) if value == "--profile"]
        if len(profile_indexes) != 1:
            raise ValueError("argv must contain exactly one --profile pair")
        profile_index = profile_indexes[0]
        if profile_index + 1 >= len(argv) or argv[profile_index + 1] != self.profile:
            raise ValueError("argv profile does not match the fixed profile")
        if any(value.startswith("--profile=") for value in argv):
            raise ValueError("--profile must be a separate argv pair")
        if any(value.startswith("--patch=") for value in argv):
            raise ValueError("--patch must be a separate argv pair")

        patch_indexes = [index for index, value in enumerate(argv) if value == "--patch"]
        if self.patch is None:
            if patch_indexes:
                raise ValueError("argv contains a patch but no fixed patch is configured")
        else:
            patch_path = os.path.abspath(self.patch)
            if not os.path.isabs(self.patch) or "\x00" in self.patch:
                raise ValueError("patch must be an absolute path without NUL bytes")
            if len(patch_indexes) != 1 or patch_indexes[0] + 1 >= len(argv):
                raise ValueError("argv must contain exactly one --patch pair")
            if os.path.abspath(argv[patch_indexes[0] + 1]) != patch_path:
                raise ValueError("argv patch does not match the fixed patch")
            object.__setattr__(self, "patch", patch_path)

        if not self.allow_unpersisted:
            # Production receives no caller-controlled app arguments.  The
            # sealed runtime has one executable, one authenticated CLI, and
            # exactly the fixed profile/patch pairs whose bytes are verified
            # below.  Accepting an extra token here would let a future DSH
            # flag silently widen the runtime policy outside the manifest.
            if self.patch is None or len(argv) != 6:
                raise ValueError("production argv must be the sealed six-token invocation")
            expected = (
                argv[0],
                argv[1],
                "--profile",
                self.profile,
                "--patch",
                self.patch,
            )
            if argv != expected:
                raise ValueError("production argv must match the sealed invocation order")

        base = Path(self.cwd)
        if not base.is_absolute() or not base.is_dir():
            raise ValueError("cwd must be an existing absolute directory")
        object.__setattr__(self, "cwd", str(base.resolve()))
        if self.prepared_dsh_home is not None:
            template = Path(self.prepared_dsh_home)
            if not template.is_absolute() or not template.is_dir():
                raise ValueError("prepared_dsh_home must be an existing absolute directory")
            object.__setattr__(self, "prepared_dsh_home", str(template.resolve()))
        elif not self.allow_unpersisted:
            raise ValueError("prepared_dsh_home is required for a production DSH config")
        if self.runtime_manifest is not None:
            manifest = Path(self.runtime_manifest)
            if not manifest.is_absolute() or not manifest.is_file() or manifest.is_symlink():
                raise ValueError("runtime_manifest must be an absolute regular file")
            object.__setattr__(self, "runtime_manifest", str(manifest.resolve()))
        elif not self.allow_unpersisted:
            raise ValueError("runtime_manifest is required for a production DSH config")
        if not isinstance(self.allowed_routes, (frozenset, set, tuple, list)):
            raise ValueError("allowed_routes must be an explicit route set")
        routes = frozenset(tuple(route) for route in self.allowed_routes)
        if not routes or any(
            len(route) != 2
            or any(not isinstance(value, str) or not value or "\x00" in value for value in route)
            for route in routes
        ):
            raise ValueError("allowed_routes must contain provider/model pairs")
        object.__setattr__(self, "allowed_routes", routes)

        env = dict(self.env)
        allowlist = frozenset(self.env_allowlist)
        factory_owned = {
            "HOME",
            "DSH_HOME",
            "TMPDIR",
            "TMP",
            "TEMP",
            "NODE_ENV",
            "DSH_TELEMETRY_DISABLED",
        }
        if factory_owned & set(env):
            raise ValueError("runtime isolation variables are factory-owned")
        if allowlist != _DSH_ENV_ALLOWLIST:
            raise ValueError("DSH env allowlist is fixed and cannot be expanded")
        for key in env:
            if _is_dangerous_env_name(key):
                raise ValueError("DSH env contains a credential or injection variable")
        if set(env) - _DSH_ENV_ALLOWLIST:
            raise ValueError("env contains a non-allowlisted variable")
        if any(
            not isinstance(key, str)
            or not key
            or "=" in key
            or "\x00" in key
            or not isinstance(value, str)
            or "\x00" in value
            for key, value in env.items()
        ):
            raise ValueError("env must contain safe string entries")
        object.__setattr__(self, "env", MappingProxyType(env))
        object.__setattr__(self, "env_allowlist", allowlist)

        if self.protocol_version != CANARY_PROTOCOL:
            raise ValueError("unsupported DSH protocol version")
        if self.expected_usage_source not in {"official", "byok", "system_shared"}:
            raise ValueError("unsupported usage source")
        if self.max_tokens is not None and (
            type(self.max_tokens) is not int or self.max_tokens <= 0
        ):
            raise ValueError("max_tokens must be a positive integer")
        for name in (
            "runtime_hash",
            "profile_hash",
            "policy_hash",
            "expected_model_profile_sha256",
            "expected_model_route_sha256",
        ):
            value = getattr(self, name)
            if value is not None and not _valid_digest(value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if (
            self.upstream_commit is not None
            and re.fullmatch(r"[0-9a-f]{40}", self.upstream_commit) is None
        ):
            raise ValueError("upstream_commit must be a lowercase git SHA-1")
        for name in (
            "initialize_timeout_seconds",
            "prompt_timeout_seconds",
            "idle_timeout_seconds",
            "shutdown_timeout_seconds",
            "term_timeout_seconds",
            "kill_timeout_seconds",
            "reap_timeout_seconds",
            "delivery_observer_timeout_seconds",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if not self.allow_unpersisted:
            self.verify_prepared_runtime()

    def verify_prepared_runtime(self) -> VerifiedRuntimeManifest:
        """Recompute every provisioned artifact against deployment pins."""

        if (
            self.runtime_manifest is None
            or self.prepared_dsh_home is None
            or self.patch is None
            or self.runtime_hash is None
            or self.profile_hash is None
            or self.policy_hash is None
            or self.upstream_commit is None
            or self.expected_model_profile_identity is None
            or self.expected_model_profile_sha256 is None
            or self.expected_model_route_key is None
            or self.expected_model_route_sha256 is None
        ):
            raise ValueError("production DSH runtime provenance is incomplete")
        return verify_runtime_manifest(
            Path(self.runtime_manifest),
            argv=self.argv,
            prepared_dsh_home=Path(self.prepared_dsh_home),
            safe_patch=Path(self.patch),
            expected_manifest_sha256=self.runtime_hash,
            expected_profile_sha256=self.profile_hash,
            expected_policy_sha256=self.policy_hash,
            expected_upstream_commit=self.upstream_commit,
        )


class DshModelResult(ModelResult):
    """A normal ``ModelResult`` plus lossless DSH accounting observations."""

    usage: TokenUsage
    delivery_state: DeliveryState

    @property
    def deliveryState(self) -> str:
        """Camel-case spelling used by the official wire result."""
        return self.delivery_state.value


class DshAttemptHandle:
    """One single-use DSH transport and its private directory."""

    def __init__(
        self,
        context: AttemptContext,
        transport: DshTransport,
        private_dir: Path,
        config: DshRuntimeConfig,
        persistence: DshPersistence | None = None,
    ) -> None:
        self.context = context
        self.transport = transport
        self.private_dir = private_dir
        self.config = config
        self.persistence = persistence
        self._pid: int | None = None
        self._state = _DshHandleState.OPEN
        self._condition = Condition()
        self._delivery_state = DeliveryState.NOT_STARTED
        self._usage: TokenUsage | None = None
        self._cleanup_error: BaseException | None = None
        self._transport_close_done = False
        self._directory_cleanup_done = False
        self._close_count = 0
        self._cancel_escalation: Timer | None = None
        self.provenance = MappingProxyType(
            {
                "attempt_id": context.attempt_id,
                "provider": context.provider,
                "model": context.model,
                "model_profile_identity": context.model_profile_identity,
                "model_profile_sha256": context.model_profile_sha256,
                "model_route_key": context.model_route_key,
                "model_route_sha256": context.model_route_sha256,
                "runtime_hash": config.runtime_hash,
                "profile_hash": config.profile_hash,
                "policy_hash": config.policy_hash,
                "protocol_version": config.protocol_version,
                "upstream_commit": config.upstream_commit,
            }
        )

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def state(self) -> str:
        with self._condition:
            return self._state.value

    @property
    def close_count(self) -> int:
        with self._condition:
            return self._close_count

    @property
    def delivery_state(self) -> DeliveryState:
        with self._condition:
            return self._delivery_state

    @property
    def usage(self) -> TokenUsage | None:
        with self._condition:
            return self._usage

    @property
    def cleanup_error(self) -> BaseException | None:
        with self._condition:
            return self._cleanup_error

    @property
    def reaped_child_pid(self) -> int | None:
        """Return the exact child only after transport cleanup proved reap."""

        with self._condition:
            if self._transport_close_done and self._pid is not None:
                return self._pid
            return None

    def _sync_delivery(self, fallback: DeliveryState | None = None) -> None:
        try:
            value = self.transport.delivery_state
        except (AttributeError, TypeError):
            value = fallback or self._delivery_state
        if isinstance(value, DeliveryState):
            self._delivery_state = value

    def _require_open(self, operation: str) -> None:
        if self._state is not _DshHandleState.OPEN:
            raise GatewayLifecycleError(
                f"cannot {operation} gateway handle in {self._state.value} state"
            )

    def complete(self, payload: dict) -> ModelResult:
        if (
            self.context.workflow_key != "harness.canary"
            or self.context.role != "canary_dsh_actor@1"
        ):
            raise DshGatewayError("DSH gateway only admits the frozen harness canary actor")
        prompt = _strict_json_text(payload, label="gateway payload")
        with self._condition:
            self._require_open("complete")
            self._state = _DshHandleState.COMPLETING
        try:
            kwargs: dict[str, Any] = {
                "provider": self.context.provider,
                "model": self.context.model,
            }
            # Route settings are authenticated in the frozen AttemptContext;
            # config values are only a compatibility fallback for pre-binding
            # test contexts and never override a context field.
            reasoning_effort = (
                self.context.reasoning_effort
                if hasattr(self.context, "reasoning_effort")
                else self.config.reasoning_effort
            )
            max_tokens = (
                self.context.max_output_tokens
                if hasattr(self.context, "max_output_tokens")
                else self.config.max_tokens
            )
            if reasoning_effort is not None:
                kwargs["reasoning_effort"] = reasoning_effort
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            start = getattr(self.transport, "start", None)
            if callable(start):
                self._pid = start()
                if not isinstance(self._pid, int) or self._pid <= 0:
                    raise DshGatewayError("DSH transport returned an invalid child pid")
                if self.persistence is not None:
                    self.persistence.attach_pid(self.context, self._pid)
            self.transport.initialize(**kwargs)
            try:
                outcome = self.transport.prompt(self.context.attempt_id, prompt)
            except HarnessTurnError as error:
                if error.usage is not None:
                    error_class = _classify_known_turn(error.reason)
                    with self._condition:
                        self._usage = error.usage
                        self._delivery_state = DeliveryState.ACKNOWLEDGED
                    raise DshKnownFailure(
                        "DSH turn ended with a known provider outcome",
                        error_class=error_class,
                        usage=error.usage,
                        runtime_message_id=error.message_id,
                        provider_request_id=(
                            error.reason.get("error", {}).get("requestId")
                            if isinstance(error.reason.get("error"), dict)
                            else None
                        ),
                    ) from None
                raise
            try:
                outcome = PromptOutcome.model_validate(outcome)
            except ValidationError as error:
                raise DshGatewayError("DSH prompt result is not a strict PromptOutcome") from error
            usage = outcome.usage
            delivery = DeliveryState.ACKNOWLEDGED
            with self._condition:
                if self._state is not _DshHandleState.COMPLETING:
                    raise GatewayLifecycleError(
                        f"complete returned after handle became {self._state.value}"
                    )
                self._delivery_state = delivery
                self._usage = usage
            if max_tokens is not None and usage.outputTokens > max_tokens:
                raise DshKnownFailure(
                    "DSH response exceeded its authenticated output-token limit",
                    error_class=AttemptErrorClass.budget,
                    usage=usage,
                    runtime_message_id=outcome.messageId,
                )
            if len(outcome.output) != 1:
                raise DshKnownFailure(
                    "DSH response violated the single-output contract",
                    error_class=AttemptErrorClass.validation,
                    usage=usage,
                    runtime_message_id=outcome.messageId,
                )
            try:
                output = _parse_strict_json(outcome.output[0].text)
            except DshGatewayError:
                raise DshKnownFailure(
                    "DSH response violated the JSON output contract",
                    error_class=AttemptErrorClass.validation,
                    usage=usage,
                    runtime_message_id=outcome.messageId,
                ) from None
            with self._condition:
                if self._state is not _DshHandleState.COMPLETING:
                    # Cancellation may win while the acknowledged response is
                    # being parsed.  Preserve its lifecycle decision and the
                    # usage/message evidence recorded above; a late complete
                    # must never overwrite CANCELLED with COMPLETED.
                    raise GatewayLifecycleError(
                        f"complete finalized after handle became {self._state.value}"
                    )
                self._state = _DshHandleState.COMPLETED
            return DshModelResult(
                output=output,
                input_tokens=usage.inputTokens,
                output_tokens=usage.outputTokens,
                provider_request_id=None,
                runtime_message_id=outcome.messageId,
                usage=usage,
                delivery_state=delivery,
            )
        except HarnessTransportError as error:
            with self._condition:
                if self._state is _DshHandleState.COMPLETING:
                    self._state = _DshHandleState.FAILED
                self._sync_delivery(error.delivery_state)
                delivery = self._delivery_state
            error_class = (
                AttemptErrorClass.indeterminate
                if delivery is not DeliveryState.NOT_STARTED
                else AttemptErrorClass.timeout
                if isinstance(error, HarnessTimeoutError)
                else AttemptErrorClass.provider
            )
            raise DshGatewayError(
                "DSH transport failed at a typed execution boundary",
                error_class=error_class,
            ) from None
        except BaseException:
            with self._condition:
                if self._state is _DshHandleState.COMPLETING:
                    self._state = _DshHandleState.FAILED
                self._sync_delivery()
            raise

    def _cleanup(self) -> None:
        with self._condition:
            escalation = self._cancel_escalation
            self._cancel_escalation = None
        if escalation is not None:
            escalation.cancel()
        error: BaseException | None = None
        if not self._transport_close_done:
            try:
                self.transport.close()
            except BaseException as exc:  # cleanup failures are deliberately observable
                error = exc
            else:
                self._transport_close_done = True
        if error is None and not self._directory_cleanup_done:
            try:
                shutil.rmtree(self.private_dir)
            except FileNotFoundError:
                self._directory_cleanup_done = True
            except BaseException as exc:
                error = exc
            else:
                self._directory_cleanup_done = True
        if error is not None:
            self._cleanup_error = error
            raise error
        self._cleanup_error = None

    def cancel(self) -> None:
        with self._condition:
            if self._state not in {_DshHandleState.OPEN, _DshHandleState.COMPLETING}:
                raise GatewayLifecycleError(
                    f"cannot cancel gateway handle in {self._state.value} state"
                )
            completion_in_flight = self._state is _DshHandleState.COMPLETING
            self._state = _DshHandleState.CANCELLED
        if completion_in_flight:
            # Never run the shutdown JSON-RPC handshake concurrently with the
            # prompt reader.  A process-group TERM wakes that owner; the runner
            # thread then performs the bounded close/KILL/reap ladder exactly
            # once before classifying delivery.
            self.transport.terminate()
            escalation = Timer(self.config.term_timeout_seconds, self._escalate_cancel)
            escalation.daemon = True
            with self._condition:
                if self._state is _DshHandleState.CANCELLED:
                    self._cancel_escalation = escalation
                    escalation.start()
            self._sync_delivery()
            return
        try:
            self._cleanup()
        finally:
            self._sync_delivery()

    def _escalate_cancel(self) -> None:
        """Wake a prompt owner whose child ignored TERM; that owner still reaps."""
        with self._condition:
            if self._state is not _DshHandleState.CANCELLED:
                return
        try:
            self.transport.kill()
        except BaseException as error:  # cleanup will retry and surface proof failure
            with self._condition:
                self._cleanup_error = error

    def close(self) -> None:
        with self._condition:
            if self._state is _DshHandleState.CLOSED:
                if self._cleanup_error is not None:
                    raise self._cleanup_error
                return
            if self._state is _DshHandleState.COMPLETING:
                raise GatewayLifecycleError(
                    "cannot close gateway handle while completion is in flight"
                )
            self._state = _DshHandleState.CLOSED
            self._close_count += 1
        try:
            self._cleanup()
        finally:
            self._sync_delivery()

    def retry_cleanup(self) -> None:
        with self._condition:
            if self._state is not _DshHandleState.CLOSED:
                raise GatewayLifecycleError("retry_cleanup requires a closed gateway handle")
            if self._cleanup_error is None:
                return
        self._cleanup()


class DshGatewayFactory(GatewayFactory):
    """Open one independently cancellable, private DSH handle per Attempt."""

    supported_runtime_kinds = frozenset({"dsh"})
    _ASSEMBLY_FIELDS = frozenset(
        {"config", "_transport_factory", "persistence", "_clock_us"}
    )

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_assembly_frozen", False) and name in self._ASSEMBLY_FIELDS:
            raise AttributeError("DSH factory assembly is immutable after construction")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        config: DshRuntimeConfig,
        *,
        transport_factory: TransportFactory | None = None,
        persistence: DshPersistence | None = None,
        clock_us: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(config, DshRuntimeConfig):
            raise TypeError("config must be a DshRuntimeConfig")
        self.config = config
        if transport_factory is not None and not config.allow_unpersisted:
            raise ValueError(
                "a production DSH factory must use the sealed AttemptTransport"
            )
        self._transport_factory = transport_factory or AttemptTransport
        self.persistence = persistence
        self._clock_us = clock_us or (lambda: time.time_ns() // 1_000)
        if not callable(self._transport_factory):
            raise TypeError("transport_factory must be callable")
        if not callable(self._clock_us):
            raise TypeError("clock_us must be callable")
        self._open_count = 0
        self._open_count_lock = Lock()
        self._assembly_frozen = True

    @property
    def open_count(self) -> int:
        with self._open_count_lock:
            return self._open_count

    @property
    def durable_runtime(self) -> bool:
        """Whether this factory is eligible for a product DSH route."""
        return bool(
            self.persistence is not None
            and self.config.prepared_dsh_home is not None
            and self.config.runtime_manifest is not None
            and not self.config.allow_unpersisted
            and self._transport_factory is AttemptTransport
            and all(
                value is not None
                for value in (
                    self.config.upstream_commit,
                    self.config.runtime_hash,
                    self.config.profile_hash,
                    self.config.policy_hash,
                    self.config.expected_model_profile_identity,
                    self.config.expected_model_profile_sha256,
                    self.config.expected_model_route_key,
                    self.config.expected_model_route_sha256,
                )
            )
        )

    def open(self, context: AttemptContext) -> GatewayHandle:
        _validate_context(context, self.config)
        self._remaining_seconds(context)
        if not self.config.allow_unpersisted:
            # Construction verifies the package once; every open re-verifies it
            # so a post-start mutation cannot silently inherit the original
            # provenance hashes.
            verified_runtime = self.config.verify_prepared_runtime()
        else:
            verified_runtime = None
        if self.persistence is None and not self.config.allow_unpersisted:
            raise ValueError("a durable DSH persistence seam is required")
        hashes = (
            self.config.upstream_commit,
            self.config.runtime_hash,
            self.config.profile_hash,
            self.config.policy_hash,
            self.config.expected_model_profile_identity,
            self.config.expected_model_profile_sha256,
            self.config.expected_model_route_key,
            self.config.expected_model_route_sha256,
        )
        if not self.config.allow_unpersisted and any(value is None for value in hashes):
            raise ValueError("runtime and frozen model provenance are required")
        if not self.config.allow_unpersisted and (
            self.config.patch is None
            or not Path(self.config.patch).is_file()
            or Path(self.config.patch).is_symlink()
        ):
            raise ValueError("a fixed regular DSH safety patch is required")
        if self.persistence is not None:
            self.persistence.reserve_launch(
                context,
                DshLaunch(
                    runtime_session_id=context.attempt_id,
                    deadline_at=context.deadline_at_us,
                    upstream_commit=self.config.upstream_commit or "0" * 40,
                    runtime_hash=self.config.runtime_hash or "0" * 64,
                    profile_hash=self.config.profile_hash or "0" * 64,
                    policy_hash=self.config.policy_hash or "0" * 64,
                ),
            )
        try:
            private_dir = Path(tempfile.mkdtemp(prefix="dsh-attempt-", dir=self.config.cwd))
            os.chmod(private_dir, 0o700)
            home = private_dir / "home"
            dsh_home = private_dir / "dsh-home"
            temp = private_dir / "tmp"
            home.mkdir(mode=0o700)
            dsh_home.mkdir(mode=0o700)
            temp.mkdir(mode=0o700)
            if self.config.prepared_dsh_home is not None:
                copied_hash = _copy_dsh_home(Path(self.config.prepared_dsh_home), dsh_home)
                if (
                    verified_runtime is not None
                    and copied_hash != verified_runtime.template_sha256
                ):
                    raise ValueError("private DSH_HOME failed post-copy verification")
            env = dict(self.config.env)
            env.update(
                {
                    "HOME": str(home),
                    "DSH_HOME": str(dsh_home),
                    "TMPDIR": str(temp),
                    "TMP": str(temp),
                    "TEMP": str(temp),
                    "NODE_ENV": "production",
                    "DSH_TELEMETRY_DISABLED": "1",
                }
            )
            attempt_timeout_seconds = self._remaining_seconds(context)
            attempt_argv = self.config.argv
            if self.config.patch is not None:
                patch_index = attempt_argv.index("--patch") + 1
                private_patch = dsh_home / "pharos-safe.cordis.patch.yml"
                if not private_patch.is_file() or private_patch.is_symlink():
                    raise ValueError("private DSH_HOME is missing its authenticated safety patch")
                attempt_argv = (
                    *attempt_argv[:patch_index],
                    str(private_patch),
                    *attempt_argv[patch_index + 1 :],
                )
            transport_config = AttemptTransportConfig(
                argv=attempt_argv,
                cwd=str(private_dir),
                allowed_routes=self.config.allowed_routes,
                profile=self.config.profile,
                expected_server_version=self.config.expected_server_version,
                env=env,
                env_allowlist=self.config.env_allowlist
                | {
                    "HOME",
                    "DSH_HOME",
                    "TMPDIR",
                    "TMP",
                    "TEMP",
                    "NODE_ENV",
                    "DSH_TELEMETRY_DISABLED",
                },
                initialize_timeout_seconds=self.config.initialize_timeout_seconds,
                prompt_timeout_seconds=self.config.prompt_timeout_seconds,
                idle_timeout_seconds=self.config.idle_timeout_seconds,
                shutdown_timeout_seconds=self.config.shutdown_timeout_seconds,
                term_timeout_seconds=self.config.term_timeout_seconds,
                kill_timeout_seconds=self.config.kill_timeout_seconds,
                reap_timeout_seconds=self.config.reap_timeout_seconds,
                max_frame_bytes=self.config.max_frame_bytes,
                max_buffer_bytes=self.config.max_buffer_bytes,
                max_json_depth=self.config.max_json_depth,
                max_event_bytes=self.config.max_event_bytes,
                max_total_event_bytes=self.config.max_total_event_bytes,
                max_events=self.config.max_events,
                max_output_bytes=self.config.max_output_bytes,
                max_stderr_bytes=self.config.max_stderr_bytes,
                delivery_observer_timeout_seconds=self.config.delivery_observer_timeout_seconds,
                attempt_timeout_seconds=attempt_timeout_seconds,
            )
            persistence = self.persistence
            observer = (
                None
                if persistence is None
                else lambda state: persistence.observe_delivery(context, state)
            )
            transport_factory = self._transport_factory
            try:
                parameters = inspect.signature(transport_factory).parameters
                accepts_observer = "delivery_observer" in parameters or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters.values()
                )
            except (TypeError, ValueError):
                accepts_observer = False
            transport = (
                transport_factory(transport_config, delivery_observer=observer)  # type: ignore[call-arg]
                if accepts_observer
                else transport_factory(transport_config)
            )
            if (
                not hasattr(transport, "start")
                or not hasattr(transport, "initialize")
                or not hasattr(transport, "prompt")
                or not hasattr(transport, "terminate")
                or not hasattr(transport, "kill")
                or not hasattr(transport, "close")
            ):
                raise TypeError("transport_factory returned an invalid transport")
            with self._open_count_lock:
                self._open_count += 1
            return DshAttemptHandle(context, transport, private_dir, self.config, self.persistence)
        except BaseException:
            if "private_dir" in locals():
                shutil.rmtree(private_dir, ignore_errors=True)
            raise

    def _remaining_seconds(self, context: AttemptContext) -> float:
        now_us = self._clock_us()
        if type(now_us) is not int or now_us <= 0:
            raise DshGatewayError("DSH runtime clock is invalid")
        remaining_us = context.deadline_at_us - now_us
        if remaining_us <= 0:
            raise DshGatewayError("DSH Attempt deadline has expired")
        return remaining_us / 1_000_000


def _validate_context(context: AttemptContext, config: DshRuntimeConfig) -> None:
    if not isinstance(context, AttemptContext):
        raise TypeError("gateway factory context must be an AttemptContext")
    if context.runtime_kind != "dsh":
        raise ValueError("DSH gateway requires AttemptContext.runtime_kind='dsh'")
    if (context.provider, context.model) not in config.allowed_routes:
        raise ValueError("Attempt route is not admitted by the DSH runtime config")
    if (
        config.expected_model_profile_identity is not None
        and context.model_profile_identity != config.expected_model_profile_identity
    ):
        raise ValueError("Attempt model profile provenance does not match DSH config")
    if (
        config.expected_model_profile_sha256 is not None
        and context.model_profile_sha256 != config.expected_model_profile_sha256
    ):
        raise ValueError("Attempt model profile hash does not match DSH config")
    if (
        config.expected_model_route_key is not None
        and context.model_route_key != config.expected_model_route_key
    ):
        raise ValueError("Attempt model route provenance does not match DSH config")
    if (
        config.expected_model_route_sha256 is not None
        and context.model_route_sha256 != config.expected_model_route_sha256
    ):
        raise ValueError("Attempt model route hash does not match DSH config")
    if context.usage_source != config.expected_usage_source:
        raise ValueError("Attempt usage provenance does not match DSH config")


__all__ = [
    "AttemptHandle",
    "CANARY_ROUTE",
    "CANARY_PROFILE",
    "CANARY_ROUTE_KEY",
    "DshAttemptHandle",
    "DshGatewayError",
    "DshGatewayFactory",
    "DshGatewayHandle",
    "DshKnownFailure",
    "DshLaunch",
    "DshModelResult",
    "DshRuntimeConfig",
]

# Short aliases keep the Attempt vocabulary discoverable to callers while the
# concrete class name makes its runtime ownership explicit.
AttemptHandle = DshAttemptHandle
DshGatewayHandle = DshAttemptHandle
