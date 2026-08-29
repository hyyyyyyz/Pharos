"""Shared fixtures for Harness kernel tests.

Every test runs against an isolated temporary SQLite database; nothing here
touches the network, a real model, a real key or a real Zotero library.
"""

from __future__ import annotations

import pharos.db.session as dbs
import pytest
from pharos.db.session import session_scope
from pharos.harness.app import HarnessApp
from pharos.harness.configrev import HarnessConfigSnapshot, WorkflowRoute
from pharos.harness.contracts import ActivationState
from pharos.harness.fakes import FakeClock
from pharos.harness.repository import Scope, now_iso


@pytest.fixture
def db(tmp_path):
    """A fresh, isolated database with the engine installed."""
    # init_engine is a process singleton; reset it so each test gets its own
    # file. This is test support only -- the production path inits once.
    dbs._engine = None
    dbs._SessionLocal = None
    dbs.init_engine(tmp_path / "pharos.db")
    yield tmp_path / "pharos.db"
    dbs._engine = None
    dbs._SessionLocal = None


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def app(db, clock) -> HarnessApp:
    harness = HarnessApp(clock=clock)
    harness.ensure_bootstrapped()
    return harness


@pytest.fixture(autouse=True)
def seed_users(db):
    """Harness user rows reference the users table; seed the test accounts."""
    from pharos.db.models import User

    with session_scope() as session:
        for uid in ("owner" + "0" * 26 + "1", "stranger" + "0" * 23 + "1"):
            if session.get(User, uid) is None:
                session.add(
                    User(
                        id=uid,
                        email=f"{uid}@example.invalid",
                        password_hash="test-hash",
                    )
                )
    yield


@pytest.fixture
def owner() -> Scope:
    return Scope.user("owner000000000000000000000000001")


def enable_canary(app: HarnessApp, *, agent_steps: bool = False) -> None:
    """Persist an operator revision that activates the internal canary."""
    with session_scope() as session:
        head = app.config_service.current(session)
        expected = head["current_revision_id"] if head else None
        snapshot = HarnessConfigSnapshot(
            gates={
                "harness_enabled": True,
                "dispatcher_enabled": True,
                "canary_enabled": True,
                "agent_steps_enabled": agent_steps,
                "agent_runtime_enabled": False,
                "domain_publish_enabled": False,
                "fulltext_enabled": False,
                "desktop_bridge_enabled": False,
                "experiments_enabled": False,
            },
            routes=(
                WorkflowRoute(
                    workflow_key="harness.canary",
                    active_version=1,
                    activation_state=ActivationState.active,
                    execution_mode=None,
                ),
            ),
            actor="test-operator",
            reason="run the kernel canary",
        )
        app.config_service.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=expected,
            actor="test-operator",
            reason="run the kernel canary",
            now=now_iso(),
        )
        session.commit()
