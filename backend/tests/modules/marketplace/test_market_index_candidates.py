"""Market index base-url candidate racing.

Covers ``resolve_index_base_url`` / ``MarketIndexClient``'s lazy-base mode:
concurrent ``GET {candidate}/healthz`` racing, once-per-process pinning
(the outcome — winner or nothing-reachable — is final for the process
lifetime; no per-request re-probing), and skipping the race entirely when an
explicit base url is configured. No real network — every case runs over an
``httpx.MockTransport``.
"""

from __future__ import annotations

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
async def test_race_runs_once_and_winner_is_final() -> None:
    """The race runs exactly once per process: later requests reuse the
    pinned winner without any further healthz probing — even when requests
    against it keep failing."""
    probes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = f"{request.url.scheme}://{request.url.host}"
        if request.url.path == "/healthz":
            probes.append(host)
            return httpx.Response(200 if host == GOOD else 503, json={})
        # The pinned candidate's API keeps failing (e.g. route not deployed):
        # this must NOT trigger a re-race.
        return httpx.Response(500, json={"error": "boom"})

    client, _ = _client_for(handler, [BAD, GOOD])

    for locale in ("en-US", "fr-FR", "de-DE", "ja-JP"):
        with pytest.raises(MarketIndexUnavailableError):
            await client.categories("skill", locale)
        assert market_index._pinned_base_url == GOOD  # noqa: SLF001

    assert sorted(set(probes)) == sorted({BAD, GOOD})
    assert len(probes) == 2  # one probe per candidate, ever


@pytest.mark.asyncio
async def test_failed_race_outcome_is_sticky_and_fails_fast() -> None:
    """A total race failure is final for the process: later requests raise
    immediately without probing again."""
    probes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            probes.append(f"{request.url.scheme}://{request.url.host}")
        return httpx.Response(503, json={"ok": False})

    client, _ = _client_for(handler, [BAD, GOOD])

    with pytest.raises(MarketIndexUnavailableError):
        await client.categories("skill", "en-US")
    first_round = len(probes)
    assert first_round == 2

    with pytest.raises(MarketIndexUnavailableError):
        await client.categories("skill", "fr-FR")
    with pytest.raises(MarketIndexUnavailableError):
        await client.categories("skill", "de-DE")
    assert len(probes) == first_round  # no re-probing, ever


@pytest.mark.asyncio
async def test_resolve_in_background_pins_at_startup(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The boot hook races once in the background; a no-op with an explicit
    base url configured."""
    import asyncio

    from valuz_agent.infra.config import settings

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            host = f"{request.url.scheme}://{request.url.host}"
            return httpx.Response(200 if host == GOOD else 503, json={})
        return httpx.Response(200, json={})

    monkeypatch.setattr(settings, "marketplace_index_base_url", "https://pinned.example")
    assert market_index.resolve_index_in_background() is None

    monkeypatch.setattr(settings, "marketplace_index_base_url", "")
    monkeypatch.setattr(settings, "marketplace_index_candidates", [BAD, GOOD])
    transport = httpx.MockTransport(handler)

    real_resolve = market_index.resolve_index_base_url

    async def resolve_with_mock_transport(candidates, *, client=None):  # type: ignore[no-untyped-def]
        async with httpx.AsyncClient(transport=transport) as mock_client:
            return await real_resolve(candidates, client=mock_client)

    monkeypatch.setattr(market_index, "resolve_index_base_url", resolve_with_mock_transport)
    task = market_index.resolve_index_in_background()
    assert task is not None
    await asyncio.wait_for(task, timeout=2)
    assert market_index._pinned_base_url == GOOD  # noqa: SLF001
