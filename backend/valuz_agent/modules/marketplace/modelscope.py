"""Anonymous reader for ModelScope's public MCP catalog OpenAPI."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://modelscope.cn/openapi/v1"
_TIMEOUT_SECONDS = 15.0


class ModelScopeUnavailableError(Exception):
    """ModelScope could not be reached or returned an unusable payload."""


class ModelScopeClient:
    """Adapter over the public MCP catalog with short-lived detail reuse."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client
        self._detail_semaphore = asyncio.Semaphore(2)
        self._detail_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._detail_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._detail_failures: dict[str, float] = {}

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        try:
            if self._client is not None:
                response = await self._client.request(method, url, json=json)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                    response = await client.request(method, url, json=json)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("ModelScope request failed: %s %s: %s", method, path, exc)
            raise ModelScopeUnavailableError(str(exc)) from exc
        except ValueError as exc:
            raise ModelScopeUnavailableError("invalid JSON from ModelScope") from exc
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise ModelScopeUnavailableError("unexpected ModelScope payload")
        return payload

    async def list_servers(
        self,
        *,
        category: str | None = None,
        search: str | None = None,
        is_hosted: bool | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> tuple[list[dict[str, Any]], int]:
        safe_page = max(1, page)
        safe_page_size = max(1, min(page_size, 100))
        if safe_page * safe_page_size > 100:
            return [], 0
        body: dict[str, Any] = {
            "page_number": safe_page,
            "page_size": safe_page_size,
        }
        filter_: dict[str, Any] = {}
        if category:
            filter_["category"] = category
        if is_hosted is not None:
            filter_["is_hosted"] = is_hosted
        if filter_:
            body["filter"] = filter_
        if search:
            body["search"] = search
        payload = await self._request_json("PUT", "/mcp/servers", json=body)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ModelScopeUnavailableError("unexpected ModelScope list payload")
        servers = data.get("mcp_server_list")
        if not isinstance(servers, list):
            raise ModelScopeUnavailableError("unexpected ModelScope list payload")
        return [row for row in servers if isinstance(row, dict)], int(data.get("total_count") or 0)

    async def server_detail(self, server_id: str) -> dict[str, Any]:
        payload = await self._request_json("GET", f"/mcp/servers/{quote(server_id, safe='')}")
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise ModelScopeUnavailableError("unexpected ModelScope detail payload")
        return data

    async def server_detail_cached(
        self, server_id: str, *, ttl_seconds: float = 300
    ) -> dict[str, Any]:
        """Reuse README-bearing detail briefly and coalesce duplicate reads."""
        cached = self._detail_cache.get(server_id)
        if cached is not None and monotonic() - cached[0] < ttl_seconds:
            return cached[1]
        failed_at = self._detail_failures.get(server_id)
        if failed_at is not None and monotonic() - failed_at < 30:
            raise ModelScopeUnavailableError("ModelScope detail is temporarily unavailable")
        task = self._detail_tasks.get(server_id)
        if task is None:

            async def fetch() -> dict[str, Any]:
                async with self._detail_semaphore:
                    return await self.server_detail(server_id)

            task = asyncio.create_task(fetch())
            self._detail_tasks[server_id] = task

            def discard(done: asyncio.Task[dict[str, Any]]) -> None:
                if self._detail_tasks.get(server_id) is done:
                    self._detail_tasks.pop(server_id, None)

            task.add_done_callback(discard)
        try:
            detail = await asyncio.shield(task)
        except ModelScopeUnavailableError:
            self._detail_failures[server_id] = monotonic()
            raise
        self._detail_failures.pop(server_id, None)
        self._detail_cache[server_id] = (monotonic(), detail)
        return detail
