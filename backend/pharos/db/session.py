"""Database engine and session management (SQLite, WAL mode)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from pharos.db.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None

#: Name of the full-text index. Exported because :mod:`pharos.services.search`
#: has to name it in raw SQL — ``snippet()`` and ``bm25()`` take the table as an
#: argument, so there is no way to reach them through the ORM.
FTS_TABLE = "papers_fts"

#: Columns mirrored into the index, in the order the virtual table declares them.
#: ``snippet()`` addresses columns by *index*, so this order is part of the
#: contract with the search service and must not be permuted casually.
FTS_COLUMNS = ("title", "authors", "abstract", "full_text")

#: Whether this SQLite build can do FTS5 at all. ``None`` until probed.
_fts5_available: bool | None = None


def fts5_available() -> bool:
    """Whether full-text search is backed by FTS5 on this build.

    FTS5 is compiled in for the overwhelming majority of Python builds, but it is
    an *optional* SQLite module and some distribution and minimal-container
    builds omit it. Treating it as guaranteed would mean the whole application
    fails to boot on those, so it is probed once at startup and the search
    service degrades to ``LIKE`` when the answer is no.

    ``False`` before ``init_engine`` has run, which is the safe direction: the
    fallback works everywhere, so a caller that somehow arrives early degrades
    rather than issuing SQL against a table that does not exist.
    """
    return bool(_fts5_available)


def _py_lower(value: str | None) -> str | None:
    """``str.lower`` as a SQLite function — see :func:`_configure_sqlite`."""
    return value.lower() if isinstance(value, str) else None


def _configure_sqlite(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()
    # SQLite's built-in lower() folds ASCII only: lower('SCHRÖDINGER') is
    # 'schrÖdinger', with the Ö untouched. Python's str.lower() folds the whole
    # of Unicode. Anywhere SQL and Python are meant to be asking the *same*
    # question about the same text — notably the daily feed's keyword prefilter,
    # which narrows in SQL and then matches in Python — that difference makes the
    # SQL side quietly the narrower of the two, and rows vanish between the two
    # halves of one query. Registering Python's own casefold as a SQLite function
    # makes the two agree by construction rather than by a rule someone has to
    # remember. Registered on the connect event so every pooled connection has
    # it; ``deterministic`` lets SQLite treat it as a pure function.
    dbapi_conn.create_function("py_lower", 1, _py_lower, deterministic=True)


def init_engine(db_path: Path) -> Engine:
    """Create (once) the SQLite engine, enable WAL, and create tables."""
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _configure_sqlite)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    _init_fts(engine)
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine


def _add_missing_columns(engine: Engine) -> None:
    """Bring an existing database up to the current model.

    ``create_all`` only creates missing *tables*, so a database written by an
    earlier version keeps its old column set and every query naming a new
    column fails. There is no Alembic here, and the schema only ever grows by
    nullable columns, so a PRAGMA-driven ``ADD COLUMN`` pass is enough — and
    it is idempotent, which matters because ``init_engine`` runs on boot.

    Anything beyond adding a nullable column (dropping, renaming, retyping,
    backfilling) is out of scope and needs a real migration tool.
    """
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table.name})").fetchall()
            if not rows:  # table absent — create_all already handled it
                continue
            existing = {row[1] for row in rows}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable and column.server_default is None:
                    # Can't invent a value for existing rows; surface it loudly
                    # instead of leaving the schema silently half-migrated.
                    raise RuntimeError(
                        f"Cannot auto-add NOT NULL column {table.name}.{column.name} "
                        "to an existing database; write a real migration."
                    )
                ddl = column.type.compile(engine.dialect)
                conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl}")


def _probe_fts5(engine: Engine) -> bool:
    """Ask this SQLite build whether it has FTS5, by trying to use it.

    Compile options can be queried with ``PRAGMA compile_options``, but that
    reports how the library was *built*, which is not quite the same question as
    whether the module will load here and now. Creating a throwaway table is the
    direct answer. It goes in ``temp``, which is per-connection and vanishes when
    the connection closes, so the probe leaves nothing behind even if the drop
    never runs.

    The probe gets its own connection: a failed ``CREATE`` leaves the transaction
    needing a rollback, and doing that on the connection that then has to create
    the real schema would tangle two concerns that have no reason to share a
    transaction.
    """
    with engine.connect() as conn:
        try:
            conn.exec_driver_sql("CREATE VIRTUAL TABLE temp.pharos_fts5_probe USING fts5(x)")
            conn.exec_driver_sql("DROP TABLE temp.pharos_fts5_probe")
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False


def _fts_ddl() -> tuple[str, ...]:
    """The virtual table and the three triggers that keep it honest.

    An *external content* table (``content='papers'``) stores only the inverted
    index and reads the column values back out of ``papers`` on demand, so the
    document text is not duplicated — which matters when one of the columns is a
    400k-character ``full_text``. The price is that SQLite will not maintain it
    for us: an external-content index that nobody updates silently rots into
    results pointing at rows that no longer say what the index thinks.

    Hence the triggers. They live in the database rather than in the ORM on
    purpose. SQLAlchemy events would only fire for writes that go through the
    ORM, and this codebase already writes through ``exec_driver_sql`` in the
    migration path and through bulk statements elsewhere; a trigger fires for
    every writer, including a human at the ``sqlite3`` prompt.

    Two details are load-bearing:

    * ``DELETE`` and ``UPDATE`` push the *old* column values into the special
      ``'delete'`` command. FTS5 uses them to locate the index entries to remove,
      and it cannot read them from ``papers`` because by then the row is gone or
      changed. Passing anything other than the true previous values corrupts the
      index quietly — searches start missing papers that plainly contain the word.
    * The ``UPDATE`` trigger is guarded by a ``WHEN`` comparing the four indexed
      columns with ``IS NOT`` (null-safe, unlike ``<>``). Without it, every
      unrelated write — a soft delete stamping ``deleted_at``, a translation job
      touching the row — would reindex the whole document text for nothing.
    """
    columns = ", ".join(FTS_COLUMNS)
    new_values = ", ".join(f"new.{name}" for name in FTS_COLUMNS)
    old_values = ", ".join(f"old.{name}" for name in FTS_COLUMNS)
    changed = " OR ".join(f"old.{name} IS NOT new.{name}" for name in FTS_COLUMNS)
    return (
        f"""
        CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5(
            {columns},
            content='papers',
            content_rowid='rowid',
            tokenize="unicode61 remove_diacritics 2"
        )
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ai AFTER INSERT ON papers BEGIN
            INSERT INTO {FTS_TABLE}(rowid, {columns}) VALUES (new.rowid, {new_values});
        END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ad AFTER DELETE ON papers BEGIN
            INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, {columns})
            VALUES ('delete', old.rowid, {old_values});
        END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_au AFTER UPDATE ON papers
        WHEN {changed} BEGIN
            INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, {columns})
            VALUES ('delete', old.rowid, {old_values});
            INSERT INTO {FTS_TABLE}(rowid, {columns}) VALUES (new.rowid, {new_values});
        END
        """,
    )


def _init_fts(engine: Engine) -> None:
    """Create the full-text index once, and keep it across restarts.

    Runs *after* ``_add_missing_columns`` and depends on that order: the index
    names ``papers.full_text``, so on a database written before that column
    existed the triggers would fail to compile if this went first.

    Degrading rather than raising is the whole contract of this function. A build
    without FTS5 leaves ``_fts5_available`` false and the application boots
    normally with ``LIKE``-based search.

    Setup is deliberately expressed as "make the state right" rather than "do the
    steps once", because the steps are *not* atomic here and it would be easy to
    assume they are. SQLite's DDL is transactional, but the pysqlite driver does
    not open a transaction for a ``CREATE``: it autocommits it, so a statement
    inside ``engine.begin()`` survives a rollback. A process killed between
    creating the table and populating it therefore leaves the table behind, empty.

    That torn state is invisible to the obvious check. ``SELECT COUNT(*)`` on an
    external-content table is answered from the *content* table, so an index
    holding zero entries still cheerfully reports one row per paper. Left
    undetected it is about the worst failure this module has: the table exists, so
    a "have we set up yet" guard says yes; the triggers only fire for *future*
    writes; and search returns nothing for the entire existing library, forever,
    with no error anywhere to explain why.

    Hence :func:`_fts_needs_rebuild`, which asks whether the index is *populated*
    rather than whether it exists.
    """
    global _fts5_available

    if not _probe_fts5(engine):
        _fts5_available = False
        return

    with engine.begin() as conn:
        if _fts_table_exists(conn):
            # Already set up by an earlier boot. The triggers are re-issued
            # because IF NOT EXISTS makes it free, and it repairs a database whose
            # table survived but whose triggers somebody dropped by hand.
            for statement in _fts_ddl()[1:]:
                conn.exec_driver_sql(statement)
        else:
            for statement in _fts_ddl():
                conn.exec_driver_sql(statement)

        if _fts_needs_rebuild(conn):
            # ``rebuild`` derives the whole index from ``papers``. It covers the
            # upgrade — an index created beside a library already full of papers
            # that the triggers will never see written — and the torn state above.
            conn.exec_driver_sql(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')")

    _fts5_available = True


def _fts_table_exists(conn: Connection) -> bool:
    row = conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (FTS_TABLE,)
    ).fetchone()
    return row is not None


def _fts_needs_rebuild(conn: Connection) -> bool:
    """True when there are papers to index but the index holds no documents.

    Counted from ``<table>_docsize``, one of FTS5's shadow tables, which holds a
    row per *indexed document* — the honest answer to "is this index populated",
    and the one thing ``COUNT(*)`` on the virtual table cannot tell us (see
    :func:`_init_fts`). It is part of FTS5's on-disk format rather than its public
    API, so a build that ever stops providing it degrades to never rebuilding,
    which is the safe direction: a boot that skips a rebuild it did not need costs
    nothing, while one that rebuilds on every start would reindex the whole
    library each time the application is restarted.
    """
    papers = conn.exec_driver_sql("SELECT COUNT(*) FROM papers").scalar()
    if not papers:
        return False
    try:
        indexed = conn.exec_driver_sql(f"SELECT COUNT(*) FROM {FTS_TABLE}_docsize").scalar()
    except Exception:
        return False
    return not indexed


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session scope: commit on success, rollback on error."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized; call init_engine() first.")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
