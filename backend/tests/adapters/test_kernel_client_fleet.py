"""Tests for the per-user kernel resolution seam (fleet PR-5 S1).

Default `BootSingletonAllocator` → `_kernel_for` returns the process-global
`client` (behavior unchanged); a per-user endpoint → a cached HttpKernelClient.
EXEC facade methods route through `_kernel_for`.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401  (sys.path bootstrap)
from valuz_agent.adapters import kernel_client as kc
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.sandbox_allocator import BootSingletonAllocator, SandboxLease
from valuz_agent.ports.sandbox_provider import SandboxEndpoint


class _FakeAllocator:
    def __init__(self, lease: SandboxLease) -> None:
        self._lease = lease
        self.calls: list[str] = []

    async def ensure(self, *, owner_user_id: str) -> SandboxLease:
        self.calls.append(owner_user_id)
        return self._lease

    async def release(self, *, owner_user_id: str) -> None:
        return None


async def test_kernel_for_default_singleton_returns_global(monkeypatch) -> None:
    monkeypatch.setattr(ext, "sandbox_allocator", BootSingletonAllocator())
    assert await kc._kernel_for("u1") is kc.client  # unchanged behavior


async def test_kernel_for_endpoint_none_returns_global(monkeypatch) -> None:
    monkeypatch.setattr(ext, "sandbox_allocator", _FakeAllocator(SandboxLease(endpoint=None)))
    assert await kc._kernel_for("u1") is kc.client


async def test_kernel_for_caches_per_endpoint(monkeypatch) -> None:
    from valuz_agent.adapters.kernel_client_http import HttpKernelClient

    ep = SandboxEndpoint(sandbox_id="s", base_url="https://u1.pool", token="t")
    monkeypatch.setattr(ext, "sandbox_allocator", _FakeAllocator(SandboxLease(endpoint=ep)))
    monkeypatch.setattr(kc, "_endpoint_clients", {})
    c1 = await kc._kernel_for("u1")
    c2 = await kc._kernel_for("u1")
    assert isinstance(c1, HttpKernelClient) and c1 is c2  # one client per endpoint URL


async def test_two_owners_two_endpoints(monkeypatch) -> None:
    monkeypatch.setattr(kc, "_endpoint_clients", {})

    class _PerOwner:
        async def ensure(self, *, owner_user_id: str) -> SandboxLease:
            return SandboxLease(
                endpoint=SandboxEndpoint(
                    sandbox_id=owner_user_id, base_url=f"https://{owner_user_id}.pool", token="t"
                )
            )

        async def release(self, *, owner_user_id: str) -> None:
            return None

    monkeypatch.setattr(ext, "sandbox_allocator", _PerOwner())
    a = await kc._kernel_for("A")
    b = await kc._kernel_for("B")
    assert a is not b  # different owners → different kernels


async def test_create_session_routes_through_kernel_for(monkeypatch) -> None:
    seen: dict[str, str] = {}

    class _FakeClient:
        async def create_session(self, user_id: str, req: object) -> str:
            seen["user"] = user_id
            return "SESSION"

    monkeypatch.setattr(ext, "sandbox_allocator", BootSingletonAllocator())
    monkeypatch.setattr(kc, "client", _FakeClient())
    out = await kc.create_session("u9", object())
    assert out == "SESSION" and seen["user"] == "u9"


async def test_subscribe_session_events_is_async_generator(monkeypatch) -> None:
    class _FakeClient:
        def subscribe_session_events(self, user_id: str, session_id: str):
            async def _gen():
                yield {"e": 1}
                yield {"e": 2}

            return _gen()

    monkeypatch.setattr(ext, "sandbox_allocator", BootSingletonAllocator())
    monkeypatch.setattr(kc, "client", _FakeClient())
    got = [ev async for ev in kc.subscribe_session_events("u", "s")]
    assert got == [{"e": 1}, {"e": 2}]
