"""The Evidence Ledger — statements anchored to where they actually came from.

This module is the enforcement point for ``docs/RESEARCH_WORKFLOW.md`` §8. The
chain it protects is claim → evidence statement → paper identity → page + section
→ exact quote, and the two failures it exists to make impossible are:

* **A fabricated page number.** ``page_no`` is never taken from a client for a
  quote. It is *resolved*: the text is matched against the paper's extracted
  :class:`~pharos.db.models.PaperChunk` rows, and the page comes back from the
  chunk that actually contains it. For the other kinds a caller may name a page,
  but only a page that has a chunk — a page number no extraction ever produced is
  refused rather than stored. The schema's CHECK constraint backs this up; every
  write path here validates before it flushes so that constraint is a safety net
  and not the error the client sees.
* **A conflated authorship.** ``kind`` says who wrote the text — the paper
  (``quote``), a person (``note``), a deterministic pass (``rule_summary``), or a
  model (``model_inference``). Provenance columns are *required* for the two
  machine kinds and *refused* for ``note``, because a human note carrying
  ``model="gpt-x"`` is the same lie told backwards, and once both render as grey
  text in a panel the difference is unrecoverable.

Owner scoping is the third invariant, and it works the way it does everywhere
else in this codebase: the owner id is a required keyword, a row belonging to
someone else raises :class:`NotFound`, and the API turns that into a 404 rather
than a 403 — a 403 would confirm the id is real and turn the endpoint into an
oracle for walking ids across other users' research.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pharos.db.models import Evidence, Paper, PaperChunk, ResearchProject
from pharos.services import annotate

#: Who wrote the text. See the module docstring; this is not a style enum.
KINDS = frozenset({"quote", "note", "rule_summary", "model_inference"})

#: How precisely the text is placed. Deliberately the same three words the
#: resolver returns, so mapping a :class:`Placement` onto a row is an identity.
LOCATORS = frozenset({"page", "abstract_only", "unlocated"})

#: The kinds a machine produced, and which therefore must say which machine.
#: ``rule_summary`` is in here even though it involves no model: the contract
#: asks every *automated* product to record provider/model/version/input hash,
#: and a deterministic pass naming itself ("pharos"/"rules@1") is what keeps it
#: distinguishable from a human-authored row, which records the same fact by
#: leaving all four NULL.
AUTOMATED_KINDS = frozenset({"rule_summary", "model_inference"})

#: The four provenance columns, all-or-nothing. Half-recorded provenance cannot
#: be audited: knowing a model wrote something without knowing which input it
#: saw is not a weaker record, it is an unusable one.
PROVENANCE_FIELDS = ("provider", "model", "workflow_version", "input_sha256")

#: A passage, not a document. Matches ``annotate.MAX_TEXT`` because a quote and a
#: highlight are the same act performed in two surfaces.
MAX_TEXT = 20_000

#: What the evidence is offered as support for — a sentence or a paragraph.
MAX_STATEMENT = 20_000

#: Shortest quote that can meaningfully identify a position. Below this a match
#: says nothing: "the" is on every page, so resolving it to page 1 would be a
#: precise-looking answer to a question that was never asked.
MIN_QUOTE_CHARS = 8

#: Sanity bound on a supplied page, mirroring ``annotate.MAX_PAGE``. The real
#: check is that a chunk exists for the page; this only stops an absurd integer
#: reaching the query.
MAX_PAGE = 100_000

_PROVIDER_LIMITS = {
    "provider": 32,
    "model": 64,
    "workflow_version": 16,
    "input_sha256": 64,
}


class EvidenceError(Exception):
    """Base for failures this service reports, each carrying its HTTP status.

    Mapped in one place by ``pharos.api.evidence``'s route class, so a new
    endpoint cannot forget the translation and turn a 404 into a 500 traceback.
    """

    status_code = 400


class NotFound(EvidenceError):
    """The row does not exist, or belongs to someone else — one class for both."""

    status_code = 404


class Invalid(EvidenceError):
    """Malformed input: an unknown kind, an impossible page, partial provenance."""

    status_code = 400


class QuoteNotInPaper(EvidenceError):
    """The paper has extracted text and the quote is not in any of it.

    Deliberately its own class with its own status. This is not "your request was
    malformed" — the request is well formed and the caller may well believe it —
    it is a statement about the world that contradicts the stored paper, which is
    what 409 is for. It is also deliberately not 422: FastAPI already spends 422
    on request-model validation, and a client needs to tell "you sent the wrong
    shape" apart from "this quotation is not in this paper".

    There is no override flag. A caller who cannot place a quote is not offered a
    way to store it as a quote anyway, because a ``quote`` row asserts the paper
    said this; if that cannot be shown, the honest record is a ``note``, which
    says a person wrote it down.
    """

    status_code = 409


# --------------------------------------------------------------- normalisation


#: Typographic characters a PDF paste carries that mean the same thing as their
#: ASCII spelling. Folding them is *widening the match across typography*, not
#: across meaning — which is the line this whole module is drawn around.
_FOLD = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "′": "'",
        "″": '"',
        # True hyphens that are not U+002D. NFKC leaves these alone.
        "‐": "-",
        "‑": "-",
        # Zero-width joiners and the BOM: invisible, and pasted more often than
        # anyone expects. NFKC does not remove them.
        "​": "",
        "‌": "",
        "‍": "",
        "﻿": "",
        # Discretionary hyphen: rendered only when the line breaks there, so it
        # is never part of the text a reader sees.
        "­": "",
    }
)

_WHITESPACE = re.compile(r"\s+")

#: A hyphen immediately before a line break. In a PDF viewer's clipboard this is
#: nearly always a word broken across two lines ("trans-\nformer"), not a real
#: compound — a real compound would not have a newline wedged inside it.
_LINE_BREAK_HYPHEN = re.compile(r"-[ \t\r]*\n[ \t]*")


def _collapse(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def normalise(value: str) -> str:
    """Fold a text down to the form both sides of a match are compared in.

    NFKC first, which is what turns the ``ﬁ`` ligature a PDF emits back into
    ``fi`` and full-width forms back into ASCII; then the typographic fold above;
    then whitespace collapsed to single spaces, which is the requirement that
    makes a quote pasted with hard line breaks match a chunk stored without them.

    Case is *not* folded. A ``quote`` asserts the paper said this, and a service
    that answered "page 7" for text whose capitalisation the paper does not share
    has already started rounding off what verbatim means. Refusing is the safe
    direction: it costs a user one retry, where a wrong page costs a reader their
    trust in every other citation.
    """
    return _collapse(unicodedata.normalize("NFKC", value).translate(_FOLD))


def _match_candidates(quote: str) -> tuple[str, ...]:
    """The normalised spellings of a quote that a chunk may legitimately hold.

    Two, and they differ only in what happened to a hyphen at a line break: the
    extractor that wrote the chunk may have joined the word back up or may have
    left the hyphen in place, and the viewer the user copied from had the same
    choice. Both candidates are still *exact substrings* — nothing here admits a
    synonym, a reordering or a dropped clause, which is the property that stops a
    paraphrase acquiring a page number.
    """
    prepared = unicodedata.normalize("NFKC", quote).translate(_FOLD)
    joined = _collapse(_LINE_BREAK_HYPHEN.sub("", prepared))
    literal = _collapse(prepared)
    return (joined,) if joined == literal else (joined, literal)


# ------------------------------------------------------------------- placement


#: The resolver outcome that has no ``locator``, because there is no honest one.
NOT_IN_PAPER = "not_in_paper"


@dataclass(frozen=True)
class Placement:
    """Where a piece of text sits in a paper, or the fact that it does not.

    ``outcome`` is the discriminant, and three of its four values are spelled
    exactly like the ``locator`` vocabulary — ``page``, ``abstract_only``,
    ``unlocated`` — so a placed result maps onto the row without a translation
    table that could drift. The fourth, ``not_in_paper``, has no locator on
    purpose: the whole point is that it cannot be quietly written down.

    Returned rather than raised because "where is this text?" is a question worth
    asking without committing to a write — the reader can preflight a quote and
    tell the user before they save. The *write* path does not get that latitude:
    :func:`create_evidence` turns ``not_in_paper`` into
    :class:`QuoteNotInPaper` so a caller cannot ignore it by omission.
    """

    outcome: str
    page_no: int | None = None
    chunk_id: str | None = None

    @property
    def placed(self) -> bool:
        return self.outcome != NOT_IN_PAPER


def _now() -> datetime:
    return datetime.now(UTC)


def _require_owner(user_id: str) -> str:
    """Reject a falsy owner before it can render as ``user_id IS NULL``.

    ``Paper.user_id`` is nullable so the pre-accounts migration could run, so a
    ``None`` threaded this far would silently match the legacy rows instead of
    failing. Mirrors the identical guards in ``library``/``organise``/``annotate``.
    """
    if not user_id:
        raise ValueError("user_id is required: every evidence query must be owner-scoped")
    return user_id


def require_paper(session: Session, paper_id: str, *, user_id: str) -> Paper:
    _require_owner(user_id)
    row = session.scalar(
        select(Paper).where(
            Paper.id == paper_id, Paper.user_id == user_id, Paper.deleted_at.is_(None)
        )
    )
    if row is None:
        raise NotFound("Paper not found")
    return row


def _require_project(session: Session, project_id: str, *, user_id: str) -> ResearchProject:
    """Owner-scoped project lookup, kept local rather than borrowed.

    ``projects.require_project`` would answer the same question, but it raises
    ``projects.NotFound`` — a sibling of this module's exception tree, not a
    member — which the evidence router's error mapping would not catch, and an
    uncaught one is a 500 where the contract promises a 404. It also eager-loads
    sources, artifacts and their literature results, which is a great deal of
    work for a yes/no.
    """
    _require_owner(user_id)
    row = session.scalar(
        select(ResearchProject).where(
            ResearchProject.id == project_id, ResearchProject.user_id == user_id
        )
    )
    if row is None:
        raise NotFound("Project not found")
    return row


def _has_chunks(session: Session, paper_id: str, *, user_id: str) -> bool:
    return (
        session.scalar(
            select(PaperChunk.id)
            .where(PaperChunk.paper_id == paper_id, PaperChunk.user_id == user_id)
            .limit(1)
        )
        is not None
    )


def _unplaced_locator(session: Session, paper: Paper, *, user_id: str) -> str:
    """The locator for text that is genuine but has no page.

    The distinction the contract draws is between *never had full text* and
    *have full text, do not know where in it*, and only the chunk rows can tell
    those apart. ``Paper.full_text`` deliberately does not participate: it is one
    flat run with the page breaks normalised away and it is capped, so it can
    neither yield a page nor prove a quote's absence.
    """
    if _has_chunks(session, paper.id, user_id=user_id):
        return "unlocated"
    return "abstract_only" if (paper.abstract or "").strip() else "unlocated"


def resolve_quote(session: Session, *, user_id: str, paper_id: str, quote: object) -> Placement:
    """Find the page a verbatim quote sits on, or say honestly why there is none.

    Four outcomes, and the third and fourth are the ones that matter:

    * found in a chunk → ``page``, with that chunk's page and id;
    * the paper has no chunks at all (a scan, a metadata-only Zotero import) →
      ``abstract_only`` when there is an abstract to rest on, else ``unlocated``;
    * the paper *has* chunks and the quote is in none of them → ``not_in_paper``.
      This is a finding, not a shrug. Downgrading it to ``unlocated`` would file
      "this text is not in this paper" under the same heading as "this text is in
      this paper somewhere", and the second is the one a reviewer would assume.

    When the quote occurs on several pages — a running header, a repeated figure
    caption — the earliest ``(page_no, ordinal)`` wins. That is not a guess
    dressed up as a fact: the text really is on that page, and a deterministic
    choice keeps two identical requests from disagreeing.
    """
    paper = require_paper(session, paper_id, user_id=user_id)
    text = _clean_text(quote, field="quote", limit=MAX_TEXT, required=True) or ""
    candidates = _match_candidates(text)
    if max((len(candidate) for candidate in candidates), default=0) < MIN_QUOTE_CHARS:
        raise Invalid(f"a quote must contain at least {MIN_QUOTE_CHARS} characters")

    chunks = session.scalars(
        select(PaperChunk)
        .where(PaperChunk.paper_id == paper.id, PaperChunk.user_id == user_id)
        .order_by(PaperChunk.page_no, PaperChunk.ordinal, PaperChunk.id)
    ).all()
    if not chunks:
        return Placement(_unplaced_locator(session, paper, user_id=user_id))

    for chunk in chunks:
        haystack = normalise(chunk.text or "")
        if any(candidate in haystack for candidate in candidates):
            return Placement("page", page_no=int(chunk.page_no), chunk_id=chunk.id)
    return Placement(NOT_IN_PAPER)


# ------------------------------------------------------------------ validation


def _clean_text(value: object, *, field: str, limit: int, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise Invalid(f"{field} cannot be empty")
        return None
    if not isinstance(value, str):
        raise Invalid(f"{field} must be a string")
    cleaned = value.strip()
    if not cleaned:
        if required:
            raise Invalid(f"{field} cannot be empty")
        return None
    if len(cleaned) > limit:
        raise Invalid(f"{field} must be at most {limit} characters")
    return cleaned


def _clean_kind(value: object) -> str:
    if not isinstance(value, str) or value.strip() not in KINDS:
        raise Invalid(f"kind must be one of {sorted(KINDS)}")
    return value.strip()


def _clean_page(value: object) -> int:
    # ``bool`` is an ``int`` subclass, so ``True`` would otherwise arrive as
    # page 1 — a nonsense request accepted as a plausible one.
    if not isinstance(value, int) or isinstance(value, bool):
        raise Invalid("page_no must be an integer")
    if value < 1 or value > MAX_PAGE:
        raise Invalid(f"page_no must be between 1 and {MAX_PAGE}")
    return value


def _require_chunk_for_page(
    session: Session, *, user_id: str, paper_id: str, page_no: int
) -> PaperChunk:
    """The chunk backing a caller-supplied page, or a refusal.

    This is the anti-fabrication rule for every kind that is not a quote. A note
    or a model inference may legitimately name the page it is about, but only a
    page an extraction actually produced: ``locator='page'`` promises the reader
    that turning to that page will find text Pharos has seen, and a page number
    with nothing behind it is precisely the "看似精确的页码" the contract forbids.
    """
    chunk = session.scalar(
        select(PaperChunk)
        .where(
            PaperChunk.paper_id == paper_id,
            PaperChunk.user_id == user_id,
            PaperChunk.page_no == page_no,
        )
        .order_by(PaperChunk.ordinal, PaperChunk.id)
        .limit(1)
    )
    if chunk is None:
        raise Invalid(
            f"page {page_no} has no extracted text for this paper; "
            "evidence cannot claim a page the extraction never produced"
        )
    return chunk


def _clean_provenance(kind: str, values: dict[str, object]) -> dict[str, str | None]:
    """Apply the provenance rule for one ``kind``, in both directions.

    Required for :data:`AUTOMATED_KINDS`, refused for ``note``, and optional for
    ``quote``. That asymmetry is not an oversight — the rule is about *who
    authored the text*. A note's author is the human sitting there, so naming a
    model is a false attribution. A quote's author is the paper, so provenance
    describes only which pass happened to carry it across, which is useful to
    record and dishonest to require.

    Whatever the kind, provenance is all-or-nothing (see
    :data:`PROVENANCE_FIELDS`).
    """
    cleaned = {
        field: _clean_text(values.get(field), field=field, limit=_PROVIDER_LIMITS[field])
        for field in PROVENANCE_FIELDS
    }
    present = sorted(field for field, value in cleaned.items() if value is not None)

    if kind in AUTOMATED_KINDS:
        missing = sorted(set(PROVENANCE_FIELDS) - set(present))
        if missing:
            raise Invalid(
                f"kind '{kind}' is machine-produced and must record its provenance; "
                f"missing: {missing}"
            )
        return cleaned
    if kind == "note" and present:
        raise Invalid(
            "kind 'note' is human-authored and must not carry machine provenance; "
            f"remove: {present}"
        )
    if present and len(present) != len(PROVENANCE_FIELDS):
        raise Invalid(
            "provenance is all-or-nothing; "
            f"got {present}, missing {sorted(set(PROVENANCE_FIELDS) - set(present))}"
        )
    return cleaned


def _clean_rects(value: object, *, locator: str) -> str | None:
    """Validate optional geometry, and refuse it where it could not exist.

    Reuses ``annotate``'s rectangle validation rather than growing a second copy:
    the model documents ``rects`` as "the same convention as ``Highlight``", and
    two implementations of one convention is how the reader ends up drawing
    evidence in one coordinate space and highlights in another.

    Rectangles on evidence that has no page are refused outright. A region is a
    position on a page; supplying one for ``unlocated`` text is the same
    invention as supplying the page itself, one level down.
    """
    if value is None:
        return None
    if locator != "page":
        raise Invalid("rects require locator 'page'; unplaced evidence has no region")
    try:
        return annotate.dump_rects(annotate.clean_rects(value))
    except annotate.Invalid as exc:
        raise Invalid(str(exc)) from exc


def _assert_locator_contract(locator: str, page_no: int | None) -> None:
    """Restate the schema CHECK before anything is flushed.

    The constraint (``page_no`` non-NULL iff ``locator='page'``) is the last line
    of defence and must stay that way, but a client must never *meet* it: an
    IntegrityError surfacing from a flush is a 500 with a traceback, and it also
    poisons the transaction, taking down whatever else the request was doing.
    Checking here means the same rule produces a clean 400.
    """
    if locator not in LOCATORS:
        raise Invalid(f"locator must be one of {sorted(LOCATORS)}")
    if (locator == "page") != (page_no is not None):
        raise Invalid(
            "page_no is set if and only if locator is 'page' "
            f"(got locator={locator!r}, page_no={page_no!r})"
        )


# ----------------------------------------------------------------------- CRUD


def create_evidence(
    session: Session,
    *,
    user_id: str,
    paper_id: str,
    kind: object,
    text: object,
    project_id: str | None = None,
    statement: object = None,
    page_no: object = None,
    rects: object = None,
    provider: object = None,
    model: object = None,
    workflow_version: object = None,
    input_sha256: object = None,
) -> Evidence:
    """Record one statement against a paper, placed as precisely as is honest.

    The placement rule differs by kind, and that is the heart of this function:

    * ``quote`` — ``page_no`` may **not** be supplied. It is resolved from the
      paper's chunks, so the only page a quote can ever carry is one where the
      text was actually found. A quote that resolves to ``not_in_paper`` raises
      :class:`QuoteNotInPaper` rather than being filed as ``unlocated``.
    * everything else — the caller may name a page, and it is accepted only if a
      chunk exists for it (:func:`_require_chunk_for_page`). With no page, the
      locator falls back to ``abstract_only`` or ``unlocated`` depending on
      whether the paper ever had full text at all.
    """
    paper = require_paper(session, paper_id, user_id=user_id)
    clean_kind = _clean_kind(kind)
    clean_statement = _clean_text(statement, field="statement", limit=MAX_STATEMENT)
    provenance = _clean_provenance(
        clean_kind,
        {
            "provider": provider,
            "model": model,
            "workflow_version": workflow_version,
            "input_sha256": input_sha256,
        },
    )
    if project_id is not None:
        _require_project(session, project_id, user_id=user_id)

    if clean_kind == "quote":
        if page_no is not None:
            raise Invalid(
                "page_no cannot be supplied for a quote; it is resolved from the "
                "paper's extracted pages"
            )
        clean_body = _clean_text(text, field="text", limit=MAX_TEXT, required=True) or ""
        placement = resolve_quote(session, user_id=user_id, paper_id=paper.id, quote=clean_body)
        if not placement.placed:
            raise QuoteNotInPaper(
                "This text does not appear in the extracted pages of this paper. "
                "Record it as a note if a person wrote it, or re-extract the paper "
                "if the text is missing from the extraction."
            )
        locator, resolved_page, chunk_id = (
            placement.outcome,
            placement.page_no,
            placement.chunk_id,
        )
    else:
        clean_body = _clean_text(text, field="text", limit=MAX_TEXT, required=True) or ""
        chunk_id = None
        if page_no is None:
            locator, resolved_page = _unplaced_locator(session, paper, user_id=user_id), None
        else:
            resolved_page = _clean_page(page_no)
            chunk = _require_chunk_for_page(
                session, user_id=user_id, paper_id=paper.id, page_no=resolved_page
            )
            locator, chunk_id = "page", chunk.id

    _assert_locator_contract(locator, resolved_page)
    row = Evidence(
        user_id=user_id,
        paper_id=paper.id,
        project_id=project_id,
        chunk_id=chunk_id,
        kind=clean_kind,
        locator=locator,
        page_no=resolved_page,
        rects=_clean_rects(rects, locator=locator),
        text=clean_body,
        statement=clean_statement,
        **provenance,
    )
    session.add(row)
    session.flush()
    return row


def require_evidence(session: Session, evidence_id: str, *, user_id: str) -> Evidence:
    """One of the caller's evidence rows, or the 404 that hides everyone else's."""
    _require_owner(user_id)
    row = session.scalar(
        select(Evidence).where(Evidence.id == evidence_id, Evidence.user_id == user_id)
    )
    if row is None:
        raise NotFound("Evidence not found")
    return row


def list_evidence(
    session: Session,
    *,
    user_id: str,
    paper_id: str | None = None,
    project_id: str | None = None,
    kind: object = None,
    locator: object = None,
) -> list[Evidence]:
    """The caller's evidence, optionally narrowed, oldest first.

    ``paper_id`` and ``project_id`` are resolved through the owner-scoped
    lookups rather than dropped straight into the WHERE clause. Filtering alone
    would already be safe — ``Evidence.user_id`` is on the row — but it would
    answer an empty list for another user's paper, which is the same shape as
    "your paper, no evidence yet"; routing through the lookup makes it a 404 and
    keeps the id-probing answer uniform with every other endpoint.
    """
    _require_owner(user_id)
    statement = select(Evidence).where(Evidence.user_id == user_id)
    if paper_id is not None:
        paper = require_paper(session, paper_id, user_id=user_id)
        statement = statement.where(Evidence.paper_id == paper.id)
    if project_id is not None:
        project = _require_project(session, project_id, user_id=user_id)
        statement = statement.where(Evidence.project_id == project.id)
    if kind is not None:
        statement = statement.where(Evidence.kind == _clean_kind(kind))
    if locator is not None:
        if not isinstance(locator, str) or locator not in LOCATORS:
            raise Invalid(f"locator must be one of {sorted(LOCATORS)}")
        statement = statement.where(Evidence.locator == locator)
    return list(session.scalars(statement.order_by(Evidence.created_at, Evidence.id)))


def update_evidence(
    session: Session, *, user_id: str, evidence_id: str, changes: dict[str, object]
) -> Evidence:
    """Edit an evidence row, without letting an edit invent a placement.

    Three fields are deliberately **not** editable:

    * ``kind`` — who wrote the text is not something a PATCH discovers. Flipping
      a ``note`` to a ``quote`` would turn a human's paraphrase into a claim that
      the paper said it, while keeping every other column intact. Being wrong
      about the kind is a delete and a re-create.
    * the provenance columns — they record what produced the row *then*. Making
      them patchable is a way to relabel a person's note as a model's output
      afterwards, which is the exact attribution failure the columns exist for.
    * ``page_no`` on a quote — it is derived, and it is re-derived below.

    ``text`` is editable, and on a quote that edit **re-resolves the placement**.
    A design where the text moved but the page stayed would be the cheapest
    fabrication route in the whole subsystem: keep page 7, replace the quotation
    with something the paper never said, and the row still reads as verified.
    """
    row = require_evidence(session, evidence_id, user_id=user_id)
    allowed = {"text", "statement", "project_id", "page_no", "rects"}
    unknown = set(changes) - allowed
    if unknown:
        raise Invalid(f"unexpected evidence fields: {sorted(unknown)}")
    if not changes:
        raise Invalid("No evidence fields provided")

    if "project_id" in changes:
        project_id = changes["project_id"]
        if project_id is None:
            row.project_id = None
        elif isinstance(project_id, str):
            row.project_id = _require_project(session, project_id, user_id=user_id).id
        else:
            raise Invalid("project_id must be a string or null")

    if "statement" in changes:
        row.statement = _clean_text(changes["statement"], field="statement", limit=MAX_STATEMENT)

    if row.kind == "quote":
        if "page_no" in changes:
            raise Invalid(
                "page_no cannot be set on a quote; it is resolved from the paper's extracted pages"
            )
        if "text" in changes:
            body = _clean_text(changes["text"], field="text", limit=MAX_TEXT, required=True)
            placement = resolve_quote(session, user_id=user_id, paper_id=row.paper_id, quote=body)
            if not placement.placed:
                raise QuoteNotInPaper(
                    "The edited text does not appear in the extracted pages of this "
                    "paper; a quote cannot keep a page it no longer matches."
                )
            row.text = body or ""
            row.locator = placement.outcome
            row.page_no = placement.page_no
            row.chunk_id = placement.chunk_id
            if row.locator != "page":
                # The re-resolution lost the page (the paper's chunks were
                # replaced by an extraction that no longer produces any), so any
                # stored region now points at nothing.
                row.rects = None
    else:
        if "text" in changes:
            row.text = (
                _clean_text(changes["text"], field="text", limit=MAX_TEXT, required=True) or ""
            )
        if "page_no" in changes:
            page_no = changes["page_no"]
            if page_no is None:
                row.locator = _unplaced_locator(
                    session, require_paper(session, row.paper_id, user_id=user_id), user_id=user_id
                )
                row.page_no = None
                row.chunk_id = None
                row.rects = None
            else:
                clean_page = _clean_page(page_no)
                chunk = _require_chunk_for_page(
                    session, user_id=user_id, paper_id=row.paper_id, page_no=clean_page
                )
                row.locator = "page"
                row.page_no = clean_page
                row.chunk_id = chunk.id

    if "rects" in changes:
        row.rects = _clean_rects(changes["rects"], locator=row.locator)

    _assert_locator_contract(row.locator, row.page_no)
    row.updated_at = _now()
    session.flush()
    return row


def delete_evidence(session: Session, *, user_id: str, evidence_id: str) -> None:
    """Remove one evidence row. No soft delete.

    Unlike a paper, evidence is small and re-creatable, and a recycle bin nobody
    opens is only a second place for the ledger to disagree with itself. A
    research decision worth keeping is recorded as a ``ProjectArtifact`` with a
    status, which is where the "keep the history of a rejected idea" convention
    already lives.
    """
    row = require_evidence(session, evidence_id, user_id=user_id)
    session.delete(row)
    session.flush()
