"""Provider-neutral model access with a deterministic fake implementation.

H1 wires exactly one gateway: the deterministic fake. The canary (and the
whole kernel test matrix) must never spend real money; a real HTTP gateway
arrives with the first business workflow (H2), behind this same protocol.
"""

from __future__ import annotations

from typing import Protocol

from pharos.harness.fakes import FakeModel, ModelResult


class ModelGateway(Protocol):
    def complete(self, payload: dict) -> ModelResult: ...
    def cancel(self) -> None: ...


class FakeModelGateway:
    """The H1 default: scripted, offline, usage-accounted by the caller."""

    def __init__(self, model: FakeModel) -> None:
        self._model = model

    def complete(self, payload: dict) -> ModelResult:
        return self._model.complete(payload)

    def cancel(self) -> None:
        self._model.cancelled = True
