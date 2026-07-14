"""Translation-engine abstraction.

Every translation backend is reached through the :class:`TranslationEngine`
protocol. The MVP implementation (:mod:`pharos.engines.babeldoc_engine`)
drives the AGPL-3.0 BabelDOC engine as an *arm's-length subprocess* (see
``docs/ARCHITECTURE.md`` §3); future engines — e.g. a MinerU-based extractor
that also yields structured chunks for RAG/Q&A — can be dropped in without
touching the API or UI layers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class JobStage(str, Enum):
    """Coarse pipeline stage, surfaced to the UI."""

    QUEUED = "queued"
    PARSING = "parsing"
    TRANSLATING = "translating"
    TYPESETTING = "typesetting"
    DONE = "done"
    ERROR = "error"


class EngineError(RuntimeError):
    """A translation engine failed. Carries the engine's structured error info."""

    def __init__(self, message: str, error_type: str = "EngineError", details: str = "") -> None:
        super().__init__(message)
        self.error_type = error_type
        self.details = details


@dataclass(slots=True)
class TranslatorConfig:
    """Which translation backend to use, and its credentials.

    ``type`` is one of: ``bing``, ``google`` (free, keyless); ``deepseek``,
    ``openai``, ``openai_compatible`` (LLM, need ``api_key``). ``base_url`` /
    ``model`` / ``temperature`` apply to the LLM backends. Secrets are passed to
    the worker via a mode-600 file, never argv.
    """

    type: str = "bing"
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: str | None = None

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
        }


@dataclass(slots=True)
class TranslationProgress:
    """A progress event streamed while a paper is translated."""

    stage: JobStage
    percent: float  # overall, 0.0 – 100.0
    message: str = ""
    stage_percent: float = 0.0  # progress within the current stage


@dataclass(slots=True)
class TranslationResult:
    """Terminal event: paths to the produced PDFs (may be ``None`` on partial output)."""

    mono_pdf: Path | None  # Chinese-only, layout-preserving
    dual_pdf: Path | None  # bilingual, side-by-side
    total_seconds: float | None = None
    tokens: int | None = None


@dataclass(slots=True)
class TranslationRequest:
    """Everything the engine needs to translate one document."""

    source_pdf: Path
    output_dir: Path
    target_lang: str = "zh"
    source_lang: str = "en"
    pages: str | None = None  # e.g. "1", "1-3,5"; None = all pages
    glossaries_csv: Path | None = None  # source,target,tgt_lng CSV (M4)
    custom_system_prompt: str | None = None


# The engine yields any number of TranslationProgress events, then exactly one
# TranslationResult as the final item (or raises EngineError).
TranslationEvent = TranslationProgress | TranslationResult


@runtime_checkable
class TranslationEngine(Protocol):
    """Protocol implemented by every translation backend."""

    #: stable identifier persisted on the job, e.g. ``"babeldoc"``
    name: str

    def translate(self, request: TranslationRequest) -> AsyncIterator[TranslationEvent]:
        """Translate ``request.source_pdf``.

        Returns an async iterator that yields :class:`TranslationProgress`
        events during the run and a final :class:`TranslationResult`. Raises
        :class:`EngineError` on failure.
        """
        ...
