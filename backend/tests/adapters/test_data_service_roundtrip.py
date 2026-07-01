"""Phase B-sim — RemoteStoreHttp <-> data service end-to-end (no DB creds, no port).

Drives the real :class:`RemoteStoreHttp` against the real
``create_data_service_app`` over an in-process ASGI transport, backed by a tmp
SQLite ``SQLAlchemyStore`` and HS256 JWT auth. Proves the T1 contract:
round-trip of all StorePort methods, server-side idempotency, owner isolation,
forged-owner rejection, and invalid-token failure.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.*/app.* (sys.path)
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*/app.*

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data_service import create_data_service_app
from src.adapters.remote_store import RemoteFatalError
from src.adapters.remote_store_http import RemoteStoreHttp
from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.token_signer import HmacTokenVerifier, TokenSigner
from src.core.types import Message, Session, UserMessage

_SECRET = "test-secret-please-be-at-least-32-bytes-long!!"


@pytest.fixture
async def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kernel.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    store = SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False))
    signer = TokenSigner(_SECRET)
    app = create_data_service_app(store, HmacTokenVerifier(_SECRET))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://svc")

    def client_for(uid: str) -> RemoteStoreHttp:
        async def _token() -> str:
            return signer.sign(user_id=uid)

        return RemoteStoreHttp(
            base_url="http://svc",
            access_token=_token,
            http_client=http,
            max_attempts=2,
            base_backoff_s=0.0,
        )

    yield SimpleNamespace(store=store, http=http, signer=signer, client_for=client_for)
    await http.aclose()
    await engine.dispose()


def _sess(sid: str, owner: str, tmp_path) -> Session:
    return Session(
        id=sid,
        user_id=owner,
        agent_config=AgentConfig(id="a", name="a", model="claude-sonnet-4-6"),
        cwd=str(tmp_path),
    )


async def _seed(client: RemoteStoreHttp, owner: str, tmp_path) -> tuple[str, str]:
    sid, mid = uuid.uuid4().hex, uuid.uuid4().hex
    await client.save_session(_sess(sid, owner, tmp_path))
    await client.save_message(
        owner,
        Message(
            id=mid,
            session_id=sid,
            user_message=UserMessage(text="hi"),
            started_at=1,
            status="running",
        ),
    )
    return sid, mid


async def test_full_round_trip(env, tmp_path):
    c = env.client_for("ua")
    sid, mid = await _seed(c, "ua", tmp_path)

    got = await c.load_session("ua", sid)
    assert got is not None and got.id == sid and got.user_id == "ua"
    assert [s.id for s in await c.list_sessions("ua")] == [sid]
    assert (await c.load_message("ua", mid)).id == mid
    assert [m.id for m in await c.list_messages_for_session("ua", sid)] == [mid]

    seq = await c.append_event("ua", sid, mid, Event(type="user_message", data={}))
    assert seq is not None
    assert len(await c.get_events("ua", sid)) == 1
    assert len(await c.get_events_for_message("ua", mid)) == 1
    after = await c.get_events_after("ua", sid, after_seq=0)
    assert len(after) == 1 and after[0].seq == seq
    window, has_more = await c.get_events_window("ua", sid)
    assert len(window) == 1 and has_more is False
    assert isinstance(await c.usage_rollup("ua", 0, 10**13), list)

    assert await c.delete_session("ua", sid) is True
    assert await c.load_session("ua", sid) is None


async def test_append_idempotent_over_http(env, tmp_path):
    c = env.client_for("ua")
    sid, mid = await _seed(c, "ua", tmp_path)
    # Two POSTs with the SAME request_id (a simulated retry) → one row, orig seq.
    body = {
        "session_id": sid,
        "message_id": mid,
        "event": {"type": "user_message", "data": {}},
        "request_id": "rid-1",
    }
    d1 = await c._post("append_event", body)
    d2 = await c._post("append_event", body)
    assert d1 == d2 and d1 is not None
    assert len(await c.get_events("ua", sid)) == 1


async def test_owner_isolation(env, tmp_path):
    a, b = env.client_for("ua"), env.client_for("ub")
    sid, _ = await _seed(a, "ua", tmp_path)
    # user_B holds a valid token but a different owner → sees nothing of A's.
    assert await b.load_session("ub", sid) is None
    assert [s.id for s in await b.list_sessions("ub")] == []
    assert (await a.load_session("ua", sid)).id == sid


async def test_forged_owner_in_body_is_ignored(env, tmp_path):
    a = env.client_for("ua")
    # The wire row claims user_id "evil"; the server forces owner from the token.
    await a.save_session(_sess("forged", "evil", tmp_path))
    got = await a.load_session("ua", "forged")
    assert got is not None and got.user_id == "ua"  # owner = token, not body
    assert await env.client_for("evil").load_session("evil", "forged") is None


async def test_invalid_token_is_fatal(env):
    async def _bad() -> str:
        return "not.a.jwt"

    c = RemoteStoreHttp(
        base_url="http://svc",
        access_token=_bad,
        http_client=env.http,
        max_attempts=2,
        base_backoff_s=0.0,
    )
    with pytest.raises(RemoteFatalError):
        await c.load_session("ua", "x")
