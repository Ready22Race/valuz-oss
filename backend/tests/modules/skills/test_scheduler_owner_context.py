"""Skill auto-scan scheduler owner handling.

The scheduler runs in a daemon thread with no inherited request context. It must
derive an explicit job owner instead of reading ``get_current_user_id_optional``.
"""

from __future__ import annotations

import pytest

from valuz_agent.infra import auth_context
from valuz_agent.infra.config import settings
from valuz_agent.modules.skills import scheduler as sched


class _Svc:
    def __init__(self) -> None:
        self.scanned: list[str] = []

    async def startup_scan(self, owner: str) -> int:
        self.scanned.append(owner)
        return 3


def _skill_service_gen(svc: _Svc):  # type: ignore[no-untyped-def]
    async def _gen():
        yield svc

    return _gen()


@pytest.mark.asyncio
async def test_auto_scan_uses_explicit_local_owner(monkeypatch) -> None:
    monkeypatch.setattr(settings, "deployment_type", "local")
    monkeypatch.setattr(
        "valuz_agent.infra.local_identity.resolve_local_user_id",
        lambda: "local-owner",
    )
    svc = _Svc()
    monkeypatch.setattr("valuz_agent.api.deps.get_skill_service", lambda: _skill_service_gen(svc))

    # Poison the ambient request context. The scheduler must ignore it.
    token = auth_context.set_current_user_id("wrong-request-owner")
    try:
        await sched._arun_skill_scan()
    finally:
        auth_context.reset_current_user_id(token)

    assert svc.scanned == ["local-owner"]


@pytest.mark.asyncio
async def test_auto_scan_skips_non_local_deployments(monkeypatch) -> None:
    monkeypatch.setattr(settings, "deployment_type", "cloud")
    called = False

    def _get_skill_service():  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        return _skill_service_gen(_Svc())

    monkeypatch.setattr("valuz_agent.api.deps.get_skill_service", _get_skill_service)

    await sched._arun_skill_scan()

    assert called is False
