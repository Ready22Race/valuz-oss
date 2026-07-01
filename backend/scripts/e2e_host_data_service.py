"""E2E: host-mounted DataService over real Postgres, reached with a minted JWT.

Exercises the core of the DataService-as-host-router refactor without a sandbox:
build the SAME app the host mounts (create_data_service_app over PG), mint a
token with the host secret, and round-trip through it via the kernel's
``RemoteStoreHttp`` client (the exact client a sandbox uses) — proving auth +
owner-from-token + write + read against a live PG.

Run (PG up via `make pg`):
  cd backend && PYTHONPATH=. uv run python scripts/e2e_host_data_service.py
  # override DSN with DS_PG_DSN=postgresql+asyncpg://user:pass@host:5432/db
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.*/app.*
from __future__ import annotations

import asyncio
import os
import secrets
import sys

import valuz_agent.boot.kernel as kb  # noqa: F401 — sys.path side-effect + helpers

import httpx
from app.data_service import create_data_service_app
from src.adapters.remote_store_http import RemoteStoreHttp
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import Message, Session, UserMessage

DSN = os.environ.get("DS_PG_DSN", "postgresql+asyncpg://valuz:valuz@127.0.0.1:5432/valuz_kernel")
OWNER = "e2e-host-ds-owner"


async def main() -> int:
    secret = secrets.token_urlsafe(32)
    store, engine = kb.build_host_data_service_store(DSN)
    await kb.ensure_host_data_service_schema(engine)
    app = create_data_service_app(store, kb.make_host_data_service_verifier(secret))
    token = kb.mint_data_service_token(secret, user_id=OWNER, ttl_s=3600)

    async def _tok() -> str:
        return token

    transport = httpx.ASGITransport(app=app)
    http = httpx.AsyncClient(transport=transport, base_url="http://host-ds")
    remote = RemoteStoreHttp(base_url="http://host-ds", access_token=_tok, http_client=http)

    sid = secrets.token_hex(16)
    mid = secrets.token_hex(16)
    try:
        # 1) anti-spoof: an unsigned/garbage token is rejected (401 → fatal).
        bad = RemoteStoreHttp(
            base_url="http://host-ds",
            access_token=lambda: _bad(),  # type: ignore[arg-type]
            http_client=httpx.AsyncClient(transport=transport, base_url="http://host-ds"),
        )
        spoof_rejected = False
        try:
            await bad.load_session(OWNER, sid)
        except Exception:  # noqa: BLE001
            spoof_rejected = True
        assert spoof_rejected, "unsigned token was NOT rejected"

        # 2) write a session + message + events through the JWT'd client.
        await remote.save_session(
            Session(
                id=sid,
                user_id=OWNER,
                agent_config=AgentConfig(id="a", name="a", model="claude-sonnet-4-6"),
                cwd="/tmp/e2e",
            )
        )
        await remote.save_message(
            OWNER,
            Message(
                id=mid,
                session_id=sid,
                user_message=UserMessage(text="hi"),
                started_at=0,
                status="running",
            ),
        )
        s1 = await remote.append_event(
            OWNER, sid, mid, Event(type="user_message", data={"text": "hi"})
        )
        s2 = await remote.append_event(
            OWNER, sid, mid, Event(type="assistant_message", data={"text": "yo"})
        )
        assert s1 and s2 and s2 > s1, f"seq not monotonic: {s1},{s2}"

        # 3) read back through the client (owner from token).
        got = await remote.load_session(OWNER, sid)
        assert got is not None and got.id == sid, "session not read back"
        events = await remote.get_events(OWNER, sid)
        assert len(events) == 2, f"expected 2 events, got {len(events)}"

        # 4) verify the rows really landed in PG (read straight from the store).
        direct = await store.get_events(OWNER, sid, limit=10)
        assert len(direct) == 2, f"PG has {len(direct)} events"

        # 5) HOST unified read path: bind the in-process reader (what the lifespan
        # does) and read history through the REAL event_sse_adapter — the same
        # path the SSE endpoint uses. No kernel/sandbox involved.
        from valuz_agent.adapters import event_sse_adapter as sse
        from valuz_agent.adapters.data_service_local import bind_local_reader
        from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id

        bind_local_reader(store)
        tok = set_current_user_id(OWNER)
        try:
            frames = await sse.list_events_after(sid, after_seq=0, limit=50)
            assert len(frames) == 2, f"host read returned {len(frames)} frames"
            window = await sse.list_events_window(sid, turn_limit=5)
            assert len(window.items) >= 1, "turn window empty via DataService read"

            # 6) "kill the sandbox": close the sandbox-side write client. The host
            # read path must STILL work — reads go through the DataService, not the
            # (now-gone) sandbox kernel.
            await http.aclose()
            frames_after_kill = await sse.list_events_after(sid, after_seq=0, limit=50)
            assert len(frames_after_kill) == 2, "history unreadable after sandbox 'died'"
        finally:
            reset_current_user_id(tok)
            bind_local_reader(None)

        print(
            f"OK — host DataService over PG: session {sid[:8]} (seqs {s1},{s2}). "
            f"sandbox-write via JWT → PG; host unified read OK; "
            f"read survived sandbox kill; spoof rejected."
        )
        return 0
    finally:
        # cleanup this E2E session (fresh client — the original may be closed).
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://host-ds"
        ) as cleanup_http:
            cleanup = RemoteStoreHttp(
                base_url="http://host-ds", access_token=_tok, http_client=cleanup_http
            )
            try:
                await cleanup.delete_session(OWNER, sid)
            except Exception:  # noqa: BLE001
                pass
        await engine.dispose()


async def _bad() -> str:
    return "not.a.valid.jwt"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
