from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI


@pytest.mark.asyncio
async def test_lifespan_hooks_run_inside_oss_lifespan(monkeypatch) -> None:
    from valuz_agent.api import app as app_mod

    events: list[str] = []

    @asynccontextmanager
    async def fake_oss_lifespan(_: FastAPI) -> AsyncIterator[None]:
        events.append("oss:start")
        try:
            yield
        finally:
            events.append("oss:stop")

    @asynccontextmanager
    async def lifespan_hook(_: FastAPI) -> AsyncIterator[None]:
        events.append("extra:start")
        try:
            yield
        finally:
            events.append("extra:stop")

    monkeypatch.setattr(app_mod, "lifespan", fake_oss_lifespan)

    async with app_mod._build_lifespan([lifespan_hook])(FastAPI()):
        events.append("body")

    assert events == [
        "oss:start",
        "extra:start",
        "body",
        "extra:stop",
        "oss:stop",
    ]
