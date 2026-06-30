"""Host read-routing — remote tier reads history from the DataService.

The SaaS sandbox is ephemeral, so in ``remote`` mode the host reads event
HISTORY straight from the central DataService (not via the maybe-dead sandbox
kernel). Covers the host ``DataServiceReadClient`` row→EventData mapping and the
``event_sse_adapter`` reader routing. No network — httpx ``MockTransport``.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede app.* (sys.path)
from __future__ import annotations

import json

import httpx
import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for app.*

from valuz_agent.adapters import event_sse_adapter as sse
from valuz_agent.adapters.data_service_client import DataServiceReadClient


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ds")


async def test_client_maps_get_events_after_rows():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        body = json.loads(request.content)
        seen["body"] = body
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "seq": 59,
                        "session_id": "s1",
                        "message_id": "m1",
                        "type": "user_message",
                        "data": {"text": "hi"},
                        "timestamp": 123,
                    }
                ]
            },
        )

    client = DataServiceReadClient(
        base_url="http://ds", token="jwt-A", http_client=_mock_client(handler)
    )
    try:
        events = await client.get_events("u", "s1", after_seq=0, limit=200)
    finally:
        await client.aclose()

    assert seen["path"] == "/rpc/get_events_after"
    assert seen["auth"] == "Bearer jwt-A"
    assert seen["body"] == {"session_id": "s1", "after_seq": 0, "limit": 200}
    assert len(events) == 1
    e = events[0]
    assert e.seq == 59 and e.type == "user_message" and e.data == {"text": "hi"}
    assert e.message_id == "m1" and e.timestamp == 123


async def test_client_maps_get_events_window():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "events": [
                        {
                            "seq": 1,
                            "session_id": "s",
                            "message_id": "m",
                            "type": "thinking",
                            "data": {},
                            "timestamp": 1,
                        }
                    ],
                    "has_more": True,
                }
            },
        )

    client = DataServiceReadClient(
        base_url="http://ds", token="t", http_client=_mock_client(handler)
    )
    try:
        window = await client.get_events_window("u", "s", before_seq=None, turn_limit=20)
    finally:
        await client.aclose()
    assert window.has_more is True
    assert len(window.items) == 1 and window.items[0].seq == 1


@pytest.fixture
def reset_reader():
    sse._data_service_reader = None
    yield
    sse._data_service_reader = None


def test_reader_routes_to_kernel_in_local_mode(reset_reader, monkeypatch):
    monkeypatch.setattr(sse.settings, "kernel_store", "local", raising=False)
    assert sse._history_reader() is sse.kernel_client


def test_reader_routes_to_data_service_in_remote_mode(reset_reader, monkeypatch):
    monkeypatch.setattr(sse.settings, "kernel_store", "remote", raising=False)
    monkeypatch.setattr(sse.settings, "kernel_data_api_url", "http://127.0.0.1:8400", raising=False)
    reader = sse._history_reader()
    assert isinstance(reader, DataServiceReadClient)


async def test_list_events_after_uses_data_service(reset_reader, monkeypatch):
    """End-to-end: in remote mode, list_events_after pulls + translates frames
    from the DataService even though the kernel seam is never touched."""
    from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rpc/get_events_after"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "seq": 5,
                        "session_id": "s",
                        "message_id": "m",
                        "type": "user_message",
                        "data": {"text": "yo"},
                        "timestamp": 9,
                    }
                ]
            },
        )

    monkeypatch.setattr(sse.settings, "kernel_store", "remote", raising=False)
    monkeypatch.setattr(sse.settings, "kernel_data_api_url", "http://ds", raising=False)
    sse._data_service_reader = DataServiceReadClient(
        base_url="http://ds", token="t", http_client=_mock_client(handler)
    )
    tok = set_current_user_id("u")
    try:
        frames = await sse.list_events_after("s", after_seq=0, limit=10)
    finally:
        reset_current_user_id(tok)
        await sse._data_service_reader.aclose()
    # The user_message event was fetched from the DataService and translated.
    assert len(frames) == 1
    assert frames[0].seq == 5
