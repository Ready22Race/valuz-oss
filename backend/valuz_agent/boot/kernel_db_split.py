"""One-time migration: move the kernel store out of the shared ``valuz.db``.

Pre-split installs kept the kernel's ``sessions`` / ``messages`` / ``events``
(and any langgraph checkpoint tables) INSIDE the host ``valuz.db``. Now that
the kernel gets its own ``kernel.db`` (see ``config.kernel_db_url``), this boot
step moves that data across ONCE so existing history survives the cutover:

1. back up ``valuz.db`` (kept indefinitely — the user's safety net),
2. copy each kernel-owned table (schema + rows) into ``kernel.db``,
3. verify row counts match per table,
4. only then drop the originals from ``valuz.db``.

Idempotent and re-entrant: rows copy with ``INSERT OR IGNORE`` on the primary
key, so an interrupted run re-copies the gap on the next boot; once
``valuz.db`` no longer holds the kernel tables the whole step is a no-op. Runs
synchronously off the event loop (like ``ensure_kernel_schema_migratable``) — it owns
no ORM session and touches the SQLite files directly, before any engine opens
them, under the single-writer lock acquired earlier in boot.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

from valuz_agent.infra.config import settings

logger = logging.getLogger(__name__)

# Kernel-owned tables that may live in the pre-split ``valuz.db``. The trio is
# always present once the kernel ran; the langgraph checkpoint tables only
# exist if a DeepAgents/Valuz-Agent workflow ever wrote a checkpoint, so each
# is guarded by existence. ``alembic_version`` (the KERNEL stamp — the host
# chain uses ``alembic_version_host``) is moved separately so kernel.db inherits
# the same revision the data was created at.
_KERNEL_TABLES = (
    "sessions",
    "messages",
    "events",
)
_CHECKPOINT_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "writes",
    "checkpoint_migrations",
)
_KERNEL_VERSION_TABLE = "alembic_version"

_BACKUP_SUFFIX = ".pre-kernel-split.bak"


def _sqlite_path(url: str) -> Path | None:
    """Filesystem path for a ``sqlite:///...`` URL, or None for non-SQLite."""
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            return Path(url[len(prefix) :])
    return None


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def migrate_kernel_store_out_of_host_db() -> None:
    """Move pre-split kernel tables from ``valuz.db`` into ``kernel.db``.

    No-op unless the kernel is a SEPARATE SQLite file from the host and the
    host file still carries kernel tables. Raises on a verification mismatch
    (data is left untouched in ``valuz.db`` and the backup is kept) so boot
    fails loudly rather than dropping unverified data.
    """
    # Only meaningful when host + kernel are distinct LOCAL SQLite files.
    host_path = _sqlite_path(settings.db_url)
    kernel_path = _sqlite_path(settings.kernel_db_url)
    if host_path is None or kernel_path is None:
        return  # a Postgres host and/or kernel — separation handled elsewhere
    if host_path.resolve() == kernel_path.resolve():
        return  # not separated (shared single file)
    if not host_path.exists():
        return  # fresh install — nothing to migrate

    host_conn = sqlite3.connect(str(host_path))
    try:
        present = _table_names(host_conn)
        movable = [t for t in (*_KERNEL_TABLES, *_CHECKPOINT_TABLES) if t in present]
        if not movable:
            return  # host file holds no kernel tables — already split or fresh
        counts_src = {t: _row_count(host_conn, t) for t in movable}
    finally:
        host_conn.close()

    logger.warning(
        "kernel DB split: moving %s from %s into %s",
        ", ".join(f"{t}({counts_src[t]})" for t in movable),
        host_path,
        kernel_path,
    )

    _backup_host_db(host_path)
    _copy_into_kernel_db(host_path, kernel_path, movable)
    _verify_counts(kernel_path, counts_src)
    _drop_from_host_db(host_path, movable)

    logger.warning(
        "kernel DB split: migrated %d table(s); originals dropped from %s "
        "(backup at %s%s)",
        len(movable),
        host_path.name,
        host_path.name,
        _BACKUP_SUFFIX,
    )


def _backup_host_db(host_path: Path) -> None:
    """Copy ``valuz.db`` to a one-time backup, WAL folded in for consistency.

    Keeps the FIRST backup if one already exists (an earlier interrupted run),
    so the pre-migration snapshot is never overwritten by a partial state.
    """
    backup = host_path.with_name(host_path.name + _BACKUP_SUFFIX)
    if backup.exists():
        return
    conn = sqlite3.connect(str(host_path))
    try:
        # Fold the WAL into the main file so the single-file copy is complete.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    shutil.copy2(host_path, backup)
    logger.info("kernel DB split: backed up %s -> %s", host_path.name, backup.name)


def _copy_into_kernel_db(host_path: Path, kernel_path: Path, movable: list[str]) -> None:
    """Create each table's schema in kernel.db (from the source's own DDL when
    absent) and copy rows by column name with ``INSERT OR IGNORE``."""
    kernel_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(kernel_path))
    try:
        conn.execute("ATTACH DATABASE ? AS src", (str(host_path),))
        existing = _table_names(conn)

        for table in (*movable, _KERNEL_VERSION_TABLE):
            src_has = conn.execute(
                "SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if src_has is None:
                continue  # e.g. alembic_version absent in a stamp-less source

            if table not in existing:
                # Recreate the table verbatim from the source DDL, then its
                # indexes — preserving PK/NOT NULL/types the kernel relies on.
                conn.execute(src_has[0])
                for (idx_sql,) in conn.execute(
                    "SELECT sql FROM src.sqlite_master WHERE type='index' "
                    "AND tbl_name=? AND sql IS NOT NULL",
                    (table,),
                ).fetchall():
                    conn.execute(idx_sql)

            cols = [c for c in _columns(conn, table) if c in set(_columns_src(conn, table))]
            collist = ", ".join(f'"{c}"' for c in cols)
            conn.execute(
                f'INSERT OR IGNORE INTO main."{table}" ({collist}) '  # noqa: S608
                f'SELECT {collist} FROM src."{table}"'
            )
        conn.commit()
    finally:
        conn.execute("DETACH DATABASE src")
        conn.close()


def _columns_src(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA src.table_info("{table}")')]


def _verify_counts(kernel_path: Path, counts_src: dict[str, int]) -> None:
    """Abort (raise) if any moved table's row count in kernel.db doesn't match
    the source — the drop step is skipped, so valuz.db + the backup are intact."""
    conn = sqlite3.connect(str(kernel_path))
    try:
        for table, expected in counts_src.items():
            got = _row_count(conn, table)
            if got != expected:
                raise RuntimeError(
                    f"kernel DB split verification failed for {table!r}: "
                    f"kernel.db has {got} rows, valuz.db had {expected}; "
                    f"leaving valuz.db untouched (backup retained)"
                )
    finally:
        conn.close()


def _drop_from_host_db(host_path: Path, movable: list[str]) -> None:
    """Drop the moved kernel tables + the kernel ``alembic_version`` stamp from
    valuz.db. The host chain's ``alembic_version_host`` is left in place."""
    conn = sqlite3.connect(str(host_path))
    try:
        for table in (*movable, _KERNEL_VERSION_TABLE):
            conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
