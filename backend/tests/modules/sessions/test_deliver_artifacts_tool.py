"""Tests for the built-in ``deliver_artifacts`` MCP tool handler.

The handler runs on the host toolkit MCP path: the kernel ``ExecContext``
carries the calling ``session_id`` and the MCP wrapper passes the session
owner explicitly. It records agent-declared output files into
``valuz_session_artifact`` (the durable "生成文件" list), deriving
``fileName`` / ``fileSize`` / ``mimeType`` from disk when the model omits them,
skipping missing paths, and upserting on re-delivery of the same path.

It also enforces the owner boundary on the model-supplied ``filePath``
(``owner_allowed_roots`` + ``assert_owned``), so every delivery test scopes that
allowlist to a tmp root via the ``owned_root`` fixture.

DB fixture mirrors ``test_attachment_parse`` — tmp SQLite + monkeypatched
``AsyncSessionLocal`` so the handler's ``async_unit_of_work`` binds to it.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import valuz_agent.boot.kernel  # noqa: F401 — puts kernel src/ on sys.path
from valuz_agent.integrations.toolkit_mcp_server import HostExecContext

from valuz_agent.infra.database import Base
from valuz_agent.modules.sessions import artifacts_tool as artifacts_tool_mod
from valuz_agent.modules.sessions.artifacts_tool import _deliver_artifacts_handler
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.models import SessionArtifactRow


@pytest.fixture
def owner() -> str:
    return "u1"


@pytest.fixture
def owned_root(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Scope the owner allowlist to one tmp dir — the caller's "workspace".

    Real ``owner_allowed_roots`` reads the managed project root + the owner's
    project rows; pinning it here keeps these tests about the handler and gives
    every test a path that is deliberately OUTSIDE the boundary (``tmp_path``
    itself, the parent of the returned root).
    """
    root = tmp_path / "workspace"
    root.mkdir()

    async def _roots(user_id: str):  # type: ignore[no-untyped-def]
        return [root.resolve()]

    monkeypatch.setattr(artifacts_tool_mod, "owner_allowed_roots", _roots)
    return root


@pytest.fixture
def session_factory(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "artifacts.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[SessionArtifactRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
    return factory


async def _list(factory, session_id: str = "s1") -> list[SessionArtifactRow]:
    async with factory() as db:
        return await SessionDatastore(db).list_artifacts("u1", session_id)


async def test_records_artifact_and_derives_metadata(session_factory, owner, owned_root):  # type: ignore[no-untyped-def]
    f = owned_root / "report.html"
    f.write_text("<html>hi</html>", encoding="utf-8")

    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(f)}]},
        HostExecContext(session_id="s1", user_id=owner),
    )

    assert not result.is_error
    rows = await _list(session_factory)
    assert len(rows) == 1
    row = rows[0]
    assert row.file_name == "report.html"
    assert row.file_size == f.stat().st_size
    assert row.mime_type == "text/html"
    assert row.file_path == str(f)


async def test_honors_explicit_metadata(session_factory, owner, owned_root):  # type: ignore[no-untyped-def]
    f = owned_root / "data.bin"
    f.write_bytes(b"\x00\x01\x02")

    result = await _deliver_artifacts_handler(
        {
            "attachments": [
                {
                    "filePath": str(f),
                    "fileName": "Pretty Name.bin",
                    "fileSize": 999,
                    "mimeType": "application/x-custom",
                }
            ]
        },
        HostExecContext(session_id="s1", user_id=owner),
    )

    assert not result.is_error
    (row,) = await _list(session_factory)
    assert row.file_name == "Pretty Name.bin"
    assert row.file_size == 999
    assert row.mime_type == "application/x-custom"


async def test_redelivery_upserts_in_place(session_factory, owner, owned_root):  # type: ignore[no-untyped-def]
    f = owned_root / "out.md"
    f.write_text("v1", encoding="utf-8")
    await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(f)}]},
        HostExecContext(session_id="s1", user_id=owner),
    )
    # Grow the file, re-deliver the same path.
    f.write_text("v2 longer", encoding="utf-8")
    await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(f)}]},
        HostExecContext(session_id="s1", user_id=owner),
    )

    rows = await _list(session_factory)
    assert len(rows) == 1  # upsert, not append
    assert rows[0].file_size == f.stat().st_size


async def test_missing_file_is_skipped(session_factory, owner, owned_root):  # type: ignore[no-untyped-def]
    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(owned_root / "nope.txt")}]},
        HostExecContext(session_id="s1", user_id=owner),
    )

    # Nothing delivered → surfaced as an error so the model notices.
    assert result.is_error
    assert "skipped" in result.content
    assert await _list(session_factory) == []


async def test_empty_attachments_errors() -> None:
    result = await _deliver_artifacts_handler(
        {"attachments": []}, HostExecContext(session_id="s1", user_id="u1")
    )
    assert result.is_error


async def test_no_session_id_errors(owned_root) -> None:  # type: ignore[no-untyped-def]
    f = owned_root / "x.txt"
    f.write_text("x", encoding="utf-8")
    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(f)}]}, HostExecContext(user_id="u1")
    )
    assert result.is_error
    assert "session" in result.content.lower()


# ── Owner boundary ────────────────────────────────────────────────────────────


async def test_rejects_path_outside_owner_roots(session_factory, owner, owned_root, tmp_path):  # type: ignore[no-untyped-def]
    """Another tenant's absolute path must not become this owner's artifact."""
    intruder = tmp_path / "other_tenant" / "secret.pdf"
    intruder.parent.mkdir()
    intruder.write_text("not yours", encoding="utf-8")

    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(intruder)}]},
        HostExecContext(session_id="s1", user_id=owner),
    )

    assert result.is_error
    assert "outside your workspace" in result.content
    assert await _list(session_factory) == []


async def test_rejects_symlink_escaping_owner_roots(session_factory, owner, owned_root, tmp_path):  # type: ignore[no-untyped-def]
    """A link INSIDE the workspace pointing out of it is still out of bounds."""
    outside = tmp_path / "outside.pdf"
    outside.write_text("not yours", encoding="utf-8")
    link = owned_root / "innocent.pdf"
    link.symlink_to(outside)

    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(link)}]},
        HostExecContext(session_id="s1", user_id=owner),
    )

    assert result.is_error
    assert await _list(session_factory) == []


async def test_out_of_bounds_path_is_not_an_existence_oracle(  # type: ignore[no-untyped-def]
    session_factory,
    owner,
    owned_root,
    tmp_path,
):
    """Existing and non-existing out-of-bounds paths must be indistinguishable.

    The boundary check runs before the ``isfile`` probe, so both report the same
    reason — otherwise "file not found" vs "outside your workspace" would leak
    whether another tenant holds that path.
    """
    real = tmp_path / "elsewhere_real.pdf"
    real.write_text("x", encoding="utf-8")
    ghost = tmp_path / "elsewhere_ghost.pdf"

    reasons = []
    for p in (real, ghost):
        result = await _deliver_artifacts_handler(
            {"attachments": [{"filePath": str(p)}]},
            HostExecContext(session_id="s1", user_id=owner),
        )
        reasons.append(json.loads(result.content)["skipped"][0]["reason"])

    assert reasons[0] == reasons[1]


async def test_partial_batch_records_only_owned_entries(  # type: ignore[no-untyped-def]
    session_factory,
    owner,
    owned_root,
    tmp_path,
):
    """One bad path must not sink the legitimate deliveries beside it."""
    ok = owned_root / "mine.md"
    ok.write_text("mine", encoding="utf-8")
    intruder = tmp_path / "theirs.md"
    intruder.write_text("theirs", encoding="utf-8")

    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(ok)}, {"filePath": str(intruder)}]},
        HostExecContext(session_id="s1", user_id=owner),
    )

    assert not result.is_error  # at least one delivered
    payload = json.loads(result.content)
    assert payload["delivered"] == ["mine.md"]
    assert len(payload["skipped"]) == 1
    (row,) = await _list(session_factory)
    assert row.file_path == str(ok)


async def test_unresolvable_workspace_root_fails_closed(  # type: ignore[no-untyped-def]
    session_factory,
    owner,
    monkeypatch,
    tmp_path,
):
    """No resolvable root → refuse the whole call, with a distinct message.

    Reporting every entry as "outside your workspace" would send the model
    chasing paths that were never the problem.
    """
    f = tmp_path / "report.md"
    f.write_text("x", encoding="utf-8")

    async def _no_roots(user_id: str):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(artifacts_tool_mod, "owner_allowed_roots", _no_roots)

    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(f)}]},
        HostExecContext(session_id="s1", user_id=owner),
    )

    assert result.is_error
    assert "workspace root" in result.content
    assert await _list(session_factory) == []
