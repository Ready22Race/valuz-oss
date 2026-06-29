"""Kernel data service — StorePort exposed over HTTP (the T1 transport server).

A thin, standalone FastAPI app that REUSES the kernel's own ``SQLAlchemyStore``
(one store implementation, local + remote) and exposes each StorePort method as
``POST /rpc/{op}``. The sandbox's :class:`RemoteStoreHttp` is its only client.

Security: every request derives its owner from the VERIFIED bearer token (a
``TokenVerifier``), never from the request body — ``save_session`` overwrites
the body's ``user_id`` with the token owner, and reads/writes are scoped to it.
A missing/invalid token is a hard 401. The DB lives here (trusted side); the
sandbox holds only a token + this URL.

``create_data_service_app(store, verifier)`` (used by tests and the seatbelt
closed loop) stashes the store + verifier on ``app.state``; the module-level
router resolves them per request via dependencies. The DB engine + verifier
wiring for a standalone deployment is assembled by the caller.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from src.adapters import store_wire as sw
from src.core import StorePort
from src.core.token_signer import InvalidTokenError
from src.core.token_verifier import TokenVerifier

router = APIRouter()


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


async def _owner_dep(request: Request) -> str:
    """Owner from the VERIFIED token — never from the body (anti-spoof)."""
    verifier: TokenVerifier = request.app.state.verifier
    try:
        claims = verifier.verify(_bearer(request.headers.get("authorization")))
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if claims is None:
        raise HTTPException(status_code=401, detail="missing or invalid token")
    return claims.user_id


def _store_dep(request: Request) -> StorePort:
    store: StorePort = request.app.state.store
    return store


# Module-level Annotated aliases so ``from __future__ import annotations``
# (lazy string annotations) still resolve under FastAPI's get_type_hints.
OwnerDep = Annotated[str, Depends(_owner_dep)]
StoreDep = Annotated[StorePort, Depends(_store_dep)]
JsonBody = Annotated[dict[str, Any], Body()]


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# -- writes (owner forced from token; body user_id ignored) --


@router.post("/rpc/save_session")
async def save_session(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    session = replace(sw.row_to_session(body["session"]), user_id=owner_id)
    await store.save_session(session)
    return {"data": None}


@router.post("/rpc/save_message")
async def save_message(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    await store.save_message(owner_id, sw.row_to_message(body["message"]))
    return {"data": None}


@router.post("/rpc/append_event")
async def append_event(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    seq = await store.append_event(
        owner_id,
        body["session_id"],
        body["message_id"],
        sw.row_to_event(body["event"]),
        request_id=body.get("request_id"),
    )
    return {"data": seq}


@router.post("/rpc/delete_session")
async def delete_session(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    return {"data": await store.delete_session(owner_id, body["session_id"])}


# -- reads --


@router.post("/rpc/load_session")
async def load_session(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    s = await store.load_session(owner_id, body["session_id"])
    return {"data": sw.session_to_row(s) if s else None}


@router.post("/rpc/list_sessions")
async def list_sessions(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    rows = await store.list_sessions(
        owner_id,
        status=body.get("status"),
        ids=body.get("ids"),
        limit=body.get("limit", 50),
        offset=body.get("offset", 0),
    )
    return {"data": [sw.session_to_row(s) for s in rows]}


@router.post("/rpc/load_message")
async def load_message(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    m = await store.load_message(owner_id, body["message_id"])
    return {"data": sw.message_to_row(m) if m else None}


@router.post("/rpc/list_messages_for_session")
async def list_messages_for_session(
    body: JsonBody, owner_id: OwnerDep, store: StoreDep
) -> dict[str, Any]:
    rows = await store.list_messages_for_session(
        owner_id,
        body["session_id"],
        limit=body.get("limit", 50),
        offset=body.get("offset", 0),
    )
    return {"data": [sw.message_to_row(m) for m in rows]}


@router.post("/rpc/get_events")
async def get_events(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    rows = await store.get_events(
        owner_id,
        body["session_id"],
        limit=body.get("limit", 200),
        offset=body.get("offset", 0),
    )
    return {"data": [sw.event_to_row(e) for e in rows]}


@router.post("/rpc/get_events_for_message")
async def get_events_for_message(
    body: JsonBody, owner_id: OwnerDep, store: StoreDep
) -> dict[str, Any]:
    rows = await store.get_events_for_message(
        owner_id,
        body["message_id"],
        limit=body.get("limit", 200),
        offset=body.get("offset", 0),
    )
    return {"data": [sw.event_to_row(e) for e in rows]}


@router.post("/rpc/get_events_after")
async def get_events_after(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    rows = await store.get_events_after(
        owner_id,
        body["session_id"],
        after_seq=body.get("after_seq", 0),
        limit=body.get("limit", 200),
    )
    return {"data": [sw.stored_event_to_row(e) for e in rows]}


@router.post("/rpc/get_events_window")
async def get_events_window(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    events, has_more = await store.get_events_window(
        owner_id,
        body["session_id"],
        before_seq=body.get("before_seq"),
        turn_limit=body.get("turn_limit", 20),
    )
    return {
        "data": {
            "events": [sw.stored_event_to_row(e) for e in events],
            "has_more": has_more,
        }
    }


@router.post("/rpc/usage_rollup")
async def usage_rollup(body: JsonBody, owner_id: OwnerDep, store: StoreDep) -> dict[str, Any]:
    rows = await store.usage_rollup(owner_id, body["start_ms"], body["end_ms"])
    return {"data": [sw.usage_rollup_to_row(u) for u in rows]}


def create_data_service_app(store: StorePort, verifier: TokenVerifier) -> FastAPI:
    """Build the data-service ASGI app over ``store``, authed by ``verifier``."""
    app = FastAPI(title="Valuz Kernel Data Service", version="0.1.0")
    app.state.store = store
    app.state.verifier = verifier
    app.include_router(router)
    return app
