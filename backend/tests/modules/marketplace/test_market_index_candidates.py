"""Market index base-url candidate racing.

Covers ``resolve_index_base_url`` / ``MarketIndexClient``'s lazy-base mode:
concurrent ``GET {candidate}/healthz`` racing, process-wide pinning, skipping
the race entirely when an explicit base url is configured, and re-racing
after repeated request failures against the pinned candidate. No real
network — every case runs over an ``httpx.MockTransport``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from valuz_agent.modules.marketplace import market_index
from valuz_agent.modules.marketplace.market_index import (
    MarketIndexClient,
    MarketIndexUnavailableError,
    clear_pinned_base_url,
)

GOOD = "https://good.example"
BAD = "https://bad.example"
SLOW_GOOD = "https://slow-good.example"


@pytest.fixture(autouse=True)
def _reset_pin():  # type: ignore[no-untyped-def]
    clear_pinned_base_url()
    yield
    clear_pinned_base_url()


def _client_for(handler, candidates: list[str]) -> tuple[MarketIndexClient, httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport)
    client = MarketIndexClient(None, "oss", client=async_client, candidates=candidates)
    return client, async_client


@pytest.mark.asyncio
async def test_races_candidates_and_pins_first_2xx_healthz() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = f"{request.url.scheme}://{request.url.host}"
        calls.append(f"{host}{request.url.path}")
        if request.url.path == "/healthz":
            if host == GOOD:
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(503, json={"ok": False})
        return httpx.Response(200, json={"categories": [], "degraded": False})

    client, _ = _client_for(handler, [BAD, GOOD])
    payload = await client.categories("skill", "en-US")

    assert payload == {"categories": [], "degraded": False}
    # Both candidates got probed; the pin resolved to the one answering 2xx,
    # and the actual categories request went to it (not the bad one).
    assert f"{BAD}/healthz" in calls
    assert f"{GOOD}/healthz" in calls
    assert f"{GOOD}/api/v1/marketplace/categories" in calls
    assert f"{BAD}/api/v1/marketplace/categories" not in calls


@pytest.mark.asyncio
async def test_race_picks_first_success_even_when_slower() -> None:
    """A candidate racing scheme picks the first to *answer 2xx*, not simply
    the first to respond — a fast-failing candidate must not win over a
    slower-but-healthy one."""
    import asyncio

    async def handler(request: httpx.Request) -> httpx.Response:
        host = f"{request.url.scheme}://{request.url.host}"
        if request.url.path == "/healthz":
            if host == BAD:
                return httpx.Response(503, json={"ok": False})  # fails fast
            await asyncio.sleep(0.05)  # slow but healthy
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"categories": [], "degraded": False})

    client, _ = _client_for(handler, [BAD, SLOW_GOOD])
    await client.categories("skill", "en-US")
    assert client.base_url is None  # lazy client never pins its OWN base_url attr
    assert market_index._pinned_base_url == SLOW_GOOD  # noqa: SLF001


@pytest.mark.asyncio
async def test_all_candidates_unreachable_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"ok": False})

    client, _ = _client_for(handler, [BAD, GOOD])
    with pytest.raises(MarketIndexUnavailableError):
        await client.categories("skill", "en-US")


@pytest.mark.asyncio
async def test_all_candidates_transport_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client, _ = _client_for(handler, [BAD, GOOD])
    with pytest.raises(MarketIndexUnavailableError):
        await client.categories("skill", "en-US")


@pytest.mark.asyncio
async def test_explicit_base_url_skips_candidate_race() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"categories": [], "degraded": False})

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport)
    # Explicit non-empty base_url — candidates are irrelevant and must never
    # be probed.
    client = MarketIndexClient(
        "https://pinned.example", "oss", client=async_client, candidates=[BAD, GOOD]
    )
    await client.categories("skill", "en-US")

    assert client.base_url == "https://pinned.example"
    assert "/healthz" not in calls
    assert calls == ["/api/v1/marketplace/categories"]


@pytest.mark.asyncio
async def test_consecutive_failures_clear_pin_and_retrigger_race() -> None:
    """After N consecutive request failures against the pinned candidate, the
    next request re-races — and can land on a different winner if the
    previous one has since gone unhealthy."""
    state: dict[str, Any] = {"phase": "first-race", "categories_calls_to_good": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        host = f"{request.url.scheme}://{request.url.host}"
        if request.url.path == "/healthz":
            if state["phase"] == "first-race":
                # Only GOOD is healthy the first time around.
                return httpx.Response(200 if host == GOOD else 503, json={})
            # Second race: GOOD has since gone down, BAD (renamed reality:
            # now healthy) answers instead.
            return httpx.Response(200 if host == BAD else 503, json={})
        # Real categories endpoint: GOOD always 500s once pinned (simulating
        # its API — not its healthz — failing), BAD always succeeds.
        if host == GOOD:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"categories": [], "degraded": False})

    client, _ = _client_for(handler, [BAD, GOOD])

    # First race pins GOOD (only healthy candidate at this point).
    with pytest.raises(MarketIndexUnavailableError):
        await client.categories("skill", "en-US")
    assert market_index._pinned_base_url == GOOD  # noqa: SLF001

    # 2 more consecutive failures (3 total) should clear the pin.
    with pytest.raises(MarketIndexUnavailableError):
        await client.categories("skill", "fr-FR")
    assert market_index._pinned_base_url == GOOD  # noqa: SLF001
    state["phase"] = "second-race"
    with pytest.raises(MarketIndexUnavailableError):
        await client.categories("skill", "de-DE")
    assert market_index._pinned_base_url is None  # noqa: SLF001

    # Next request re-races; BAD is now the only healthy candidate and its
    # categories endpoint actually succeeds.
    payload = await client.categories("skill", "ja-JP")
    assert payload == {"categories": [], "degraded": False}
    assert market_index._pinned_base_url == BAD  # noqa: SLF001
