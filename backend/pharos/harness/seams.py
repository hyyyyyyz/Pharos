"""Replacement seams: the interfaces H1's SQLite implementation stands behind.

These are Protocols, not registries. Business code depends on the shape; the
SQLite implementations live in the repository/events/usage modules, and a
future Postgres or external-queue replacement must satisfy the same shapes
without touching workflow definitions, API or Event contracts.
"""

from __future__ import annotations

from typing import Any, Protocol

from pharos.harness.contracts import ScopeType


class Clock(Protocol):
    def utc_epoch_us(self) -> int: ...
    def utc_epoch_seconds(self) -> float: ...


class CapabilityExecutor(Protocol):
    def execute(self, action: dict) -> Any: ...


class DispatcherWakeup(Protocol):
    def signal(self) -> None: ...


class RunScope(Protocol):
    scope_type: ScopeType
    scope_id: str


class EventRecord(Protocol):
    seq: int
    run_id: str
    scope_type: ScopeType
    scope_id: str
    event_type: str
    payload: dict[str, Any]
    step_id: str | None
    attempt_id: str | None


class EventStore(Protocol):
    def append(
        self, *, scope, run_id: str, event_type: str, payload: dict, step_id=None, attempt_id=None
    ) -> EventRecord: ...  # noqa: ANN002
    def replay(self, *, scope, run_id: str, after_seq: int, limit: int) -> list[EventRecord]: ...


class UsageLedger(Protocol):
    def reserve(self, *, scope, run_id: str, kind: str, source: str, amount: int) -> str: ...
    def settle(self, reservation_id: str, *, actual: int) -> None: ...
    def release(self, reservation_id: str) -> None: ...
    def totals(self, *, scope, run_id: str) -> dict[str, int]: ...


class RunRepository(Protocol):
    def create(
        self, *, scope, workflow: str, input: dict, idempotency_key: str, config_revision_id: str
    ) -> Any: ...  # noqa: ANN002
    def get(self, *, scope, run_id: str) -> Any | None: ...  # noqa: ANN002
    def list(self, *, scope, limit: int, cursor: str | None) -> Any: ...  # noqa: ANN002
