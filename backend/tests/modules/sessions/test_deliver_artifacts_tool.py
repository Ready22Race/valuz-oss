"""Tests for the built-in ``deliver_artifacts`` MCP tool handler.

The handler runs on the host toolkit MCP path: the kernel ``ExecContext``
carries the calling ``session_id`` and the owner is published in the auth
context by the MCP wrapper. It records agent-declared output files into
``valuz_session_artifact`` (the durable "生成文件" list), deriving
``fileName`` / ``fileSize`` / ``mimeType`` from disk when the model omits them,
skipping missing paths, and upserting on re-delivery of the same path.

DB fixture mirrors ``test_attachment_parse`` — tmp SQLite + monkeypatched
``AsyncSessionLocal`` so the handler's ``async_unit_of_work`` binds to it.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import valuz_agent.boot.kernel  # noqa: F401 — puts kernel src/ on sys.path
from src.core.tools import ExecContext

from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id
from valuz_agent.infra.database import Base
from valuz_agent.modules.sessions.artifacts_tool import _deliver_artifacts_handler
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.models import SessionArtifactRow


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


@pytest.fixture
def owner():  # type: ignore[no-untyped-def]
    token = set_current_user_id("u1")
    try:
        yield "u1"
    finally:
        reset_current_user_id(token)


async def _list(factory, session_id: str = "s1") -> list[SessionArtifactRow]:
    async with factory() as db:
        return await SessionDatastore(db).list_artifacts("u1", session_id)


async def test_records_artifact_and_derives_metadata(session_factory, owner, tmp_path):  # type: ignore[no-untyped-def]
    f = tmp_path / "report.html"
    f.write_text("<html>hi</html>", encoding="utf-8")

    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(f)}]}, ExecContext(session_id="s1")
    )

    assert not result.is_error
    rows = await _list(session_factory)
    assert len(rows) == 1
    row = rows[0]
    assert row.file_name == "report.html"
    assert row.file_size == f.stat().st_size
    assert row.mime_type == "text/html"
    assert row.file_path == str(f)


async def test_honors_explicit_metadata(session_factory, owner, tmp_path):  # type: ignore[no-untyped-def]
    f = tmp_path / "data.bin"
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
        ExecContext(session_id="s1"),
    )

    assert not result.is_error
    (row,) = await _list(session_factory)
    assert row.file_name == "Pretty Name.bin"
    assert row.file_size == 999
    assert row.mime_type == "application/x-custom"


async def test_redelivery_upserts_in_place(session_factory, owner, tmp_path):  # type: ignore[no-untyped-def]
    f = tmp_path / "out.md"
    f.write_text("v1", encoding="utf-8")
    await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(f)}]}, ExecContext(session_id="s1")
    )
    # Grow the file, re-deliver the same path.
    f.write_text("v2 longer", encoding="utf-8")
    await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(f)}]}, ExecContext(session_id="s1")
    )

    rows = await _list(session_factory)
    assert len(rows) == 1  # upsert, not append
    assert rows[0].file_size == f.stat().st_size


async def test_missing_file_is_skipped(session_factory, owner, tmp_path):  # type: ignore[no-untyped-def]
    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(tmp_path / "nope.txt")}]},
        ExecContext(session_id="s1"),
    )

    # Nothing delivered → surfaced as an error so the model notices.
    assert result.is_error
    assert "skipped" in result.content
    assert await _list(session_factory) == []


async def test_empty_attachments_errors() -> None:
    result = await _deliver_artifacts_handler({"attachments": []}, ExecContext(session_id="s1"))
    assert result.is_error


async def test_no_session_id_errors(tmp_path) -> None:  # type: ignore[no-untyped-def]
    f = tmp_path / "x.txt"
    f.write_text("x", encoding="utf-8")
    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(f)}]}, ExecContext()
    )
    assert result.is_error
    assert "session" in result.content.lower()
