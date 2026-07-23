"""Tests for the ORM -> schema converters (:mod:`pharos.api.schemas`).

The concern here is timezone awareness. SQLite has no timezone type, so a row
SQLAlchemy has just written hands back an aware datetime while the same row
re-read on a later request hands back a naive one. Serialised, the two differ
only by a trailing "Z", and a client doing ``new Date(added_at)`` reads the
offset-less form as *local* time — rendering every library timestamp hours off
in any non-UTC zone, and visibly changing it after a refresh.

These tests therefore assert on the naive input path, which is the one that was
broken; the aware path is checked mainly to confirm normalising is not lossy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pharos.api.schemas import as_utc, job_out, paper_out
from pharos.db.models import Paper, TranslationJob

NAIVE = datetime(2026, 7, 18, 15, 12, 2, 57696)
AWARE = NAIVE.replace(tzinfo=timezone.utc)


def _job(**overrides) -> TranslationJob:
    fields = dict(
        id="job-1",
        paper_id="paper-1",
        status="done",
        stage="finished",
        progress=1.0,
        translator_type="deepseek",
        target_lang="zh",
        created_at=NAIVE,
        started_at=NAIVE,
        finished_at=NAIVE,
    )
    fields.update(overrides)
    return TranslationJob(**fields)


def _paper(**overrides) -> Paper:
    fields = dict(
        id="paper-1",
        title="A Paper",
        orig_filename="a.pdf",
        source="upload",
        source_lang="en",
        added_at=NAIVE,
        jobs=[],
    )
    fields.update(overrides)
    return Paper(**fields)


def test_as_utc_stamps_a_naive_timestamp() -> None:
    assert as_utc(NAIVE) == AWARE
    assert as_utc(NAIVE).tzinfo is timezone.utc


def test_as_utc_leaves_an_offset_timestamp_alone() -> None:
    """A non-UTC offset must be preserved, not overwritten — that would shift the instant."""
    shanghai = NAIVE.replace(tzinfo=timezone(timedelta(hours=8)))
    assert as_utc(shanghai) is shanghai
    assert as_utc(AWARE) == AWARE


def test_as_utc_passes_none_through() -> None:
    assert as_utc(None) is None


def test_paper_out_added_at_is_aware_when_read_back_naive() -> None:
    assert paper_out(_paper()).added_at == AWARE


def test_job_out_timestamps_are_aware_when_read_back_naive() -> None:
    out = job_out(_job())
    assert out.created_at == AWARE
    assert out.started_at == AWARE
    assert out.finished_at == AWARE


def test_job_out_keeps_unset_timestamps_none() -> None:
    """A job that has not started yet must stay null, not become the epoch."""
    out = job_out(_job(started_at=None, finished_at=None))
    assert out.started_at is None
    assert out.finished_at is None


def test_a_freshly_written_row_and_its_naive_re_read_serialise_identically() -> None:
    """The actual reported bug: POST and a following GET disagreed by a "Z"."""
    on_write = paper_out(_paper(added_at=AWARE))
    on_read_back = paper_out(_paper(added_at=NAIVE))
    assert on_write.model_dump_json() == on_read_back.model_dump_json()


def test_nested_latest_job_timestamps_are_normalised_too() -> None:
    paper = _paper(jobs=[_job()])
    latest = paper_out(paper).latest_job
    assert latest is not None
    assert latest.created_at == AWARE
