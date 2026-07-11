"""Async client for the Valuz market index — the PRIMARY marketplace data
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

Shape and caching strategy mirror the two clients this module once replaced
(``skillhub.py`` / ``modelscope.py``, now kept alongside it as the
direct-source fallback — see ``direct_fallback.py``): a thin httpx reader, a
short in-memory TTL cache (per-process, not a durable mirror), and one
"unavailable" exception collapsing every failure mode (transport error,
non-2xx, non-JSON body).

Base URL resolution
--------------------
When ``Settings.marketplace_index_base_url`` is left empty (the OSS default),
the client does not talk to a fixed host — it races
``Settings.marketplace_index_candidates`` (concurrent ``GET {candidate}
/healthz``, 2s timeout each) and pins the first candidate to answer 2xx as
the process-wide resolved base url (see :func:`resolve_index_base_url`). The
pin is cleared (triggering a re-race on the next request) after
``_MAX_CONSECUTIVE_FAILURES`` consecutive request failures against it, so a
candidate that goes down mid-process gets replaced without a restart. An
explicit ``base_url`` passed to the constructor (i.e. a non-empty
``Settings.marketplace_index_base_url``) always skips the race — it is used
verbatim for the client's lifetime.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, cast
from urllib.parse import quote

import httpx

from valuz_agent.infra.config import settings

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://cloud.valuz.dev"

_TIMEOUT_SECONDS = 15.0
_HEALTHZ_TIMEOUT_SECONDS = 2.0
_CATEGORIES_TTL = 600.0
_LIST_TTL = 60.0
_DETAIL_TTL = 300.0

# How many consecutive request failures against the pinned base url before
# the pin is dropped and the next request re-races the candidates.
_MAX_CONSECUTIVE_FAILURES = 3


class MarketIndexUnavailableError(Exception):
    """The market index could not be reached or returned an unusable payload."""


# ---------------------------------------------------------------------------
# Candidate racing — process-wide pinned base url resolution
# ---------------------------------------------------------------------------

_pin_lock = asyncio.Lock()
_pinned_base_url: str | None = None
_pinned_at: float | None = None


async def _probe_candidate(candidate: str, client: httpx.AsyncClient | None) -> str | None:
    """``GET {candidate}/healthz`` — returns the normalized candidate on a 2xx
    response, ``None`` on any transport error, timeout, or non-2xx status."""
    url = f"{candidate.rstrip('/')}/healthz"
    try:
        if client is not None:
            resp = await client.get(url, timeout=_HEALTHZ_TIMEOUT_SECONDS)
        else:
            async with httpx.AsyncClient(timeout=_HEALTHZ_TIMEOUT_SECONDS) as probe_client:
                resp = await probe_client.get(url)
    except httpx.HTTPError as exc:
        logger.debug("market index candidate %s failed healthz: %s", candidate, exc)
        return None
    if 200 <= resp.status_code < 300:
        return candidate.rstrip("/")
    return None


async def _race_candidates(candidates: list[str], client: httpx.AsyncClient | None) -> str:
    if not candidates:
        raise MarketIndexUnavailableError("no market index candidates configured")
    tasks = [asyncio.create_task(_probe_candidate(c, client)) for c in candidates]
    winner: str | None = None
    try:
        for done in asyncio.as_completed(tasks):
            result = await done
            if result is not None:
                winner = result
                break
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
    if winner is None:
        raise MarketIndexUnavailableError(f"no market index candidate reachable: {candidates}")
    return winner


async def resolve_index_base_url(
    candidates: list[str], *, client: httpx.AsyncClient | None = None
) -> str:
    """Resolve (and pin, process-wide) the market index base url from the
    first ``candidates`` entry whose ``/healthz`` answers 2xx. Cached until
    :func:`clear_pinned_base_url` runs (explicit reset, or automatically
    after ``_MAX_CONSECUTIVE_FAILURES`` consecutive request failures)."""
    global _pinned_base_url, _pinned_at
    if _pinned_base_url is not None:
        return _pinned_base_url
    async with _pin_lock:
        if _pinned_base_url is not None:  # re-check: another waiter may have won the race
            return _pinned_base_url
        winner = await _race_candidates(candidates, client)
        _pinned_base_url = winner
        _pinned_at = time.monotonic()
        logger.info("market index resolved to %s", winner)
        return winner


def clear_pinned_base_url() -> None:
    """Drop the process-wide pin so the next resolution re-races the
    candidates. Called automatically on repeated request failures; also
    exposed for tests that need a clean slate between cases."""
    global _pinned_base_url, _pinned_at
    _pinned_base_url = None
    _pinned_at = None


class MarketIndexClient:
    """Thin cached reader over the market index HTTP API."""

    def __init__(
        self,
        base_url: str | None = None,
        channel: str = "oss",
        client: httpx.AsyncClient | None = None,
        candidates: list[str] | None = None,
    ) -> None:
        # A non-empty explicit base url is pinned for this client's lifetime
        # and never races the candidates. ``None``/empty means "resolve
        # lazily" — see ``_resolve_base``.
        self._explicit_base_url = base_url.rstrip("/") if base_url else None
        self.channel = channel
        self._client = client  # injected in tests; None → one client per call
        if candidates is not None:
            self._candidates = list(candidates)
        else:
            self._candidates = list(settings.marketplace_index_candidates)
        self._consecutive_failures = 0
        self._cache: dict[str, tuple[float, Any]] = {}

    @property
    def base_url(self) -> str | None:
        """The pinned explicit base url, or ``None`` when this client
        resolves its base lazily via candidate racing."""
        return self._explicit_base_url

    # -- base url resolution -------------------------------------------------

    async def _resolve_base(self) -> str:
        if self._explicit_base_url:
            return self._explicit_base_url
        return await resolve_index_base_url(self._candidates, client=self._client)

    def _on_request_failure(self) -> None:
        if self._explicit_base_url:
            return  # fixed base — no pin to clear, no race to retrigger.
        self._consecutive_failures += 1
        if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            logger.warning(
                "market index base url failed %d consecutive requests — clearing pin",
                self._consecutive_failures,
            )
            clear_pinned_base_url()
            self._consecutive_failures = 0

    def _on_request_success(self) -> None:
        self._consecutive_failures = 0

    # -- low-level ---------------------------------------------------------

    async def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        base = await self._resolve_base()
        url = f"{base}{path}"
        query = {**params, "channel": self.channel}
        try:
            if self._client is not None:
                resp = await self._client.get(url, params=query)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                    resp = await client.get(url, params=query)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("market index request failed: %s %s: %s", path, query, exc)
            self._on_request_failure()
            raise MarketIndexUnavailableError(str(exc)) from exc
        except ValueError as exc:  # non-JSON body
            logger.warning("market index returned non-JSON for %s: %s", path, exc)
            self._on_request_failure()
            raise MarketIndexUnavailableError("invalid JSON from market index") from exc
        self._on_request_success()
        return payload

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
