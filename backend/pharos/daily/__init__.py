"""每日论文 — the daily arXiv sweep (fetch, LLM reading, persistence).

Ported from the user's ``arxiv_ws`` project, where the pipeline only advanced
when they manually invoked a Claude Code skill in their terminal. Here the
reading layer becomes a server-side LLM call, so the digest keeps updating on a
schedule instead of stalling whenever nobody runs it.

The fetch and read halves are deliberately independent: papers are stored the
moment they are fetched, with ``read_status="pending"`` when no LLM provider is
configured. A paper that visibly has not been read yet is correct; a fabricated
summary or an invented score is not.

Only the fetch half is re-exported here. The reading and persistence layers keep
their own module-level imports so this package can be imported without pulling
in an LLM client.
"""

from __future__ import annotations

from pharos.daily.directions import (
    ARXIV_CATEGORIES,
    DIRECTIONS,
    MAX_PAPERS_PER_DAY,
    match_directions,
)
from pharos.daily.fetcher import FetchedPaper, fetch_for_date

__all__ = [
    "ARXIV_CATEGORIES",
    "DIRECTIONS",
    "FetchedPaper",
    "MAX_PAPERS_PER_DAY",
    "fetch_for_date",
    "match_directions",
]
