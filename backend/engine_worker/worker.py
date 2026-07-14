#!/usr/bin/env python3
"""Pharos translation engine worker.

Runs INSIDE the dedicated ``pharos-engine`` conda env (osx-64 / Rosetta). This
is the ONLY place the AGPL-3.0 BabelDOC engine is imported. The FastAPI backend
(a separate process in a separate env) spawns this script at arm's length,
passing a job-spec file, and reads newline-delimited JSON progress events from
stdout. See ``docs/ARCHITECTURE.md`` §3.

macOS note: BabelDOC uses ``multiprocessing`` with the *spawn* start method, so
the child re-imports this module. All heavy imports are therefore done lazily
inside functions, and the entrypoint is guarded by ``if __name__ == "__main__"``.

Protocol — one line per event on stdout, each prefixed with ``SENTINEL`` so the
parent can ignore any stray library output:

    @@XZEVT@@ {"type":"stages","stages":[...]}
    @@XZEVT@@ {"type":"progress","percent":14.1,"stage":"...","stage_percent":100.0,"phase":"progress_update"}
    @@XZEVT@@ {"type":"finish","mono_pdf_path":"...","dual_pdf_path":"...","total_seconds":28.9,"tokens":1234}
    @@XZEVT@@ {"type":"error","error":"...","error_type":"...","details":"..."}

Job spec (JSON file passed as ``argv[1]``). Secrets travel via this file
(mode 600), never via argv, so they never appear in ``ps`` output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import traceback
from pathlib import Path

SENTINEL = "@@XZEVT@@ "

# The engine's own logging stays on stderr and quiet; stdout is our NDJSON channel.
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
for _noisy in ("httpx", "openai", "httpcore", "http11", "pdfminer", "peewee"):
    logging.getLogger(_noisy).setLevel(logging.CRITICAL)


def _emit(obj: dict) -> None:
    sys.stdout.write(SENTINEL + json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _build_translate_engine_settings(translator: dict):
    from pdf2zh_next.config.translate_engine_model import (
        BingSettings,
        DeepSeekSettings,
        GoogleSettings,
        OpenAICompatibleSettings,
        OpenAISettings,
    )

    t = (translator.get("type") or "bing").lower()
    if t == "bing":
        return BingSettings()
    if t == "google":
        return GoogleSettings()
    if t == "deepseek":
        return DeepSeekSettings(
            deepseek_api_key=translator["api_key"],
            deepseek_model=translator.get("model") or "deepseek-chat",
        )
    if t in ("openai_compatible", "openai-compatible", "compatible"):
        temp = translator.get("temperature")
        return OpenAICompatibleSettings(
            openai_compatible_base_url=translator["base_url"],
            openai_compatible_api_key=translator["api_key"],
            openai_compatible_model=translator.get("model") or "gpt-4o-mini",
            openai_compatible_temperature=temp,
            openai_compatible_send_temperature=True if temp is not None else None,
        )
    if t == "openai":
        temp = translator.get("temperature")
        return OpenAISettings(
            openai_api_key=translator["api_key"],
            openai_base_url=translator.get("base_url"),
            openai_model=translator.get("model") or "gpt-4o-mini",
            openai_temperature=temp,
            openai_send_temprature=True if temp is not None else None,
        )
    raise ValueError(f"Unknown translator type: {t!r}")


def _build_settings(job: dict):
    from pdf2zh_next.config.model import (
        BasicSettings,
        PDFSettings,
        SettingsModel,
        TranslationSettings,
    )

    return SettingsModel(
        # debug MUST stay False -> keeps BabelDOC in its own subprocess (AGPL seam).
        basic=BasicSettings(),
        translation=TranslationSettings(
            lang_in=job.get("lang_in", "en"),
            lang_out=job.get("lang_out", "zh"),
            output=job["output_dir"],
            qps=job.get("qps", 4),
            custom_system_prompt=job.get("custom_system_prompt"),
            glossaries=job.get("glossaries"),
        ),
        pdf=PDFSettings(
            pages=job.get("pages"),
            watermark_output_mode=job.get("watermark_output_mode", "no_watermark"),
            no_mono=job.get("no_mono", False),
            no_dual=job.get("no_dual", False),
        ),
        translate_engine_settings=_build_translate_engine_settings(
            job.get("translator") or {"type": "bing"}
        ),
    )


async def _run(job: dict) -> int:
    from pdf2zh_next.high_level import do_translate_async_stream

    settings = _build_settings(job)
    input_pdf = job["input_pdf"]

    async for ev in do_translate_async_stream(settings, input_pdf):
        etype = ev.get("type")
        if etype in ("progress_start", "progress_update", "progress_end"):
            _emit(
                {
                    "type": "progress",
                    "percent": round(float(ev.get("overall_progress", 0.0)), 2),
                    "stage": ev.get("stage", ""),
                    "stage_percent": round(float(ev.get("stage_progress", 0.0)), 2),
                    "phase": etype,
                }
            )
        elif etype == "stage_summary":
            _emit({"type": "stages", "stages": ev.get("stages")})
        elif etype == "finish":
            r = ev["translate_result"]
            mono = getattr(r, "no_watermark_mono_pdf_path", None) or getattr(
                r, "mono_pdf_path", None
            )
            dual = getattr(r, "no_watermark_dual_pdf_path", None) or getattr(
                r, "dual_pdf_path", None
            )
            _emit(
                {
                    "type": "finish",
                    "mono_pdf_path": str(mono) if mono else None,
                    "dual_pdf_path": str(dual) if dual else None,
                    "total_seconds": getattr(r, "total_seconds", None),
                    "tokens": getattr(r, "total_valid_text_token_count", None),
                }
            )
            return 0
        elif etype == "error":
            _emit(
                {
                    "type": "error",
                    "error": ev.get("error", "unknown error"),
                    "error_type": ev.get("error_type", "UnknownError"),
                    "details": ev.get("details", ""),
                }
            )
            return 1
    _emit(
        {
            "type": "error",
            "error": "translation stream ended without a finish event",
            "error_type": "NoFinish",
            "details": "",
        }
    )
    return 1


def main() -> int:
    if len(sys.argv) < 2:
        _emit(
            {
                "type": "error",
                "error": "usage: worker.py <job.json>",
                "error_type": "Usage",
                "details": "",
            }
        )
        return 2
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        return asyncio.run(_run(job))
    except Exception as e:  # noqa: BLE001 — top-level guard: report to parent, don't crash silently
        _emit(
            {
                "type": "error",
                "error": str(e),
                "error_type": e.__class__.__name__,
                "details": traceback.format_exc(),
            }
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
