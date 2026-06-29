"""One-time migration: move the data dir from ``~/.valuz/app`` to ``~/.valuz-oss``.

The data root was flattened and renamed: the old ``~/.valuz/app`` tree (with
its sibling ``~/.valuz/kb``) becomes the new flat ``~/.valuz-oss`` root (``kb``
folds in as ``~/.valuz-oss/kb``). This step carries an existing install across
that cutover ONCE, with the same checkpoint/copy/verify discipline as
``kernel_db_split.py``:

1. BAIL — no-op when the store does not live in the local SQLite files this
   step manipulates (``database_url`` / ``kernel_database_url`` configured, e.g.
   Postgres), or when the cutover already completed at the current version.
2. CHECKPOINT — fold each old DB's WAL into its main file
   (``PRAGMA wal_checkpoint(TRUNCATE)``) so the copied ``*.db`` is self-contained.
3. COPY — clear any partial prior copy (keeping the single-writer lock) and
   ``copytree`` the old tree into the new root (symlinks preserved). The old
   tree is left untouched as a fallback — never deleted.
4. REWRITE — rewrite the leading absolute-path prefix inside the COPIED
   ``valuz.db`` / ``kernel.db`` using stdlib ``sqlite3``. The rewrite is
   SCHEMA-DRIVEN: every text/JSON column of every table is swept (so no
   path-bearing column can be missed), the match is ANCHORED to a path boundary
   (``<prefix>/`` or the bare path) so a sibling like ``~/.valuz/apple`` is never
   mangled, and external user paths (e.g. ``~/Downloads/...``) pass through
   untouched. It works verbatim inside JSON columns because the absolute path
   appears literally.
5. REPOINT — repair every project cwd's skill symlinks that point into the old
   root, both for managed chat projects (copied under the new root) and for
   user/external projects (which live OUTSIDE the data dir and are repaired in
   place).
6. VERIFY — assert the copied DBs exist and no text column still carries the old
   prefix. Only then drop the marker file (stamped with the migration version).

A marker stamped with an OLDER version triggers an in-place rewrite sweep (no
re-copy) so an install migrated by an earlier, less-complete revision self-heals
on the next boot.

Runs synchronously off the event loop, BEFORE any engine opens the files and
under the single-writer lock acquired earlier in boot. The marker file is the
authoritative "done" signal, and every step is re-entrant, so a run interrupted
mid-flight (crash / power loss) is completed by the next boot rather than left
half-migrated.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from pathlib import Path

from valuz_agent.infra.config import settings

logger = logging.getLogger(__name__)

# Bumped whenever the rewrite grows to cover more columns/tables, so an install
# migrated by an older revision self-heals in place. v1 used a hand-maintained
# column allowlist (which missed ``valuz_agent`` / ``valuz_session_artifact`` /
# kernel ``events`` + ``messages``); v2 sweeps every text column generically.
_MIGRATION_VERSION = 2

# Name of the active log directory (mirrors ``settings.log_dir``). It holds the
# RUNNING boot process's own open ``backend.log`` — structured logging is
# configured before this migration runs — and on Windows an open file can be
# neither deleted nor overwritten ([WinError 32] sharing violation; POSIX allows
# both, which is why this only ever bit Windows upgrades). Logs are disposable
# runtime output, not migratable data, so the cutover skips them in BOTH
# directions: ``_COPY_IGNORE`` keeps the copy from overwriting the open
# ``backend.log``, and ``_reset_partial_copy`` keeps rmtree from trying to
# delete it.
_LOGS_DIRNAME = "logs"

# Lock/journal noise that must NOT ride along into the copied tree. The WAL is
# checkpointed into the main DB before the copy, so the sidecars are redundant;
# ``logs`` is excluded for the open-file reason documented above.
_COPY_IGNORE = shutil.ignore_patterns(
    ".single-writer.lock", "*.db-wal", "*.db-shm", _LOGS_DIRNAME
)
_LOCK_FILENAME = ".single-writer.lock"

_MARKER_FILENAME = ".migrated-from-valuz-app"

# Declared-type hints for columns that can hold a path string. BLOB columns
# (e.g. langgraph ``checkpoints``/``writes`` payloads) are skipped — string
# REPLACE would risk corrupting binary, and they carry no host paths.
_TEXT_AFFINITY_HINTS = ("TEXT", "CHAR", "CLOB", "JSON")

# Skill-symlink dirs under each project cwd that may point into the old root.
_SKILL_LINK_DIRS = (".claude/skills", ".agents/skills")


def migrate_legacy_data_dir() -> None:
    """Move a pre-cutover ``~/.valuz/app`` install into ``~/.valuz-oss``.

    No-op when an external DB is configured or when the cutover already
    completed at the current version. A marker from an older version triggers an
    in-place rewrite sweep. Raises on a verification failure (the old tree stays
    intact) so boot fails loudly rather than running on half-migrated paths.
    """
    # An external/colocated store means the live data is not in the local SQLite
    # files this step copies + rewrites — there is nothing to migrate.
    if settings.database_url or settings.kernel_database_url:
        return

    new_root = settings.data_dir
    # Only the DEFAULT new root participates in the ``~/.valuz/app`` cutover. A
    # custom ``VALUZ_DATA_DIR`` (tests, bespoke installs) is NOT the rename
    # target — skip, so we never copy the real ``~/.valuz/app`` into an unrelated
    # data dir. (The desktop app sets ``VALUZ_DATA_DIR`` to this same default, so
    # it still migrates.)
    if new_root != Path.home() / ".valuz-oss":
        return

    old_app = Path.home() / ".valuz" / "app"
    old_kb = Path.home() / ".valuz" / "kb"
    marker = new_root / _MARKER_FILENAME
    host_db = new_root / settings.db_filename
    kernel_db = new_root / settings.kernel_db_filename
    old_app_prefix = str(old_app)
    pairs = ((old_app_prefix, str(new_root)), (str(old_kb), str(new_root / "kb")))

    if marker.exists():
        if _marker_version(marker) >= _MIGRATION_VERSION:
            return
        # Self-heal: an earlier, less-complete version cut over but left some
        # path-bearing columns un-rewritten. Sweep them in place (no re-copy).
        logger.warning(
            "data-dir migration: upgrading marker to v%d — in-place path sweep",
            _MIGRATION_VERSION,
        )
        host_n = _rewrite_all(host_db, pairs)
        kernel_n = _rewrite_all(kernel_db, pairs)
        repaired = _repoint_symlinks(new_root, host_db, old_app_prefix, str(new_root))
        _assert_dbs_clean(new_root, old_app_prefix)
        _write_marker(marker, old_app)
        logger.warning(
            "data-dir migration: sweep done — rewrote %s; repaired %d symlink(s)",
            _fmt_counts({**host_n, **kernel_n}),
            repaired,
        )
        return

    if not old_app.exists():
        # Fresh install — no legacy tree to carry over.
        return

    logger.warning(
        "data-dir migration: copying %s -> %s (old tree kept as fallback)",
        old_app,
        new_root,
    )

    # CHECKPOINT — fold each old DB's WAL into the main file first.
    for name in (settings.db_filename, settings.kernel_db_filename):
        _checkpoint_wal(old_app / name)

    # COPY — start from a clean destination so a partial prior copy can't leave
    # stale files / symlink conflicts (re-entrancy).
    _reset_partial_copy(new_root)
    _copy_tree(old_app, new_root)
    if old_kb.exists():
        _copy_tree(old_kb, new_root / "kb")

    # REWRITE (schema-driven, anchored to a path boundary).
    host_n = _rewrite_all(host_db, pairs)
    kernel_n = _rewrite_all(kernel_db, pairs)

    # REPOINT skill symlinks.
    repaired = _repoint_symlinks(new_root, host_db, old_app_prefix, str(new_root))

    # VERIFY — raise (old tree intact) before the marker is written.
    _assert_carried_over(old_app, new_root)
    _assert_dbs_clean(new_root, old_app_prefix)

    logger.warning(
        "data-dir migration: done — rewrote %s; repaired %d symlink(s); old tree %s retained",
        _fmt_counts({**host_n, **kernel_n}),
        repaired,
        old_app,
    )
    _write_marker(marker, old_app)


# --------------------------------------------------------------------------- #
# Marker
# --------------------------------------------------------------------------- #


def _write_marker(marker: Path, old_app: Path) -> None:
    try:
        marker.write_text(f"migrated from {old_app}\nversion={_MIGRATION_VERSION}\n")
    except OSError:
        logger.warning("data-dir migration: could not write marker file", exc_info=True)


def _marker_version(marker: Path) -> int:
    """Parse ``version=N`` from the marker; a versionless marker is v1."""
    try:
        for line in marker.read_text().splitlines():
            if line.startswith("version="):
                return int(line.split("=", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return 1


def _fmt_counts(counts: dict[tuple[str, str], int]) -> str:
    return ", ".join(f"{t}.{c}({n})" for (t, c), n in counts.items()) or "no rows"


# --------------------------------------------------------------------------- #
# Copy
# --------------------------------------------------------------------------- #


def _checkpoint_wal(db_path: Path) -> None:
    """Fold ``db_path``'s WAL into the main file so the copy is self-contained.

    Safe under the single-writer lock (no engine has opened the file). Best
    effort: a failure is logged, not fatal."""
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning(
            "data-dir migration: WAL checkpoint failed for %s", db_path, exc_info=True
        )


def _reset_partial_copy(new_root: Path) -> None:
    """Clear a partial prior copy under ``new_root`` (keeping the writer lock).

    The OLD tree is the source of truth and is never deleted, so discarding an
    incomplete copy is lossless and makes the copy step deterministic + re-
    entrant (avoids ``copytree`` symlink-already-exists conflicts on a re-run)."""
    if not new_root.exists():
        return
    for entry in new_root.iterdir():
        # Both are held OPEN by the current boot process — the single-writer
        # lock and the logging handler's ``logs/backend.log``. On Windows an
        # open file cannot be removed ([WinError 32]); skipping them is correct
        # anyway (neither is migratable data). See ``_LOGS_DIRNAME``.
        if entry.name in (_LOCK_FILENAME, _LOGS_DIRNAME):
            continue
        try:
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
            else:
                shutil.rmtree(entry)
        except OSError:
            logger.warning(
                "data-dir migration: could not clear partial %s", entry, exc_info=True
            )


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy ``src`` into ``dst`` preserving symlinks (not dereferenced)."""
    shutil.copytree(
        src,
        dst,
        symlinks=True,
        ignore=_COPY_IGNORE,
        ignore_dangling_symlinks=True,
        dirs_exist_ok=True,
    )


# --------------------------------------------------------------------------- #
# DB rewrite (schema-driven)
# --------------------------------------------------------------------------- #


def _rewrite_all(
    db_path: Path, pairs: tuple[tuple[str, str], ...]
) -> dict[tuple[str, str], int]:
    """Rewrite every old prefix -> new prefix across ALL text columns of ALL
    tables. Returns the per-column count of rows touched by the FIRST (app)
    prefix. Anchored to a path boundary, so siblings/external paths are safe."""
    counts: dict[tuple[str, str], int] = {}
    if not db_path.exists():
        return counts

    app_prefix = pairs[0][0]
    conn = sqlite3.connect(str(db_path))
    try:
        for table in _all_tables(conn):
            for column in _text_columns(conn, table):
                touched = _count_under_prefix(conn, table, column, app_prefix)
                for old, new in pairs:
                    _replace_anchored(conn, table, column, old, new)
                if touched:
                    counts[(table, column)] = touched
        conn.commit()
    finally:
        conn.close()
    return counts


def _all_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    ]


def _text_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Columns whose declared type has text affinity (incl. JSON/VARCHAR).

    BLOB/INTEGER/REAL/BOOLEAN columns are skipped — REPLACE could corrupt binary
    and they hold no paths."""
    cols: list[str] = []
    for row in conn.execute(f'PRAGMA table_info("{table}")'):
        name, ctype = row[1], (row[2] or "").upper()
        if any(hint in ctype for hint in _TEXT_AFFINITY_HINTS):
            cols.append(name)
    return cols


def _count_under_prefix(
    conn: sqlite3.Connection, table: str, column: str, prefix: str
) -> int:
    """Rows whose ``column`` is exactly ``prefix`` or lives under ``prefix/``."""
    boundary = prefix + os.sep
    return int(
        conn.execute(
            f'SELECT COUNT(*) FROM "{table}" '  # noqa: S608 — identifiers from schema
            f'WHERE "{column}" = ? OR "{column}" LIKE \'%\' || ? || \'%\'',
            (prefix, boundary),
        ).fetchone()[0]
    )


def _replace_anchored(
    conn: sqlite3.Connection, table: str, column: str, old: str, new: str
) -> None:
    """Replace ``old`` -> ``new`` only at a path boundary (``old/`` or bare ``old``).

    Anchoring on the separator prevents corrupting a sibling path that merely
    shares the string prefix (e.g. ``~/.valuz/apple`` under an ``~/.valuz/app``
    rename)."""
    boundary_old = old + os.sep
    boundary_new = new + os.sep
    conn.execute(
        f'UPDATE "{table}" SET "{column}" = REPLACE("{column}", ?, ?) '  # noqa: S608
        f"WHERE \"{column}\" LIKE '%' || ? || '%'",
        (boundary_old, boundary_new, boundary_old),
    )
    conn.execute(
        f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?',  # noqa: S608
        (new, old),
    )


# --------------------------------------------------------------------------- #
# Symlink repoint
# --------------------------------------------------------------------------- #


def _repoint_symlinks(
    new_root: Path, host_db: Path, old_app_prefix: str, new_prefix: str
) -> int:
    """Repair skill symlinks under every project cwd that point into the old root.

    Two cwd sources: (a) managed chat projects — every dir under
    ``new_root/projects`` (copied here, so repaired in the new tree); (b) user/
    external projects — ``valuz_project.root_path WHERE kind='project'`` (live
    OUTSIDE the data dir, never copied — repaired IN PLACE). Per-link work is
    wrapped so one bad link can't abort the whole migration.
    """
    cwds: list[Path] = []

    projects_dir = new_root / "projects"
    if projects_dir.is_dir():
        cwds.extend(p for p in projects_dir.iterdir() if p.is_dir())

    if host_db.exists():
        conn = sqlite3.connect(str(host_db))
        try:
            if "valuz_project" in _all_tables(conn):
                for (root_path,) in conn.execute(
                    "SELECT root_path FROM valuz_project "
                    "WHERE kind = 'project' AND root_path IS NOT NULL"
                ).fetchall():
                    cwds.append(Path(root_path))
        finally:
            conn.close()

    repaired = 0
    for cwd in cwds:
        for rel in _SKILL_LINK_DIRS:
            skills_dir = cwd / rel
            if not skills_dir.is_dir():
                continue
            try:
                entries = list(skills_dir.iterdir())
            except OSError:
                logger.warning(
                    "data-dir migration: cannot list %s", skills_dir, exc_info=True
                )
                continue
            for entry in entries:
                repaired += _repoint_one(entry, old_app_prefix, new_prefix)
    return repaired


def _repoint_one(entry: Path, old_app_prefix: str, new_prefix: str) -> int:
    """Repoint a single symlink if it targets the old root. Returns 1 if repaired.

    Anchored like the DB rewrite: only a target equal to the prefix or under
    ``<prefix>/`` is repointed, so a sibling target is left alone."""
    try:
        if not os.path.islink(entry):
            return 0
        target = os.readlink(entry)
        if target == old_app_prefix:
            new_target = new_prefix
        elif target.startswith(old_app_prefix + os.sep):
            new_target = new_prefix + target[len(old_app_prefix) :]
        else:
            return 0
        is_dir = not os.path.isfile(entry)  # entry resolves through the link
        entry.unlink()
        os.symlink(new_target, entry, target_is_directory=is_dir)
        return 1
    except OSError:
        logger.warning("data-dir migration: failed to repoint %s", entry, exc_info=True)
        return 0


# --------------------------------------------------------------------------- #
# Verify
# --------------------------------------------------------------------------- #


def _assert_carried_over(old_app: Path, new_root: Path) -> None:
    """Each CRITICAL file the OLD tree had MUST survive the copy.

    - A missing DB is data loss.
    - A missing ``installation.json`` would let identity re-derive a fresh owner
      id — **changing ``user_id``** and orphaning owner-scoped state (onboarding,
      skill index). Preserving it (so identity, resolved right after this step,
      reads the SAME owner) is the load-bearing guarantee that ``user_id`` is
      invariant across the migration.

    Absence in the OLD tree is fine: a PRE-SPLIT install has no ``kernel.db``
    (created later by the kernel-store split), and a never-booted tree may have
    neither a DB nor an installation file."""
    critical = (
        settings.db_filename,
        settings.kernel_db_filename,
        settings.installation_file.name,
    )
    for name in critical:
        if (old_app / name).exists() and not (new_root / name).exists():
            raise RuntimeError(
                f"data-dir migration verify failed: {new_root / name} missing after copy"
            )


def _assert_dbs_clean(new_root: Path, old_app_prefix: str) -> None:
    """No surviving DB may still carry the old prefix. Skips DBs that don't
    exist (nothing to verify). Leaves the old tree intact on failure."""
    for name in (settings.db_filename, settings.kernel_db_filename):
        db = new_root / name
        if db.exists():
            _assert_no_old_prefix(db, old_app_prefix)


def _assert_no_old_prefix(db_path: Path, old_app_prefix: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        for table in _all_tables(conn):
            for column in _text_columns(conn, table):
                stragglers = _count_under_prefix(conn, table, column, old_app_prefix)
                if stragglers:
                    raise RuntimeError(
                        f"data-dir migration verify failed: {table}.{column} still has "
                        f"{stragglers} row(s) under the old prefix {old_app_prefix!r}"
                    )
    finally:
        conn.close()
