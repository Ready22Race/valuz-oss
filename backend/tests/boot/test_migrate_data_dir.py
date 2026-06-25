"""Tests for the one-time ``~/.valuz/app`` -> ``~/.valuz-oss`` data-dir cutover.

Builds a realistic OLD tree (host + kernel SQLite files with both managed and
external project rows, plus skill symlinks under managed and external project
cwds) under a temp ``HOME``, drives ``migrate_legacy_data_dir`` directly, and
asserts: the new root is populated, DB prefixes are rewritten, external paths
are left UNCHANGED, symlinks are repointed and resolve, the old tree survives,
and a second run is a no-op.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

import valuz_agent.boot.migrate_data_dir as migrate
from valuz_agent.infra.config import settings


def _make_host_db(path: Path, *, chat_cwd: str, external_cwd: str, official_skill: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE valuz_project ("
            "id TEXT PRIMARY KEY, kind TEXT, root_path TEXT)"
        )
        # Managed chat project — root_path lives UNDER the old data dir.
        conn.execute(
            "INSERT INTO valuz_project VALUES ('p_chat', 'chat', ?)", (chat_cwd,)
        )
        # External/user project — root_path lives OUTSIDE the data dir (e.g.
        # ~/Downloads-like). Must stay untouched.
        conn.execute(
            "INSERT INTO valuz_project VALUES ('p_ext', 'project', ?)", (external_cwd,)
        )
        # A skill index row pointing into the old root + one external path.
        conn.execute(
            "CREATE TABLE valuz_skill_index ("
            "id TEXT PRIMARY KEY, source_path TEXT, project_root TEXT)"
        )
        conn.execute(
            "INSERT INTO valuz_skill_index VALUES ('sk1', ?, NULL)", (official_skill,)
        )
        conn.execute(
            "INSERT INTO valuz_skill_index VALUES ('sk2', ?, NULL)",
            (external_cwd + "/.claude/skills/mine",),
        )
        # Tables the old hand-maintained allowlist MISSED — covered now that the
        # rewrite is schema-driven: an artifact file_path + an agent skills JSON.
        conn.execute("CREATE TABLE valuz_session_artifact (id TEXT PRIMARY KEY, file_path TEXT)")
        conn.execute(
            "INSERT INTO valuz_session_artifact VALUES ('a1', ?)", (chat_cwd + "/demo.html",)
        )
        conn.execute("CREATE TABLE valuz_agent (id TEXT PRIMARY KEY, skills JSON)")
        conn.execute(
            "INSERT INTO valuz_agent VALUES ('ag1', ?)", (json.dumps([official_skill]),)
        )
        conn.commit()
    finally:
        conn.close()


def _make_kernel_db(path: Path, *, cwd: str, skill_target: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE sessions ("
            "id TEXT PRIMARY KEY, cwd TEXT, skills TEXT, agent_config TEXT)"
        )
        skills = json.dumps([skill_target])
        agent_config = json.dumps({"cwd": cwd, "note": "lives under old root"})
        conn.execute(
            "INSERT INTO sessions VALUES ('s1', ?, ?, ?)", (cwd, skills, agent_config)
        )
        # Kernel event log embeds paths in a JSON column — also missed by the
        # old allowlist, swept now.
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, data JSON)")
        conn.execute(
            "INSERT INTO events VALUES (1, ?)",
            (json.dumps({"cwd": cwd, "skill": skill_target}),),
        )
        conn.commit()
    finally:
        conn.close()


def _scalar(path: Path, sql: str) -> str | None:
    with sqlite3.connect(path) as conn:
        row = conn.execute(sql).fetchone()
    return None if row is None else row[0]


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point ``Path.home()`` + ``settings.data_dir`` at a temp HOME.

    Returns ``(old_app, old_kb, new_root)``.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    new_root = home / ".valuz-oss"
    monkeypatch.setattr(settings, "data_dir", new_root)
    return home / ".valuz" / "app", home / ".valuz" / "kb", new_root


def _build_old_tree(home: Path, old_app: Path, old_kb: Path):
    """Lay out a populated OLD ``~/.valuz/app`` tree. Returns key paths."""
    old_app_prefix = str(old_app)

    # Official skills live under the old root and are the symlink targets.
    official = old_app / "official-skills" / "skill-creator"
    official.mkdir(parents=True)
    (official / "SKILL.md").write_text("# skill-creator\n")

    # Managed chat project cwd, copied under the new root.
    chat_cwd = old_app / "projects" / "p_chat"
    chat_skills = chat_cwd / ".claude" / "skills"
    chat_skills.mkdir(parents=True)
    os.symlink(str(official), str(chat_skills / "skill-creator"), target_is_directory=True)

    # External/user project cwd — OUTSIDE the data dir, repaired in place.
    external_cwd = home / "Downloads" / "my-project"
    ext_skills = external_cwd / ".agents" / "skills"
    ext_skills.mkdir(parents=True)
    os.symlink(str(official), str(ext_skills / "skill-creator"), target_is_directory=True)

    # Old stray KB sibling tree.
    old_kb.mkdir(parents=True)
    (old_kb / "doc.md").write_text("kb doc\n")

    _make_host_db(
        old_app / "valuz.db",
        chat_cwd=str(chat_cwd),
        external_cwd=str(external_cwd),
        official_skill=str(official),
    )
    _make_kernel_db(
        old_app / "kernel.db",
        cwd=str(chat_cwd),
        skill_target=str(official),
    )
    # A lock file that must NOT be copied.
    (old_app / ".single-writer.lock").write_text("pid\n")

    return {
        "old_app_prefix": old_app_prefix,
        "chat_cwd": chat_cwd,
        "external_cwd": external_cwd,
        "official": official,
    }


def test_migrates_copies_rewrites_and_repoints(fake_home):
    old_app, old_kb, new_root = fake_home
    paths = _build_old_tree(old_app.parent.parent, old_app, old_kb)
    new_prefix = str(new_root)

    migrate.migrate_legacy_data_dir()

    # New root populated; lock file NOT carried over.
    assert (new_root / "valuz.db").exists()
    assert (new_root / "kernel.db").exists()
    assert (new_root / "kb" / "doc.md").exists()
    assert not (new_root / ".single-writer.lock").exists()
    assert (new_root / migrate._MARKER_FILENAME).exists()

    host_db = new_root / "valuz.db"
    kernel_db = new_root / "kernel.db"

    # Managed chat root_path rewritten to the new prefix.
    chat_root = _scalar(host_db, "SELECT root_path FROM valuz_project WHERE id='p_chat'")
    assert chat_root is not None and chat_root.startswith(new_prefix)

    # External root_path is UNCHANGED.
    ext_root = _scalar(host_db, "SELECT root_path FROM valuz_project WHERE id='p_ext'")
    assert ext_root == str(paths["external_cwd"])

    # Skill index: old-root source rewritten; external project_root path untouched.
    sk1 = _scalar(host_db, "SELECT source_path FROM valuz_skill_index WHERE id='sk1'")
    assert sk1 is not None and sk1.startswith(new_prefix)
    sk2 = _scalar(host_db, "SELECT source_path FROM valuz_skill_index WHERE id='sk2'")
    assert sk2 == str(paths["external_cwd"]) + "/.claude/skills/mine"

    # Kernel sessions: cwd + skills(JSON) + agent_config(JSON) rewritten.
    cwd = _scalar(kernel_db, "SELECT cwd FROM sessions WHERE id='s1'")
    assert cwd is not None and cwd.startswith(new_prefix)
    skills = _scalar(kernel_db, "SELECT skills FROM sessions WHERE id='s1'")
    assert new_prefix in skills and paths["old_app_prefix"] not in skills
    agent_config = _scalar(kernel_db, "SELECT agent_config FROM sessions WHERE id='s1'")
    assert new_prefix in agent_config and paths["old_app_prefix"] not in agent_config

    # Schema-driven sweep also rewrites tables the old allowlist missed.
    art = _scalar(host_db, "SELECT file_path FROM valuz_session_artifact WHERE id='a1'")
    assert art is not None and art.startswith(new_prefix)
    ag = _scalar(host_db, "SELECT skills FROM valuz_agent WHERE id='ag1'")
    assert ag is not None and new_prefix in ag and paths["old_app_prefix"] not in ag
    ev = _scalar(kernel_db, "SELECT data FROM events WHERE id=1")
    assert ev is not None and new_prefix in ev and paths["old_app_prefix"] not in ev

    # Symlinks repointed and resolving — managed (copied) + external (in place).
    chat_link = new_root / "projects" / "p_chat" / ".claude" / "skills" / "skill-creator"
    assert os.path.islink(chat_link)
    assert os.readlink(chat_link).startswith(new_prefix)
    assert chat_link.exists()  # resolves (the official skill was copied)

    ext_link = paths["external_cwd"] / ".agents" / "skills" / "skill-creator"
    assert os.path.islink(ext_link)
    assert os.readlink(ext_link).startswith(new_prefix)
    assert ext_link.exists()

    # Old tree retained as a fallback.
    assert old_app.exists()
    assert (old_app / "valuz.db").exists()


def test_second_run_is_noop(fake_home):
    old_app, old_kb, new_root = fake_home
    _build_old_tree(old_app.parent.parent, old_app, old_kb)

    migrate.migrate_legacy_data_dir()
    marker = new_root / migrate._MARKER_FILENAME
    first_mtime = marker.stat().st_mtime_ns

    # Mutate the new DB so a re-run that DID rewrite would be detectable.
    with sqlite3.connect(new_root / "valuz.db") as conn:
        conn.execute("UPDATE valuz_project SET root_path='SENTINEL' WHERE id='p_chat'")
        conn.commit()

    migrate.migrate_legacy_data_dir()  # guard: completion marker present

    assert marker.stat().st_mtime_ns == first_mtime
    chat_root = _scalar(
        new_root / "valuz.db", "SELECT root_path FROM valuz_project WHERE id='p_chat'"
    )
    assert chat_root == "SENTINEL"


def test_fresh_install_old_root_absent(fake_home):
    old_app, _old_kb, new_root = fake_home
    assert not old_app.exists()

    migrate.migrate_legacy_data_dir()

    # Nothing created (the writer lock would normally mkdir new_root, but this
    # step does not — and must not synthesize a DB).
    assert not (new_root / "valuz.db").exists()


def test_empty_new_root_from_writer_lock_still_migrates(fake_home):
    """The single-writer lock pre-creates an EMPTY new root + lock file. The
    guard keys on the completion marker, not directory existence, so migration
    still runs (and the lock file is preserved, not clobbered)."""
    old_app, old_kb, new_root = fake_home
    _build_old_tree(old_app.parent.parent, old_app, old_kb)

    new_root.mkdir(parents=True)
    (new_root / ".single-writer.lock").write_text("pid\n")

    migrate.migrate_legacy_data_dir()

    assert (new_root / "valuz.db").exists()
    assert (new_root / migrate._MARKER_FILENAME).exists()
    # The writer lock the current boot holds must survive the copy.
    assert (new_root / ".single-writer.lock").exists()


def test_sibling_paths_sharing_string_prefix_are_not_corrupted(fake_home):
    """A path under ``~/.valuz/apple`` shares the literal ``~/.valuz/app`` string
    prefix but is a different directory — the anchored match must leave it alone."""
    old_app, old_kb, new_root = fake_home
    home = old_app.parent.parent
    _build_old_tree(home, old_app, old_kb)

    sibling = home / ".valuz" / "apple" / "proj"  # NOT under ~/.valuz/app
    sibling_skill = str(sibling / ".claude" / "skills" / "x")
    with sqlite3.connect(old_app / "valuz.db") as conn:
        conn.execute(
            "INSERT INTO valuz_project VALUES ('p_sib', 'project', ?)", (str(sibling),)
        )
        conn.execute(
            "INSERT INTO valuz_skill_index VALUES ('sk_sib', ?, NULL)", (sibling_skill,)
        )
        conn.commit()

    migrate.migrate_legacy_data_dir()

    host_db = new_root / "valuz.db"
    assert _scalar(host_db, "SELECT root_path FROM valuz_project WHERE id='p_sib'") == str(
        sibling
    )
    assert (
        _scalar(host_db, "SELECT source_path FROM valuz_skill_index WHERE id='sk_sib'")
        == sibling_skill
    )


def test_partial_run_resumes_when_marker_absent(fake_home):
    """A run interrupted after the copy (db present, marker absent) must be
    completed by the next call, not skipped — the marker is the 'done' gate."""
    old_app, old_kb, new_root = fake_home
    _build_old_tree(old_app.parent.parent, old_app, old_kb)
    new_prefix = str(new_root)

    # Simulate a crash after COPY but before rewrite/repoint/verify: a populated
    # new root carrying the OLD prefixes, with NO marker.
    migrate._copy_tree(old_app, new_root)
    assert (new_root / "valuz.db").exists()
    assert not (new_root / migrate._MARKER_FILENAME).exists()
    pre = _scalar(new_root / "valuz.db", "SELECT root_path FROM valuz_project WHERE id='p_chat'")
    assert pre is not None and pre.startswith(str(old_app))  # not yet rewritten

    migrate.migrate_legacy_data_dir()  # resumes — must complete the rewrite

    assert (new_root / migrate._MARKER_FILENAME).exists()
    chat_root = _scalar(
        new_root / "valuz.db", "SELECT root_path FROM valuz_project WHERE id='p_chat'"
    )
    assert chat_root is not None and chat_root.startswith(new_prefix)


def test_self_heal_sweeps_under_old_marker_version(fake_home):
    """An install migrated by an older, less-complete version (versionless
    marker) with a straggler old-prefix row self-heals in place on next boot."""
    old_app, old_kb, new_root = fake_home
    _build_old_tree(old_app.parent.parent, old_app, old_kb)
    new_prefix = str(new_root)

    migrate.migrate_legacy_data_dir()  # full cutover, writes a current-version marker

    # Downgrade the marker to a versionless (v1) one and inject a straggler that
    # an older allowlist-based rewrite would have missed.
    (new_root / migrate._MARKER_FILENAME).write_text("migrated from x\n")
    leak = str(old_app / "projects" / "p_chat" / "stale.html")
    with sqlite3.connect(new_root / "valuz.db") as conn:
        conn.execute("INSERT INTO valuz_session_artifact VALUES ('leak', ?)", (leak,))
        conn.commit()

    migrate.migrate_legacy_data_dir()  # self-heal sweep (no re-copy)

    healed = _scalar(
        new_root / "valuz.db", "SELECT file_path FROM valuz_session_artifact WHERE id='leak'"
    )
    assert healed is not None and healed.startswith(new_prefix)
    assert migrate._marker_version(new_root / migrate._MARKER_FILENAME) >= 2


def test_external_db_configured_is_noop(fake_home, monkeypatch):
    """When the store lives in an external DB (e.g. Postgres), the local-SQLite
    copy/rewrite has nothing to do and must not run (or fail) at boot."""
    old_app, old_kb, new_root = fake_home
    _build_old_tree(old_app.parent.parent, old_app, old_kb)

    monkeypatch.setattr(settings, "database_url", "postgresql://user@host/db")

    migrate.migrate_legacy_data_dir()

    assert not (new_root / "valuz.db").exists()
    assert not (new_root / migrate._MARKER_FILENAME).exists()
