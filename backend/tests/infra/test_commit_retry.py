"""Lock-retry semantics for ``async_commit_with_retry``.

These tests force a single "database is locked" at commit time (deterministic,
no real contention) and assert the helper's state-preserving contract:

- a pending INSERT survives the lock-retry (no silent data loss);
- an UPDATE whose dirty state a rollback reverts fails loud (re-raises) instead
  of silently persisting the reverted row;
- a non-lock OperationalError propagates unchanged.

See ``valuz_agent/infra/db.py`` for the rationale.
"""

from __future__ import annotations

import pytest
from sqlalchemy import String, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from valuz_agent.infra.db import async_commit_with_retry


class Base(DeclarativeBase): ...


class Row(Base):
    __tablename__ = "commit_retry_row"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    note: Mapped[str] = mapped_column(String)


@pytest.fixture()
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _lock_once(db: AsyncSession) -> None:
    """Patch ``db.commit`` to raise 'database is locked' exactly once, then
    delegate to the real commit — mimicking a single SQLITE_BUSY at commit."""
    real = db.commit
    state = {"fired": False}

    async def flaky() -> None:
        if not state["fired"]:
            state["fired"] = True
            raise OperationalError("commit", {}, Exception("database is locked"))
        await real()

    db.commit = flaky  # type: ignore[method-assign]


async def test_pending_insert_survives_lock_retry(maker) -> None:
    db = maker()
    db.add(Row(id="r1", note="hello"))
    _lock_once(db)

    await async_commit_with_retry(db, where="test.insert")
    await db.close()

    verify = maker()
    got = await verify.get(Row, "r1")
    assert got is not None and got.note == "hello"  # not silently dropped
    await verify.close()


async def test_update_fails_loud_on_lock(maker) -> None:
    # Seed a row, then load+modify it (dirty) and hit a locked commit.
    seed = maker()
    seed.add(Row(id="r2", note="before"))
    await seed.commit()
    await seed.close()

    db = maker()
    row = (await db.execute(select(Row).where(Row.id == "r2"))).scalar_one()
    row.note = "after"
    _lock_once(db)

    # Fail loud rather than silently persisting the reverted ("before") state.
    with pytest.raises(OperationalError):
        await async_commit_with_retry(db, where="test.update")
    await db.close()

    verify = maker()
    got = await verify.get(Row, "r2")
    assert got is not None and got.note == "before"  # unchanged, not corrupted
    await verify.close()


async def test_non_lock_operational_error_propagates(maker) -> None:
    db = maker()
    db.add(Row(id="r3", note="x"))

    real = db.commit

    async def boom() -> None:
        raise OperationalError("commit", {}, Exception("no such table: nope"))

    db.commit = boom  # type: ignore[method-assign]

    with pytest.raises(OperationalError, match="no such table"):
        await async_commit_with_retry(db, where="test.other")
    db.commit = real  # type: ignore[method-assign]
    await db.close()
