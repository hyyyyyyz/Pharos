"""Append-only Harness events with cursor replay, plus the DB live tail.

The event log is the durable record; SSE is an optimisation, never the truth.
Replay is a cursor query (`seq > after_seq`), so a polling client and a
reconnecting SSE client read exactly the same history. Heartbeats are never
written to the log.

Retention: when rows older than the floor are deleted, the cursor is never
reused (SQLite AUTOINCREMENT), so a client that falls behind gets
``resync_required`` with the floor instead of silently replaying garbage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pharos.harness.contracts import NotFoundError
from pharos.harness.repository import Scope
from pharos.harness.tables import events

#: Hard caps that keep one client or one event from unbounded memory use.
MAX_PAYLOAD_CHARS = 8_000
MAX_REPLAY_PAGE = 200
MAX_TOTAL_REPLAY = 1_000


class EventTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class EventRecord:
    seq: int
    run_id: str
    scope_type: str
    scope_id: str
    step_id: str | None
    attempt_id: str | None
    event_type: str
    payload: dict
    created_at: int

    def public(self) -> dict:
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "payload": self.payload,
            "created_at_us": self.created_at,
        }


class EventStore:
    def __init__(self, retention_floor_seq: int = 0) -> None:
        self.retention_floor_seq = retention_floor_seq

    def append(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        event_type: str,
        payload: dict | None = None,
        step_id: str | None = None,
        attempt_id: str | None = None,
        now_us: int = 0,
    ) -> EventRecord:
        body = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        if len(body) > MAX_PAYLOAD_CHARS:
            raise EventTooLarge(f"event payload of {len(body)} chars exceeds the cap")
        result = session.execute(
            events.insert().values(
                run_id=run_id,
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                step_id=step_id,
                attempt_id=attempt_id,
                event_type=event_type,
                payload_json=body,
                created_at=now_us,
            )
        )
        result_any: Any = result
        return EventRecord(
            seq=result_any.lastrowid,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            step_id=step_id,
            attempt_id=attempt_id,
            event_type=event_type,
            payload=payload or {},
            created_at=now_us,
        )

    def require_run(self, session: Session, *, scope: Scope, run_id: str) -> None:
        from pharos.harness.repository import HarnessRunRepository

        HarnessRunRepository().require(session, scope=scope, run_id=run_id)

    def replay(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        after_seq: int,
        limit: int = MAX_REPLAY_PAGE,
    ) -> list[EventRecord]:
        if after_seq < self.retention_floor_seq:
            raise _ResyncRequired(self.retention_floor_seq)
        limit = min(limit, MAX_REPLAY_PAGE)
        rows = session.execute(
            select(events)
            .where(
                scope.where(events),
                events.c.run_id == run_id,
                events.c.seq > after_seq,
            )
            .order_by(events.c.seq)
            .limit(limit)
        ).mappings()
        return [_from_row(row) for row in rows]

    def last_seq(self, session: Session, *, run_id: str) -> int:
        value = session.execute(
            select(func.max(events.c.seq)).where(events.c.run_id == run_id)
        ).scalar()
        return int(value or 0)

    def tail(
        self, session: Session, *, scope: Scope, run_id: str, after_seq: int, limit: int
    ) -> list[EventRecord]:
        """The same replay query the live tail polls with."""
        if after_seq < self.retention_floor_seq:
            raise _ResyncRequired(self.retention_floor_seq)
        rows = session.execute(
            select(events)
            .where(
                scope.where(events),
                events.c.run_id == run_id,
                events.c.seq > after_seq,
            )
            .order_by(events.c.seq)
            .limit(limit)
        ).mappings()
        return [_from_row(row) for row in rows]


class _ResyncRequired(NotFoundError):
    def __init__(self, floor_seq: int) -> None:
        super().__init__("resync_required")
        self.floor_seq = floor_seq


def _from_row(row) -> EventRecord:  # noqa: ANN001
    return EventRecord(
        seq=row["seq"],
        run_id=row["run_id"],
        scope_type=row["scope_type"],
        scope_id=row["scope_id"],
        step_id=row["step_id"],
        attempt_id=row["attempt_id"],
        event_type=row["event_type"],
        payload=json.loads(row["payload_json"] or "{}"),
        created_at=row["created_at"],
    )
