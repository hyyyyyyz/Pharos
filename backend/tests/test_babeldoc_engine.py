"""Tests for the BabelDOC engine adapter.

Fast unit tests always run. The end-to-end integration test needs the
``pharos-engine`` conda env plus a sample PDF and is skipped otherwise:

    PHAROS_ENGINE_PYTHON=~/miniconda3/envs/pharos-engine/bin/python \
    PHAROS_TEST_PDF=/path/to/paper.pdf \
    pytest backend/tests/test_babeldoc_engine.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pharos.engines import (
    BabelDocEngine,
    JobStage,
    TranslationProgress,
    TranslationRequest,
    TranslationResult,
    TranslatorConfig,
)
from pharos.engines.babeldoc_engine import _coarse_stage, default_worker_script


# ----------------------------- fast unit tests -----------------------------


def test_worker_script_exists() -> None:
    assert default_worker_script().name == "worker.py"
    assert default_worker_script().exists()


def test_coarse_stage_mapping() -> None:
    assert _coarse_stage("Parse Page Layout") is JobStage.PARSING
    assert _coarse_stage("Translate Paragraphs") is JobStage.TRANSLATING
    assert _coarse_stage("Save PDF") is JobStage.TYPESETTING
    # Unknown stages fall back to TRANSLATING rather than crashing.
    assert _coarse_stage("Something New") is JobStage.TRANSLATING


def test_build_job_keeps_secret_and_shape(tmp_path: Path) -> None:
    engine = BabelDocEngine(
        engine_python="/does/not/matter",
        translator=TranslatorConfig(type="deepseek", api_key="sk-secret", model="deepseek-chat"),
    )
    req = TranslationRequest(
        source_pdf=tmp_path / "in.pdf",
        output_dir=tmp_path / "out",
        pages="1",
    )
    job = engine._build_job(req)
    assert job["lang_out"] == "zh"
    assert job["watermark_output_mode"] == "no_watermark"
    assert job["no_mono"] is False and job["no_dual"] is False
    assert job["translator"]["type"] == "deepseek"
    assert job["translator"]["api_key"] == "sk-secret"


# --------------------------- integration (opt-in) ---------------------------

_ENGINE_PY = os.environ.get("PHAROS_ENGINE_PYTHON")
_TEST_PDF = os.environ.get("PHAROS_TEST_PDF")


@pytest.mark.skipif(
    not (_ENGINE_PY and Path(_ENGINE_PY).exists() and _TEST_PDF and Path(_TEST_PDF).exists()),
    reason="set PHAROS_ENGINE_PYTHON and PHAROS_TEST_PDF to run the engine integration test",
)
async def test_translate_one_page(tmp_path: Path) -> None:
    engine = BabelDocEngine(engine_python=_ENGINE_PY, translator=TranslatorConfig(type="bing"))
    req = TranslationRequest(
        source_pdf=Path(_TEST_PDF),  # type: ignore[arg-type]
        output_dir=tmp_path,
        pages="1",
    )
    progress_count = 0
    result: TranslationResult | None = None
    async for ev in engine.translate(req):
        if isinstance(ev, TranslationProgress):
            progress_count += 1
            assert 0.0 <= ev.percent <= 100.0
        elif isinstance(ev, TranslationResult):
            result = ev

    assert progress_count > 0
    assert result is not None
    assert result.mono_pdf is not None and result.mono_pdf.exists()
    assert result.dual_pdf is not None and result.dual_pdf.exists()
