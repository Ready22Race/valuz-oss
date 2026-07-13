"""ModelScopeClient list caching — the fallback connectors tab must not
re-fetch the ModelScope catalog on every visit within the TTL window."""

from __future__ import annotations

import httpx
import pytest

from valuz_agent.modules.marketplace.modelscope import ModelScopeClient

_LIST_PAYLOAD = {
    "success": True,
    "data": {
        "mcp_server_list": [{"id": "acme/search-tool", "name": "Search Tool"}],
        "total_count": 1,
    },
}


def _client(handler) -> ModelScopeClient:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport)
    return ModelScopeClient(client=async_client)


@pytest.mark.asyncio
async def test_list_servers_cached_within_ttl() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=_LIST_PAYLOAD)

    ms = _client(handler)
    first = await ms.list_servers(category=None, search=None, is_hosted=True, page=1, page_size=30)
    second = await ms.list_servers(category=None, search=None, is_hosted=True, page=1, page_size=30)

    assert first == second
    assert len(calls) == 1  # second read served from the TTL cache


@pytest.mark.asyncio
async def test_list_servers_cache_keyed_by_filters() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=_LIST_PAYLOAD)

    ms = _client(handler)
    await ms.list_servers(category=None, search=None, is_hosted=True, page=1, page_size=30)
    await ms.list_servers(category="search", search=None, is_hosted=True, page=1, page_size=30)
    await ms.list_servers(category=None, search="pdf", is_hosted=True, page=1, page_size=30)

    assert len(calls) == 3  # distinct filters are distinct cache entries
