from __future__ import annotations

import asyncio

import httpx
import pytest

from valuz_agent.modules.marketplace.modelscope import (
    ModelScopeClient,
    ModelScopeUnavailableError,
)


@pytest.mark.asyncio
async def test_list_servers_sends_category_search_and_caps_page_size() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "mcp_server_list": [{"id": "owner/server", "view_count": 12}],
                    "total_count": 321,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ModelScopeClient(base_url="https://example.test/openapi/v1", client=http)
        rows, total = await client.list_servers(
            category="developer-tools", search="git", is_hosted=True, page_size=500
        )

    assert rows == [{"id": "owner/server", "view_count": 12}]
    assert total == 321
    assert requests[0].method == "PUT"
    assert requests[0].read() == (
        b'{"page_number":1,"page_size":100,"filter":{"category":"developer-tools",'
        b'"is_hosted":true},'
        b'"search":"git"}'
    )


@pytest.mark.asyncio
async def test_server_detail_encodes_modelscope_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.endswith(b"/%40modelcontextprotocol%2Ffetch")
        return httpx.Response(200, json={"success": True, "data": {"id": "@model/fetch"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ModelScopeClient(base_url="https://example.test/openapi/v1", client=http)
        detail = await client.server_detail("@modelcontextprotocol/fetch")

    assert detail["id"] == "@model/fetch"


@pytest.mark.asyncio
async def test_server_detail_cached_coalesces_and_briefly_reuses_reads() -> None:
    calls = 0
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await gate.wait()
        return httpx.Response(
            200,
            json={"success": True, "data": {"id": "owner/shared", "readme": "copy"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ModelScopeClient(base_url="https://example.test/openapi/v1", client=http)
        first = asyncio.create_task(client.server_detail_cached("owner/shared"))
        second = asyncio.create_task(client.server_detail_cached("owner/shared"))
        await asyncio.sleep(0)
        gate.set()
        details = await asyncio.gather(first, second)
        third = await client.server_detail_cached("owner/shared")

    assert calls == 1
    assert details[0] == details[1] == third


@pytest.mark.asyncio
async def test_server_detail_cached_suppresses_repeated_upstream_failure() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"message": "slow down"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ModelScopeClient(base_url="https://example.test/openapi/v1", client=http)
        with pytest.raises(ModelScopeUnavailableError):
            await client.server_detail_cached("owner/limited")
        with pytest.raises(ModelScopeUnavailableError):
            await client.server_detail_cached("owner/limited")

    assert calls == 1


@pytest.mark.asyncio
async def test_list_servers_forwards_valid_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.read() == b'{"page_number":3,"page_size":24}'
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"mcp_server_list": [], "total_count": 90},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ModelScopeClient(base_url="https://example.test/openapi/v1", client=http)
        rows, total = await client.list_servers(page=3, page_size=24)

    assert rows == [] and total == 90


@pytest.mark.asyncio
async def test_upstream_failure_is_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "down"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ModelScopeClient(base_url="https://example.test/openapi/v1", client=http)
        with pytest.raises(ModelScopeUnavailableError):
            await client.list_servers()
