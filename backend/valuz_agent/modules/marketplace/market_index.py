"""Async client for the Valuz market index — the SOLE marketplace data
source (see ``docs/cloud-marketplace/design/oss.md``). Points at Valuz
cloud's public marketplace API by default; a self-hosted deployment can
point it at a compatible implementation via
``Settings.marketplace_index_base_url``.

The payloads returned here are already the ``Marketplace*`` DTO shapes
(``MarketplaceCategoryList`` / ``MarketplaceItemList`` / ``MarketplaceItemDetail``)
as raw JSON — the service layer only recomputes ``installed`` locally and
validates through Pydantic. Every request carries ``channel`` (this
install's edition/build channel) and ``locale`` (the caller's active
locale).

Shape and caching strategy mirror the two clients this module replaces
(``skillhub.py`` / ``modelscope.py``, both retired): a thin httpx reader, a
short in-memory TTL cache (per-process, not a durable mirror), and one
"unavailable" exception collapsing every failure mode (transport error,
non-2xx, non-JSON body).
"""

from __future__ import annotations

import logging
import time
from typing import Any, cast
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://cloud.valuz.dev"

_TIMEOUT_SECONDS = 15.0
_CATEGORIES_TTL = 600.0
_LIST_TTL = 60.0
_DETAIL_TTL = 300.0


class MarketIndexUnavailableError(Exception):
    """The market index could not be reached or returned an unusable payload."""


class MarketIndexClient:
    """Thin cached reader over the market index HTTP API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        channel: str = "oss",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.channel = channel
        self._client = client  # injected in tests; None → one client per call
        self._cache: dict[str, tuple[float, Any]] = {}

    # -- low-level ---------------------------------------------------------

    async def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        query = {**params, "channel": self.channel}
        try:
            if self._client is not None:
                resp = await self._client.get(url, params=query)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                    resp = await client.get(url, params=query)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.warning("market index request failed: %s %s: %s", path, query, exc)
            raise MarketIndexUnavailableError(str(exc)) from exc
        except ValueError as exc:  # non-JSON body
            logger.warning("market index returned non-JSON for %s: %s", path, exc)
            raise MarketIndexUnavailableError("invalid JSON from market index") from exc

    def _cached(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is not None and entry[0] > time.monotonic():
            return entry[1]
        return None

    def _store(self, key: str, value: Any, ttl: float) -> None:
        self._cache[key] = (time.monotonic() + ttl, value)

    # -- catalog reads -------------------------------------------------------

    async def categories(self, kind: str, locale: str) -> dict[str, Any]:
        """``MarketplaceCategoryList`` shape for one marketplace tab."""
        cache_key = f"categories:{kind}:{locale}"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(dict[str, Any], hit)
        payload = await self._get_json(
            "/api/v1/marketplace/categories", {"kind": kind, "locale": locale}
        )
        if not isinstance(payload, dict):
            raise MarketIndexUnavailableError("unexpected categories payload")
        self._store(cache_key, payload, _CATEGORIES_TTL)
        return payload

    async def list_items(
        self,
        *,
        type_: str,
        category: str | None = None,
        subcategory: str | None = None,
        source: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 30,
        locale: str,
    ) -> dict[str, Any]:
        """``MarketplaceItemList`` shape — one page of a normalized catalog."""
        params: dict[str, Any] = {
            "type": type_,
            "page": page,
            "page_size": page_size,
            "locale": locale,
        }
        if category is not None:
            params["category"] = category
        if subcategory is not None:
            params["subcategory"] = subcategory
        if source is not None:
            params["source"] = source
        if q:
            params["q"] = q
        cache_key = f"items:{sorted(params.items())}"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(dict[str, Any], hit)
        payload = await self._get_json("/api/v1/marketplace/items", params)
        if not isinstance(payload, dict):
            raise MarketIndexUnavailableError("unexpected items payload")
        self._store(cache_key, payload, _LIST_TTL)
        return payload

    async def item_detail(self, item_id: str, locale: str) -> dict[str, Any]:
        """``MarketplaceItemDetail`` shape, including the typed
        ``install_manifest`` the install pipeline consumes."""
        cache_key = f"detail:{item_id}:{locale}"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(dict[str, Any], hit)
        payload = await self._get_json(
            f"/api/v1/marketplace/items/{quote(item_id, safe='')}", {"locale": locale}
        )
        if not isinstance(payload, dict):
            raise MarketIndexUnavailableError("unexpected item detail payload")
        self._store(cache_key, payload, _DETAIL_TTL)
        return payload
