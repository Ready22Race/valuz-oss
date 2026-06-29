"""Local E2E — Postgres RLS backstop proves DB-level owner isolation.

Connects as a NON-owner role (``valuz_app``) through the data service's
``SET LOCAL app.current_user_id`` bridge and proves the RLS policy enforces
owner scoping at the database, independently of the app-layer ``user_id``
filter:

1. a write+read under the role's own owner works (the GUC bridge fired);
2. a raw ``SELECT id FROM sessions`` (NO user_id filter) under owner-B does NOT
   return owner-A's row — RLS scopes it;
3. ``load_session`` asking for owner-A's id under owner-B returns None — RLS
   overrides the app filter;
4. writing a row owned by someone else is rejected by ``WITH CHECK``.

Requires the local podman PG migrated to head (incl. 0003). Run from backend/:

    RLS_OWNER_DSN=postgresql+asyncpg://valuz:valuz@127.0.0.1:5432/valuz_kernel \
    uv run python scripts/e2e_rls_postgres.py
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede app.*/src.* (sys.path)
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for app.*/src.*

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data_service import _owner_ctx, install_rls_guc
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core.agent_config import AgentConfig
from src.core.types import Session

OWNER_DSN = os.environ.get(
    "RLS_OWNER_DSN", "postgresql+asyncpg://valuz:valuz@127.0.0.1:5432/valuz_kernel"
)
APP_DSN = os.environ.get(
    "RLS_APP_DSN", "postgresql+asyncpg://valuz_app:app@127.0.0.1:5432/valuz_kernel"
)


def _ok(msg: str) -> None:
    print(f"  \033[32mPASS\033[0m {msg}")


def _sess(sid: str, owner: str) -> Session:
    return Session(
        id=sid,
        user_id=owner,
        agent_config=AgentConfig(id="a", name="a", model="claude-sonnet-4-6"),
        cwd="/tmp/rls",
    )


async def _ensure_app_role(owner_engine) -> None:
    async with owner_engine.begin() as c:
        await c.execute(
            text(
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='valuz_app') THEN "
                "CREATE ROLE valuz_app LOGIN PASSWORD 'app'; END IF; END $$;"
            )
        )
        await c.execute(text("GRANT USAGE ON SCHEMA public TO valuz_app"))
        await c.execute(
            text("GRANT SELECT, INSERT, UPDATE, DELETE ON sessions, messages, events TO valuz_app")
        )
        await c.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO valuz_app"))


async def main() -> int:
    owner_engine = create_async_engine(OWNER_DSN)
    await _ensure_app_role(owner_engine)

    app_engine = create_async_engine(APP_DSN)
    install_rls_guc(app_engine)  # per-transaction SET LOCAL from _owner_ctx
    store = SQLAlchemyStore(async_sessionmaker(app_engine, expire_on_commit=False))

    sid_a = uuid.uuid4().hex
    rc = 1
    try:
        # 1) write + read under the role's own owner (proves the GUC bridge fired:
        #    without the GUC, WITH CHECK would reject even the correct owner).
        _owner_ctx.set("rls-a")
        await store.save_session(_sess(sid_a, "rls-a"))
        got = await store.load_session("rls-a", sid_a)
        assert got is not None and got.id == sid_a
        _ok("non-owner app role write+read under its own owner works (GUC bridge active)")

        # 2) raw unfiltered SELECT under owner-B must not see owner-A's row.
        _owner_ctx.set("rls-b")
        async with app_engine.begin() as conn:
            ids = {r[0] for r in (await conn.execute(text("SELECT id FROM sessions"))).fetchall()}
        assert sid_a not in ids, "RLS breach: unfiltered SELECT leaked another owner's row"
        _ok("raw unfiltered SELECT under owner-B is RLS-scoped (no owner-A rows)")

        # 3) app asks for owner-A's id while GUC=owner-B → RLS overrides → None.
        _owner_ctx.set("rls-b")
        assert await store.load_session("rls-a", sid_a) is None
        _ok("RLS overrides the app-layer filter (cross-owner load returns None)")

        # 4) WITH CHECK rejects writing a row owned by someone else.
        _owner_ctx.set("rls-b")
        try:
            await store.save_session(_sess(uuid.uuid4().hex, "rls-a"))
            raise AssertionError("RLS WITH CHECK breach: wrote a row for another owner")
        except AssertionError:
            raise
        except Exception:
            _ok("RLS WITH CHECK rejects writing a row owned by someone else")

        # 5) the owner/superuser connection bypasses RLS (sanity): sees owner-A's row.
        async with owner_engine.begin() as conn:
            ids = {r[0] for r in (await conn.execute(text("SELECT id FROM sessions"))).fetchall()}
        assert sid_a in ids
        _ok("table owner bypasses RLS (migrations / trusted reads unrestricted)")

        print("\n\033[32mRLS E2E PASSED\033[0m")
        rc = 0
    finally:
        await app_engine.dispose()
        await owner_engine.dispose()
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
