"""Local E2E — sandbox (seatbelt, remote store) <-> data service + survival.

Runs a REAL ``sandbox-exec``-confined kernel in remote-store mode against a
REAL data service (our thin StorePort-over-HTTP server) on a loopback port, and
proves the whole point of the design:

1. the sandbox boots with **no database** (``KERNEL_STORE=remote``, no DSN/driver);
2. a WRITE through the sandbox (create_session) lands in the data service's DB;
3. a READ through the sandbox comes back from the data service;
4. after the sandbox is **destroyed**, the data is still queryable directly —
   queries do NOT depend on the sandbox being alive (the SaaS requirement).

macOS only (seatbelt). Run from ``backend/``:

    uv run python scripts/e2e_remote_store_seatbelt.py
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede app.*/src.* (sys.path)
from __future__ import annotations

import asyncio
import os
import secrets
import socket
import sys
import tempfile
import uuid
from pathlib import Path

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for app.*/src.*

import httpx
import uvicorn
from app.data_service import create_data_service_app
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core.token_signer import HmacTokenVerifier, TokenSigner
from valuz_agent.integrations.sandbox_seatbelt import SeatbeltSandboxProvider, seatbelt_preflight
from valuz_agent.ports.sandbox_provider import MountSpec, SandboxSpec

OWNER = "owner-a"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _ok(msg: str) -> None:
    print(f"  \033[32mPASS\033[0m {msg}")


async def main() -> int:
    problems = seatbelt_preflight()
    if problems:
        print(f"SKIP — seatbelt unavailable: {problems}")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="valuz-e2e-"))
    proj = tmp / "proj"
    proj.mkdir()
    secret = secrets.token_urlsafe(32)

    # 1) Data service over SQLite (default) or a real Postgres via
    #    E2E_DATABASE_URL (already migrated — e.g. the local podman PG).
    db_url = os.environ.get("E2E_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp / 'data.db'}"
    print(f"data store: {db_url.split('@')[-1] if '@' in db_url else db_url}")
    engine = create_async_engine(db_url)
    if db_url.startswith("sqlite"):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    store = SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False))
    app = create_data_service_app(store, HmacTokenVerifier(secret))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    while not server.started:  # wait for the socket to be listening
        await asyncio.sleep(0.05)
    data_api = f"http://127.0.0.1:{port}"
    print(f"data service up at {data_api}")

    # The sandbox's data-API JWT (owner == the header owner the host sends below).
    data_token = TokenSigner(secret).sign(user_id=OWNER, ttl_s=3600)

    # 2) Provision a real seatbelt sandbox in REMOTE store mode (no DB at all).
    provider = SeatbeltSandboxProvider()
    spec = SandboxSpec(
        sandbox_id="e2e-remote",
        kernel_db_path=str(tmp / "unused-kernel.db"),  # never created in remote mode
        mounts=(MountSpec(target=str(tmp), source=str(tmp), mode="rw"),),
        env={
            "KERNEL_STORE": "remote",
            "VALUZ_DATA_API_URL": data_api,
            "VALUZ_DATA_API_TOKEN": data_token,
            "VALUZ_DATA_API_KIND": "http",
        },
    )
    sid = uuid.uuid4().hex
    rc = 1
    try:
        endpoint = await provider.provision(spec)
        print(f"sandbox up at {endpoint.base_url}")
        assert await provider.health("e2e-remote") is True
        _ok("sandbox booted in remote-store mode (no DB / DSN) and is healthy")
        assert not (tmp / "unused-kernel.db").exists()
        _ok("no private kernel SQLite was created in the sandbox")

        hdr = {"Authorization": f"Bearer {endpoint.token}", "X-Valuz-Owner-Id": OWNER}
        async with httpx.AsyncClient(timeout=10) as c:
            # 3) WRITE through the sandbox -> RemoteStoreHttp -> data service -> DB.
            create = await c.post(
                f"{endpoint.base_url}/api/v1/sessions",
                headers=hdr,
                json={
                    "id": sid,
                    "agent_config": {"name": "e2e-agent"},
                    "cwd": str(proj),
                    "runtime_provider": "claude_agent",
                },
            )
            assert create.status_code in (200, 201), (
                f"create_session: {create.status_code} {create.text}"
            )
            sid = create.json()["data"]["id"]  # authoritative persisted id
            _ok("write through sandbox (create_session) succeeded")

            # 4) READ through the sandbox -> data service.
            listed = await c.get(f"{endpoint.base_url}/api/v1/sessions", headers=hdr)
            ids = [s["id"] for s in listed.json()["data"]]
            assert sid in ids, f"session not visible through sandbox: {ids}"
            _ok("read through sandbox returns the session from the data service")

        # 5) Destroy the sandbox — execution plane gone.
        await provider.destroy("e2e-remote")
        assert await provider.health("e2e-remote") is False
        _ok("sandbox destroyed")

        # 6) SURVIVAL — query the data service directly; data outlives the sandbox.
        async with httpx.AsyncClient(timeout=10) as c:
            survived = await c.post(
                f"{data_api}/rpc/list_sessions",
                headers={"Authorization": f"Bearer {data_token}"},
                json={"limit": 50, "offset": 0},
            )
            ids = [s["id"] for s in survived.json()["data"]]
            assert sid in ids, f"data did NOT survive sandbox teardown: {ids}"
        _ok("data still queryable after sandbox is gone (the goal)")

        # 7) ISOLATION — a different owner's token sees nothing.
        other = TokenSigner(secret).sign(user_id="owner-b", ttl_s=3600)
        async with httpx.AsyncClient(timeout=10) as c:
            empty = await c.post(
                f"{data_api}/rpc/list_sessions",
                headers={"Authorization": f"Bearer {other}"},
                json={"limit": 50, "offset": 0},
            )
            other_ids = [s["id"] for s in empty.json()["data"]]
            assert sid not in other_ids, "token-scoped isolation breached"
        _ok("a different owner's token sees nothing (token-scoped isolation)")

        print("\n\033[32mE2E PASSED\033[0m")
        rc = 0
    finally:
        await provider.destroy("e2e-remote")
        server.should_exit = True
        await serve_task
        await engine.dispose()
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
