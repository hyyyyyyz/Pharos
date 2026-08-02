"""Research-project persistence and owner-scoped workflow operations."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (
    InstrumentedAttribute,
    Session,
    selectinload,
    with_loader_criteria,
)

from pharos.daily import reader
from pharos.db.models import (
    LiteratureResult,
    LiteratureSearch,
    Paper,
    ProjectArtifact,
    ProjectSource,
    ResearchProject,
)
from pharos.services import discovery

PROJECT_STATUSES = frozenset({"active", "archived"})
PROJECT_STAGES = (
    "discovery",
    "ideation",
    "planning",
    "experimentation",
    "analysis",
    "claims",
    "drafting",
    "review",
    "complete",
)
ARTIFACT_TYPES = frozenset(
    {"hypothesis", "experiment_plan", "result", "claim", "draft", "review"}
)
ARTIFACT_STATUSES = frozenset({"draft", "ready", "verified", "rejected"})

MAX_PROJECT_NAME = 256
MAX_DESCRIPTION = 50_000
MAX_QUESTION = 20_000
MAX_QUERY = 500
MAX_NOTE = 20_000
MAX_ARTIFACT_TITLE = 512
MAX_ARTIFACT_BODY = 500_000


class ProjectError(Exception):
    status_code = 400


class NotFound(ProjectError):
    status_code = 404


class Invalid(ProjectError):
    status_code = 400


class Conflict(ProjectError):
    status_code = 409


class ProviderUnavailable(ProjectError):
    status_code = 503


def _now() -> datetime:
    return datetime.now(UTC)


def _owner(user_id: str) -> str:
    if not user_id:
        raise ValueError("user_id is required for every project query")
    return user_id


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def load_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default
    return value


def _text(
    value: object,
    *,
    field: str,
    limit: int,
    required: bool = False,
    nullable: bool = False,
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise Invalid(f"{field} must be a string")
    cleaned = value.strip()
    if required and not cleaned:
        raise Invalid(f"{field} cannot be empty")
    if len(cleaned) > limit:
        raise Invalid(f"{field} must be at most {limit} characters")
    return cleaned


def _project_options(user_id: str):
    return (
        selectinload(ResearchProject.sources).selectinload(ProjectSource.result),
        selectinload(ResearchProject.artifacts),
        with_loader_criteria(
            ProjectSource, ProjectSource.user_id == user_id, include_aliases=True
        ),
        with_loader_criteria(
            ProjectArtifact, ProjectArtifact.user_id == user_id, include_aliases=True
        ),
        with_loader_criteria(
            LiteratureResult, LiteratureResult.user_id == user_id, include_aliases=True
        ),
    )


def require_project(session: Session, project_id: str, *, user_id: str) -> ResearchProject:
    _owner(user_id)
    row = session.scalar(
        select(ResearchProject)
        .where(ResearchProject.id == project_id, ResearchProject.user_id == user_id)
        .options(*_project_options(user_id))
    )
    if row is None:
        raise NotFound("Project not found")
    return row


def create_project(
    session: Session,
    *,
    user_id: str,
    name: object,
    description: object = "",
    research_question: object = "",
) -> ResearchProject:
    _owner(user_id)
    row = ResearchProject(
        user_id=user_id,
        name=_text(name, field="name", limit=MAX_PROJECT_NAME, required=True),
        description=_text(description, field="description", limit=MAX_DESCRIPTION) or "",
        research_question=_text(
            research_question, field="research_question", limit=MAX_QUESTION
        )
        or "",
    )
    session.add(row)
    session.flush()
    row.sources = []
    row.artifacts = []
    return row


def list_projects(session: Session, *, user_id: str) -> list[ResearchProject]:
    _owner(user_id)
    return list(
        session.scalars(
            select(ResearchProject)
            .where(ResearchProject.user_id == user_id)
            .options(*_project_options(user_id))
            .order_by(ResearchProject.created_at.desc(), ResearchProject.id)
        )
    )


def update_project(
    session: Session, *, user_id: str, project_id: str, changes: dict[str, object]
) -> ResearchProject:
    row = require_project(session, project_id, user_id=user_id)
    allowed = {"name", "description", "research_question", "status", "stage"}
    unknown = set(changes) - allowed
    if unknown:
        raise Invalid(f"unexpected project fields: {sorted(unknown)}")
    if not changes:
        raise Invalid("No project fields provided")
    if "name" in changes:
        row.name = _text(
            changes["name"], field="name", limit=MAX_PROJECT_NAME, required=True
        )
    if "description" in changes:
        row.description = (
            _text(changes["description"], field="description", limit=MAX_DESCRIPTION) or ""
        )
    if "research_question" in changes:
        row.research_question = (
            _text(
                changes["research_question"],
                field="research_question",
                limit=MAX_QUESTION,
            )
            or ""
        )
    if "status" in changes:
        status = _text(changes["status"], field="status", limit=16, required=True)
        if status not in PROJECT_STATUSES:
            raise Invalid(f"status must be one of {sorted(PROJECT_STATUSES)}")
        row.status = status
    if "stage" in changes:
        # Manual backwards movement is a first-class research operation: a
        # failed experiment may send the project back to ideation. It is also
        # allowed while archived because this PATCH only corrects persisted
        # workflow metadata; it does not launch work. ``advance`` remains the
        # guarded active-only shortcut for forward execution.
        stage = _text(changes["stage"], field="stage", limit=32, required=True)
        if stage not in PROJECT_STAGES:
            raise Invalid(f"stage must be one of {list(PROJECT_STAGES)}")
        row.stage = stage
    row.updated_at = _now()
    session.flush()
    return row


def delete_project(session: Session, *, user_id: str, project_id: str) -> None:
    row = require_project(session, project_id, user_id=user_id)
    session.delete(row)
    session.flush()


def advance_project(session: Session, *, user_id: str, project_id: str) -> ResearchProject:
    row = require_project(session, project_id, user_id=user_id)
    if row.status != "active":
        raise Conflict("Archived projects cannot advance; reactivate the project first")
    try:
        index = PROJECT_STAGES.index(row.stage)
    except ValueError as exc:
        raise Conflict(f"Project has an unknown stage: {row.stage}") from exc
    if index == len(PROJECT_STAGES) - 1:
        raise Conflict("Project is already complete")
    row.stage = PROJECT_STAGES[index + 1]
    row.updated_at = _now()
    session.flush()
    return row


def run_search(
    session: Session,
    *,
    user_id: str,
    query: object,
    sources: list[str],
    limit: int,
    project_id: str | None = None,
) -> LiteratureSearch:
    _owner(user_id)
    clean_query = _text(query, field="query", limit=MAX_QUERY, required=True)
    if len(clean_query) < 2:
        raise Invalid("query must contain at least 2 characters")
    if not 1 <= limit <= 50:
        raise Invalid("limit must be between 1 and 50")
    if not sources:
        raise Invalid("at least one source is required")
    if len(sources) != len(set(sources)):
        raise Invalid("sources must not contain duplicates")
    unknown = set(sources) - discovery.SOURCES
    if unknown:
        raise Invalid(f"unsupported sources: {sorted(unknown)}")
    if project_id is not None:
        require_project(session, project_id, user_id=user_id)

    search = LiteratureSearch(
        user_id=user_id,
        project_id=project_id,
        query=clean_query,
        sources=dump_json(sources),
        status="running",
    )
    session.add(search)
    session.flush()

    batch = discovery.discover(clean_query, sources, limit)
    for rank, paper in enumerate(batch.papers, start=1):
        summary = discovery.rule_summary(paper.title, paper.abstract)
        search.results.append(
            LiteratureResult(
                user_id=user_id,
                dedup_key=discovery.dedup_key(paper),
                title=paper.title,
                authors=dump_json(list(paper.authors)),
                abstract=paper.abstract,
                year=paper.year,
                venue=paper.venue,
                doi=paper.doi,
                url=paper.url,
                pdf_url=paper.pdf_url,
                sources=dump_json(list(paper.sources)),
                source_ids=dump_json(discovery.source_ids_dict(paper)),
                citation_count=paper.citation_count,
                rank=rank,
                **summary,
            )
        )
    search.result_count = len(batch.papers)
    search.errors = dump_json(batch.errors)
    if len(batch.errors) == len(sources):
        search.status = "error"
    elif batch.errors:
        search.status = "partial"
    else:
        search.status = "complete"
    search.completed_at = _now()
    session.flush()
    return search


def _search_options(user_id: str):
    return (
        selectinload(LiteratureSearch.results),
        with_loader_criteria(
            LiteratureResult, LiteratureResult.user_id == user_id, include_aliases=True
        ),
    )


def list_searches(
    session: Session, *, user_id: str, project_id: str | None = None
) -> list[LiteratureSearch]:
    _owner(user_id)
    statement = select(LiteratureSearch).where(LiteratureSearch.user_id == user_id)
    if project_id is not None:
        require_project(session, project_id, user_id=user_id)
        statement = statement.where(LiteratureSearch.project_id == project_id)
    return list(
        session.scalars(
            statement.options(*_search_options(user_id)).order_by(
                LiteratureSearch.created_at.desc(), LiteratureSearch.id
            )
        )
    )


def require_search(session: Session, search_id: str, *, user_id: str) -> LiteratureSearch:
    _owner(user_id)
    row = session.scalar(
        select(LiteratureSearch)
        .where(LiteratureSearch.id == search_id, LiteratureSearch.user_id == user_id)
        .options(*_search_options(user_id))
    )
    if row is None:
        raise NotFound("Search not found")
    return row


def require_result(session: Session, result_id: str, *, user_id: str) -> LiteratureResult:
    _owner(user_id)
    row = session.scalar(
        select(LiteratureResult).where(
            LiteratureResult.id == result_id, LiteratureResult.user_id == user_id
        )
    )
    if row is None:
        raise NotFound("Literature result not found")
    return row


def analyze_result(
    session: Session, *, user_id: str, result_id: str
) -> LiteratureResult:
    """Replace heuristic fields only after a validated real provider response."""
    result = require_result(session, result_id, user_id=user_id)
    if not result.abstract.strip():
        raise Invalid("Literature result has no abstract to analyze")
    try:
        reading = reader.read_paper(
            result.title,
            result.abstract,
            authors=load_json(result.authors, []),
        )
    except reader.ReaderUnavailable as exc:
        # Preserve every rules field. The failed optional upgrade changes no row.
        raise Conflict(str(exc)) from exc
    except reader.ReaderError as exc:
        raise ProviderUnavailable(str(exc)) from exc

    result.analysis_mode = "llm"
    result.analysis_model = reading.model
    result.analysis_warning = None
    result.summary_zh = reading.summary_zh
    result.contribution = reading.highlights["contribution"]
    result.core_trick = reading.highlights["innovation"]
    result.method = reading.highlights["method"]
    result.results = reading.highlights["results"]
    # The daily reader is constrained to the supplied abstract but does not ask
    # for limitations. Keeping the extractive rules value is more honest than
    # making a model fill a field it was never asked to produce.
    session.flush()
    return result


# ------------------------------------------------- source ↔ library identity
#
# A ``ProjectSource`` points at a ``LiteratureResult``: a discovery hit, which
# is a title and an abstract and nothing a page number could point into. When
# the user actually holds the PDF, ``ProjectSource.paper_id`` says so, and every
# piece of evidence drawn from that source can be anchored to a real page rather
# than marked ``abstract_only``. The functions below are the two ways that link
# gets made — a deliberate one, and an automatic one that only fires when the
# two rows carry the *same identifier* and so are the same paper as a matter of
# fact rather than of judgement.

#: The version suffix on an arXiv id (``2301.12345v2``). Both sides of a
#: comparison are already meant to arrive stripped — ``discovery`` strips it
#: from a search hit's external id, and every writer of ``Paper.arxiv_id``
#: (``metadata.find_arxiv_id``, ``enrich.enrich_by_arxiv``, the daily import)
#: strips it too. Stripping once more here is free and means a row written by
#: some future path that forgets still matches: ``2301.12345`` and
#: ``2301.12345v2`` are the same paper, and declining to link them would be
#: pedantry rather than caution.
_ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)

#: How many rows the SQL prefilter may hand back per identifier. We only ever
#: need to know "one match" or "more than one", so the bound exists to keep an
#: accidental library-wide scan out of what is meant to be a cheap side effect
#: of saving a source. It is a little above 2 because the prefilter is
#: deliberately loose (see ``_candidates``) and a rejected row must not be able
#: to crowd out a real one.
_CANDIDATE_LIMIT = 8

#: LIKE metacharacters, escaped so an identifier containing one is matched
#: literally rather than turning into a wildcard scan. No real DOI or arXiv id
#: contains these; a corrupt ``source_ids`` blob can.
_LIKE_ESCAPE = str.maketrans({"\\": r"\\", "%": r"\%", "_": r"\_"})


def _identity_key(value: object) -> str | None:
    """Fold an identifier to the form both sides of a comparison must share.

    ``str.lower`` rather than ``str.casefold`` on purpose: the comparison runs
    in SQL against ``py_lower``, the SQLite function ``db.session`` registers
    precisely so that SQL and Python fold text identically (see the comment
    there). ``py_lower`` is ``str.lower``; casefolding here would reintroduce
    the mismatch it exists to remove.

    Case-insensitivity is not defensive polish, it is required by how the two
    columns are written. DOIs are case-insensitive by specification, and this
    codebase stores them inconsistently: ``metadata.find_doi`` and
    ``discovery._doi`` lower-case, but ``enrich._normalise_doi`` (the CrossRef
    path) and ``api.papers._clean_doi`` (a manual correction) keep whatever the
    registry or the user typed. So ``LiteratureResult.doi`` is always
    lower-case while ``Paper.doi`` may not be, and ``==`` would miss exactly the
    papers a user had bothered to correct by hand. For arXiv, the two spellings
    of a legacy id differ only in the subject class's case
    (``math.GT/0309136`` vs ``math.gt/0309136``) — ``metadata`` carries a whole
    function to canonicalise that, which is evidence both are in circulation.
    No two *distinct* identifiers of either kind differ only by case, so folding
    cannot manufacture a match.

    Prefix stripping is deliberately absent. Every writer of both columns
    already reduces ``https://doi.org/10.x`` and ``arXiv:2301.12345`` to the
    bare form before storing, and adding a fifth prefix-stripper to the four
    that exist would be inventing a scheme rather than following one.
    """
    if not isinstance(value, str):
        return None
    return _ARXIV_VERSION.sub("", value.strip()).lower() or None


def _result_identifiers(result: LiteratureResult) -> tuple[str | None, str | None]:
    """The (DOI, arXiv id) keys a discovery hit can be matched on.

    The arXiv id lives inside ``source_ids`` rather than in a column of its own:
    a result is a record merged across providers, so its ids are kept as a
    ``{provider: id}`` map. ``discovery.ARXIV`` is the only key in that map that
    identifies the *paper* — an OpenAlex work id (``W123``) names a provider's
    row about the paper and says nothing about what is in the library.
    """
    source_ids = load_json(result.source_ids, {})
    arxiv = source_ids.get(discovery.ARXIV) if isinstance(source_ids, dict) else None
    return _identity_key(result.doi), _identity_key(arxiv)


def _match_library_paper(
    session: Session, result: LiteratureResult, *, user_id: str
) -> Paper | None:
    """Find the one live library paper that *is* ``result``, or None.

    Identifier equality only. A shared title is not identity — papers are
    revised, renamed, and republished under the same title, and two versions of
    one paper have different page numbers, which is the exact thing this link
    exists to get right. A wrong link is worse than no link: it produces a
    citation that points confidently at the wrong page, and no reader can tell
    from the outside.

    Ambiguity in either direction declines. If a DOI matches two library rows
    (the user uploaded the preprint and the published version), or the DOI and
    the arXiv id each match a *different* row, then the library itself does not
    say which paper this source is, and guessing would silently pick page
    numbers from one of two documents. Declining leaves the source
    abstract-only, which is honest and which the user can correct in one call.

    Soft-deleted papers are excluded: reaching into the recycle bin to anchor a
    project's evidence is not something the user asked for.
    """
    doi, arxiv = _result_identifiers(result)
    # DOI first, mirroring ``library._enrich``'s reasoning that CrossRef
    # describes the version of record while arXiv describes a preprint. The
    # order only decides anything when the two disagree, and disagreement is
    # ambiguity, which is handled by counting rather than by precedence.
    candidates: dict[str, Paper] = {}
    for column, key in ((Paper.doi, doi), (Paper.arxiv_id, arxiv)):
        if key is None:
            continue
        for paper in _candidates(session, column, key, user_id=user_id):
            # SQL narrowed; Python decides. Same division of labour as the daily
            # feed's keyword prefilter, and for the same reason: the normalising
            # rule lives in ``_identity_key`` and only Python can apply all of
            # it, so letting SQL be the authority would put half the rule in a
            # WHERE clause where it would quietly drift.
            if _identity_key(getattr(paper, column.key)) == key:
                candidates[paper.id] = paper
        if len(candidates) > 1:
            break
    if len(candidates) != 1:
        return None
    return next(iter(candidates.values()))


def _candidates(
    session: Session,
    column: InstrumentedAttribute[str | None],
    key: str,
    *,
    user_id: str,
) -> list[Paper]:
    """The rows worth comparing ``key`` against, owner-scoped and bounded.

    The prefilter is deliberately a shade looser than the comparison it feeds.
    SQLite has no regular expressions, so the version suffix ``_identity_key``
    strips cannot be stripped in SQL: a stored ``2301.12345v2`` would never
    equal a normalised ``2301.12345``. The second arm recovers exactly that
    case, and the exact test in the caller discards anything it let through
    (``2301.12345version-control`` is not an arXiv id).

    It is a ``v``-anchored LIKE rather than an open ``key%`` prefix because
    prefixes genuinely collide: ``10.1234/x.1`` is a prefix of ``10.1234/x.12``,
    and a bare prefix scan would spend the row budget on unrelated papers and
    could push the real match past the limit.

    Every writer of both columns already stores the version-stripped form, so in
    practice the second arm never fires. It costs one OR, and means the day some
    path forgets, the link is still made rather than silently lost.

    On cost: neither arm can use an index on the identifier — ``py_lower(col)``
    is a functional expression, and ``Paper.arxiv_id`` carries no index at all
    (only ``Paper.doi`` does). What keeps this cheap is ``Paper.user_id``, which
    is indexed, so the scan is bounded by one person's library rather than the
    table. That is fine at library scale and is why the limit above exists
    anyway.
    """
    folded = func.py_lower(column)
    return list(
        session.scalars(
            select(Paper)
            .where(
                Paper.user_id == user_id,
                Paper.deleted_at.is_(None),
                or_(
                    folded == key,
                    folded.like(f"{key.translate(_LIKE_ESCAPE)}v%", escape="\\"),
                ),
            )
            .order_by(Paper.added_at, Paper.id)
            .limit(_CANDIDATE_LIMIT)
        )
    )


def _autolink(
    session: Session, source: ProjectSource, result: LiteratureResult, *, user_id: str
) -> bool:
    """Link ``source`` to the paper it demonstrably is. True if it changed."""
    if source.paper_id is not None:
        # Never overwrite an existing link. It may be one the user made by hand,
        # and their judgement outranks an identifier match.
        return False
    paper = _match_library_paper(session, result, user_id=user_id)
    if paper is None:
        return False
    source.paper_id = paper.id
    return True


def _require_library_paper(session: Session, paper_id: object, *, user_id: str) -> Paper:
    """Resolve a client-supplied paper id to one of *this user's* live papers.

    This is the one place in this module where an id from one table is attached
    to a row in another, so it restates the library's own discipline rather than
    trusting it from a distance: the owner predicate goes in the WHERE clause,
    and a miss raises ``NotFound``.

    404 rather than 403 for another user's paper, matching
    ``LibraryService.get_paper``. A 403 would confirm that the id names a real
    paper, which is enough to enumerate someone else's library one guess at a
    time; a 404 makes "no such paper" and "not yours" the same answer.

    A soft-deleted paper is a miss too. It is one the user has said they do not
    want, and anchoring a project's evidence to it would mean the next purge
    silently clears the link (the FK is ON DELETE SET NULL) leaving nothing to
    say it was ever there. Restoring it first is one click and keeps the link
    truthful.
    """
    clean = _text(paper_id, field="paper_id", limit=32, required=True)
    row = session.scalar(
        select(Paper).where(
            Paper.id == clean,
            Paper.user_id == user_id,
            Paper.deleted_at.is_(None),
        )
    )
    if row is None:
        raise NotFound("Paper not found")
    return row


def add_source(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    result_id: str,
    note: object = None,
) -> ProjectSource:
    project = require_project(session, project_id, user_id=user_id)
    result = require_result(session, result_id, user_id=user_id)
    clean_note = _text(note, field="note", limit=MAX_NOTE, nullable=True)
    existing = session.scalar(
        select(ProjectSource).where(
            ProjectSource.project_id == project.id,
            ProjectSource.result_id == result.id,
            ProjectSource.user_id == user_id,
        )
    )
    if existing is not None:
        # A repeat add is the user pointing at this result again, which makes it
        # a natural moment to retry the match: the PDF may have reached the
        # library since the first add.
        if _autolink(session, existing, result, user_id=user_id):
            session.flush()
        return existing
    matched = _match_library_paper(session, result, user_id=user_id)
    row = ProjectSource(
        user_id=user_id,
        project_id=project.id,
        result_id=result.id,
        note=clean_note,
        # Free and unambiguous, so it happens without being asked for: when the
        # library already holds a paper with this result's DOI or arXiv id, the
        # link is a fact about identifiers. Doing it here is what makes
        # page-anchored evidence available from the moment a source is saved.
        paper_id=matched.id if matched is not None else None,
    )
    try:
        # The check above makes ordinary retries cheap; the savepoint makes two
        # genuinely concurrent first-adds idempotent as well. Adding inside the
        # nested transaction is load-bearing because entering a savepoint
        # autoflushes anything already pending into the outer transaction.
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        if row in session:
            session.expunge(row)
        existing = session.scalar(
            select(ProjectSource).where(
                ProjectSource.project_id == project.id,
                ProjectSource.result_id == result.id,
                ProjectSource.user_id == user_id,
            )
        )
        if existing is None:
            raise
        return existing
    return row


def require_source(
    session: Session, *, user_id: str, project_id: str, source_id: str
) -> ProjectSource:
    require_project(session, project_id, user_id=user_id)
    row = session.scalar(
        select(ProjectSource)
        .join(LiteratureResult, ProjectSource.result_id == LiteratureResult.id)
        .where(
            ProjectSource.id == source_id,
            ProjectSource.project_id == project_id,
            ProjectSource.user_id == user_id,
            LiteratureResult.user_id == user_id,
        )
        .options(selectinload(ProjectSource.result))
    )
    if row is None:
        raise NotFound("Project source not found")
    return row


def update_source_note(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    source_id: str,
    note: object,
) -> ProjectSource:
    row = require_source(
        session, user_id=user_id, project_id=project_id, source_id=source_id
    )
    row.note = _text(note, field="note", limit=MAX_NOTE, nullable=True)
    session.flush()
    return row


def link_source_paper(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    source_id: str,
    paper_id: object,
) -> ProjectSource:
    """Declare that this source is the library paper ``paper_id``.

    Both ends are resolved through their own owner-scoped lookup before either
    is touched, so a paper id belonging to somebody else is a 404 and never a
    write. Re-linking an already linked source is allowed and simply replaces
    the target: a user correcting an automatic match is the case this exists for.

    Nothing forbids two sources in one project naming the same paper. That is a
    real situation — two discovery hits that failed to deduplicate are still one
    document — and refusing it would block the user from recording the truth.
    """
    source = require_source(
        session, user_id=user_id, project_id=project_id, source_id=source_id
    )
    paper = _require_library_paper(session, paper_id, user_id=user_id)
    source.paper_id = paper.id
    session.flush()
    return source


def unlink_source_paper(
    session: Session, *, user_id: str, project_id: str, source_id: str
) -> ProjectSource:
    """Drop the link, returning the source to abstract-only.

    Idempotent: unlinking an unlinked source is not an error, because the caller
    is asking for a state, not for a transition.
    """
    source = require_source(
        session, user_id=user_id, project_id=project_id, source_id=source_id
    )
    source.paper_id = None
    session.flush()
    return source


def autolink_project_sources(
    session: Session, *, user_id: str, project_id: str
) -> list[ProjectSource]:
    """Link every still-unlinked source whose paper the library now holds.

    ``add_source`` matches at the moment a source is saved, which only helps if
    the PDF was already in the library. The usual order is the other way round —
    discover, save, then obtain the paper — so this is what makes the common
    case link at all.

    Deliberately an explicit call rather than a side effect of reading a
    project: a GET that quietly writes is one that cannot be cached, retried, or
    reasoned about, and it would also make every project listing pay for a
    library scan.

    Known limit: an explicit unlink is not remembered, because the schema has
    only "linked" and "not linked" to say it with. A user who unlinks a match
    they judged wrong and then runs this will get it back. Recording the
    difference needs a column (a nullable ``paper_link_source``, "auto" vs
    "manual", would do it) and so is reported rather than invented here.
    """
    require_project(session, project_id, user_id=user_id)
    rows = list(
        session.scalars(
            select(ProjectSource)
            .join(LiteratureResult, ProjectSource.result_id == LiteratureResult.id)
            .where(
                ProjectSource.project_id == project_id,
                ProjectSource.user_id == user_id,
                ProjectSource.paper_id.is_(None),
                # The result is re-scoped even though the source already is, for
                # the same reason ``require_source`` does it: the two rows carry
                # the owner independently and a mismatch means something is
                # wrong, not that we should read across it.
                LiteratureResult.user_id == user_id,
            )
            .options(selectinload(ProjectSource.result))
            .order_by(ProjectSource.added_at, ProjectSource.id)
        )
    )
    linked = [row for row in rows if _autolink(session, row, row.result, user_id=user_id)]
    if linked:
        session.flush()
    return linked


def remove_source(
    session: Session, *, user_id: str, project_id: str, source_id: str
) -> None:
    row = require_source(
        session, user_id=user_id, project_id=project_id, source_id=source_id
    )
    session.delete(row)
    session.flush()


def list_artifacts(
    session: Session, *, user_id: str, project_id: str
) -> list[ProjectArtifact]:
    require_project(session, project_id, user_id=user_id)
    return list(
        session.scalars(
            select(ProjectArtifact)
            .where(
                ProjectArtifact.project_id == project_id,
                ProjectArtifact.user_id == user_id,
            )
            .order_by(ProjectArtifact.created_at, ProjectArtifact.id)
        )
    )


def require_artifact(
    session: Session, *, user_id: str, project_id: str, artifact_id: str
) -> ProjectArtifact:
    require_project(session, project_id, user_id=user_id)
    row = session.scalar(
        select(ProjectArtifact).where(
            ProjectArtifact.id == artifact_id,
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.user_id == user_id,
        )
    )
    if row is None:
        raise NotFound("Project artifact not found")
    return row


def _artifact_field(value: object, *, field: str) -> str:
    cleaned = _text(value, field=field, limit=32, required=True)
    accepted = {
        "stage": frozenset(PROJECT_STAGES),
        "type": ARTIFACT_TYPES,
        "status": ARTIFACT_STATUSES,
    }[field]
    if cleaned not in accepted:
        raise Invalid(f"{field} must be one of {sorted(accepted)}")
    return cleaned


def create_artifact(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    stage: object,
    type: object,
    title: object,
    body: object = "",
    status: object = "draft",
) -> ProjectArtifact:
    project = require_project(session, project_id, user_id=user_id)
    row = ProjectArtifact(
        user_id=user_id,
        project_id=project.id,
        stage=_artifact_field(stage, field="stage"),
        type=_artifact_field(type, field="type"),
        title=_text(title, field="title", limit=MAX_ARTIFACT_TITLE, required=True),
        body=_text(body, field="body", limit=MAX_ARTIFACT_BODY) or "",
        status=_artifact_field(status, field="status"),
    )
    session.add(row)
    session.flush()
    return row


def update_artifact(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    artifact_id: str,
    changes: dict[str, object],
) -> ProjectArtifact:
    row = require_artifact(
        session, user_id=user_id, project_id=project_id, artifact_id=artifact_id
    )
    allowed = {"stage", "type", "title", "body", "status"}
    unknown = set(changes) - allowed
    if unknown:
        raise Invalid(f"unexpected artifact fields: {sorted(unknown)}")
    if not changes:
        raise Invalid("No artifact fields provided")
    if "stage" in changes:
        row.stage = _artifact_field(changes["stage"], field="stage")
    if "type" in changes:
        row.type = _artifact_field(changes["type"], field="type")
    if "title" in changes:
        row.title = _text(
            changes["title"], field="title", limit=MAX_ARTIFACT_TITLE, required=True
        )
    if "body" in changes:
        row.body = _text(changes["body"], field="body", limit=MAX_ARTIFACT_BODY) or ""
    if "status" in changes:
        row.status = _artifact_field(changes["status"], field="status")
    row.updated_at = _now()
    session.flush()
    return row


def delete_artifact(
    session: Session, *, user_id: str, project_id: str, artifact_id: str
) -> None:
    row = require_artifact(
        session, user_id=user_id, project_id=project_id, artifact_id=artifact_id
    )
    session.delete(row)
    session.flush()
