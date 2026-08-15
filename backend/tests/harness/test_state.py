"""The transition tables are exhaustive: every legal transition is exercised
and every illegal one raises, without a database.
"""

from __future__ import annotations

import itertools

import pytest
from pharos.harness.contracts import (
    AttemptState,
    RunState,
    StateError,
    StepState,
)
from pharos.harness.state import (
    ATTEMPT_TRANSITIONS,
    RUN_TRANSITIONS,
    STEP_TRANSITIONS,
    _check,
)

TERMINAL = {
    RunState: frozenset(
        {RunState.succeeded, RunState.failed, RunState.cancelled, RunState.indeterminate}
    ),
    StepState: frozenset(
        {
            StepState.succeeded,
            StepState.failed,
            StepState.cancelled,
            StepState.skipped,
            StepState.indeterminate,
        }
    ),
    AttemptState: frozenset(
        {
            AttemptState.succeeded,
            AttemptState.failed,
            AttemptState.timed_out,
            AttemptState.cancelled,
            AttemptState.abandoned,
            AttemptState.blocked,
            AttemptState.indeterminate,
        }
    ),
}


def test_every_state_has_an_entry():
    for enum, table in (
        (RunState, RUN_TRANSITIONS),
        (StepState, STEP_TRANSITIONS),
        (AttemptState, ATTEMPT_TRANSITIONS),
    ):
        assert set(enum) == set(table), f"{enum.__name__} has unlisted states"


def test_terminal_states_have_no_exits():
    for enum, table in (
        (RunState, RUN_TRANSITIONS),
        (StepState, STEP_TRANSITIONS),
        (AttemptState, ATTEMPT_TRANSITIONS),
    ):
        for state in TERMINAL[enum]:
            assert (
                table[state] == frozenset()
            ), f"{enum.__name__}.{state} is terminal but declares transitions"


def test_every_legal_transition_passes():
    for enum, table in (
        (RunState, RUN_TRANSITIONS),
        (StepState, STEP_TRANSITIONS),
        (AttemptState, ATTEMPT_TRANSITIONS),
    ):
        for current, allowed in table.items():
            for target in allowed:
                _check(current, target, table, "probe")  # must not raise


def test_every_illegal_transition_raises():
    for enum, table in (
        (RunState, RUN_TRANSITIONS),
        (StepState, STEP_TRANSITIONS),
        (AttemptState, ATTEMPT_TRANSITIONS),
    ):
        for current, target in itertools.product(list(enum), repeat=2):
            if target in table[current]:
                continue
            with pytest.raises(StateError, match="illegal transition"):
                _check(current, target, table, "probe")


def test_unknown_state_raises():
    with pytest.raises(StateError, match="unknown state"):
        _check("banana", "queued", RUN_TRANSITIONS, "probe")


def test_attempt_retry_creates_new_rows_not_reuse():
    """Retries are new attempts; the vocabulary has no 'restart' state."""
    assert "restarted" not in {state.value for state in AttemptState}
    assert {state.value for state in AttemptState} >= {
        "leased",
        "running",
        "succeeded",
        "failed",
        "timed_out",
        "cancelled",
        "abandoned",
        "blocked",
        "indeterminate",
    }
