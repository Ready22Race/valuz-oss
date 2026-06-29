"""``_retry_async`` backs off and retries only matching errors.

Backs the connector-probe self-heal: a no-auth connector that answers a
transient 401 (e.g. Firecrawl's anonymous rate limit) is retried a couple
times, so the auto-probe matches what a manual reconnect does. Non-matching
errors and exhausted retries propagate.
"""

from __future__ import annotations

import httpx
import pytest

# Side-effect import — kernel ``app``/``src`` on sys.path before connectors
# (transitively) reaches the mcp resolver.
import valuz_agent.boot.kernel  # noqa: F401,E402
from valuz_agent.api.routes.connectors import _is_unauthorized, _retry_async


def _http_401() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://x/mcp")
    resp = httpx.Response(401, request=req)
    return httpx.HTTPStatusError("401", request=req, response=resp)


async def test_succeeds_after_transient_401() -> None:
    calls = {"n": 0}

    async def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_401()
        return "ok"

    out = await _retry_async(fn, retry_if=_is_unauthorized, delays=(0.0, 0.0))
    assert out == "ok"
    assert calls["n"] == 3  # failed twice, succeeded on the third


async def test_reraises_after_retries_exhausted() -> None:
    calls = {"n": 0}

    async def fn() -> str:
        calls["n"] += 1
        raise _http_401()

    with pytest.raises(httpx.HTTPStatusError):
        await _retry_async(fn, retry_if=_is_unauthorized, delays=(0.0, 0.0))
    assert calls["n"] == 3  # initial + 2 retries


async def test_does_not_retry_non_matching_error() -> None:
    calls = {"n": 0}

    async def fn() -> str:
        calls["n"] += 1
        raise RuntimeError("connection refused")

    with pytest.raises(RuntimeError):
        await _retry_async(fn, retry_if=_is_unauthorized, delays=(0.0, 0.0))
    assert calls["n"] == 1  # no retry on a non-401


async def test_returns_immediately_on_success() -> None:
    calls = {"n": 0}

    async def fn() -> str:
        calls["n"] += 1
        return "ok"

    assert await _retry_async(fn, retry_if=_is_unauthorized, delays=(0.0, 0.0)) == "ok"
    assert calls["n"] == 1
