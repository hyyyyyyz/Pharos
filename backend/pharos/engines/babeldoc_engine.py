"""BabelDOC translation engine — drives the arm's-length engine worker.

Runs in the APP env. It never imports BabelDOC; instead it spawns
``engine_worker/worker.py`` with the ENGINE env's Python interpreter (a separate
process in a separate env) and parses the worker's newline-delimited JSON
progress stream. See ``docs/ARCHITECTURE.md`` §3.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from pharos.engines.base import (
    EngineError,
    JobStage,
    TranslationEvent,
    TranslationProgress,
    TranslationRequest,
    TranslationResult,
    TranslatorConfig,
)

SENTINEL = "@@XZEVT@@ "

# Coarse mapping from BabelDOC stage names to our UI-facing JobStage.
_STAGE_MAP: dict[str, JobStage] = {
    "Parse PDF and Create Intermediate Representation": JobStage.PARSING,
    "DetectScannedFile": JobStage.PARSING,
    "Parse Page Layout": JobStage.PARSING,
    "Parse Paragraphs": JobStage.PARSING,
    "Parse Formulas and Styles": JobStage.PARSING,
    "Translate Paragraphs": JobStage.TRANSLATING,
    "Typesetting": JobStage.TYPESETTING,
    "Add Fonts": JobStage.TYPESETTING,
    "Generate drawing instructions": JobStage.TYPESETTING,
    "Subset font": JobStage.TYPESETTING,
    "Save PDF": JobStage.TYPESETTING,
}


def default_worker_script() -> Path:
    """Path to ``engine_worker/worker.py`` relative to the backend package."""
    return (Path(__file__).resolve().parents[2] / "engine_worker" / "worker.py").resolve()


def default_engine_python() -> Path:
    """Best-effort default path to the engine env's Python. Override in config."""
    return Path.home() / "miniconda3" / "envs" / "pharos-engine" / "bin" / "python"


def _coarse_stage(name: str) -> JobStage:
    return _STAGE_MAP.get(name, JobStage.TRANSLATING)


class BabelDocEngine:
    """Drives BabelDOC (via pdf2zh-next) as a subprocess in the engine env."""

    name = "babeldoc"

    def __init__(
        self,
        engine_python: str | Path | None = None,
        worker_script: str | Path | None = None,
        translator: TranslatorConfig | None = None,
        watermark_output_mode: str = "no_watermark",
        qps: int = 4,
    ) -> None:
        self._engine_python = str(engine_python or default_engine_python())
        self._worker_script = str(worker_script or default_worker_script())
        self._translator = translator or TranslatorConfig(type="bing")
        self._watermark_output_mode = watermark_output_mode
        self._qps = qps

    def _build_job(self, request: TranslationRequest) -> dict:
        return {
            "input_pdf": str(request.source_pdf),
            "output_dir": str(request.output_dir),
            "lang_in": request.source_lang,
            "lang_out": request.target_lang,
            "pages": request.pages,
            "qps": self._qps,
            "watermark_output_mode": self._watermark_output_mode,
            "no_mono": False,
            "no_dual": False,
            "custom_system_prompt": request.custom_system_prompt,
            "glossaries": str(request.glossaries_csv) if request.glossaries_csv else None,
            "translator": self._translator.to_dict(),
        }

    async def translate(self, request: TranslationRequest) -> AsyncIterator[TranslationEvent]:
        request.output_dir.mkdir(parents=True, exist_ok=True)

        # Write the job spec (incl. any API key) to a private temp file so
        # secrets never appear in argv / `ps`.
        fd, job_path = tempfile.mkstemp(suffix=".json", prefix="xz-job-")
        os.write(fd, json.dumps(self._build_job(request)).encode("utf-8"))
        os.close(fd)
        os.chmod(job_path, 0o600)

        proc: asyncio.subprocess.Process | None = None
        stderr_chunks: list[bytes] = []
        try:
            proc = await asyncio.create_subprocess_exec(
                self._engine_python,
                self._worker_script,
                job_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HF_HUB_DISABLE_TELEMETRY": "1"},
            )
            assert proc.stdout is not None and proc.stderr is not None

            async def _drain_stderr() -> None:
                async for line in proc.stderr:  # type: ignore[union-attr]
                    stderr_chunks.append(line)

            drain = asyncio.create_task(_drain_stderr())
            try:
                saw_finish = False
                async for raw in proc.stdout:
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                    if not line.startswith(SENTINEL):
                        continue  # ignore any stray library output on stdout
                    ev = json.loads(line[len(SENTINEL) :])
                    etype = ev.get("type")
                    if etype == "progress":
                        yield TranslationProgress(
                            stage=_coarse_stage(ev.get("stage", "")),
                            percent=float(ev.get("percent", 0.0)),
                            message=ev.get("stage", ""),
                            stage_percent=float(ev.get("stage_percent", 0.0)),
                        )
                    elif etype == "finish":
                        saw_finish = True
                        yield TranslationResult(
                            mono_pdf=Path(ev["mono_pdf_path"]) if ev.get("mono_pdf_path") else None,
                            dual_pdf=Path(ev["dual_pdf_path"]) if ev.get("dual_pdf_path") else None,
                            total_seconds=ev.get("total_seconds"),
                            tokens=ev.get("tokens"),
                        )
                    elif etype == "error":
                        raise EngineError(
                            ev.get("error", "unknown engine error"),
                            ev.get("error_type", "EngineError"),
                            ev.get("details", ""),
                        )
                    # "stages" summary is currently ignored by the app.

                await proc.wait()
                if not saw_finish and proc.returncode not in (0, None):
                    tail = b"".join(stderr_chunks).decode("utf-8", errors="replace")[-2000:]
                    raise EngineError(
                        f"engine worker exited with code {proc.returncode}",
                        "WorkerExit",
                        tail,
                    )
            finally:
                drain.cancel()
        finally:
            # Ensure the worker (and its BabelDOC child) is not left running, e.g.
            # if the consumer cancels this async generator.
            if proc is not None and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except (asyncio.TimeoutError, ProcessLookupError):
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
            Path(job_path).unlink(missing_ok=True)
