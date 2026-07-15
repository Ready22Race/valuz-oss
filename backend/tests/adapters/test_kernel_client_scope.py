"""Sandbox-scope routing through the kernel_client facade (scoped allocation).

Pins the on-demand start/stop seam:

- EXEC ops derive ``session:{session_id}`` scope by default and hand it to a
  scope-aware allocator;
- an explicit creation scope (tasks) seeds the session→scope cache so later
  ops on the same session route to the SAME scope;
- the bound resolver maps task sessions to ``task:{task_id}``;
- pre-scope allocators (no ``scope`` kwarg) keep working untouched;
- ``subscribe_session_events_existing`` / ``emit_live_event`` NEVER provision.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (sys.path bootstrap)
from valuz_agent.adapters import kernel_client as kc
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.sandbox_allocator import SandboxLease, SandboxScope
from valuz_agent.ports.sandbox_provider import SandboxEndpoint


@pytest.fixture(autouse=True)
def _clean_scope_state(monkeypatch):
    monkeypatch.setattr(kc, "_scope_cache", {})
    monkeypatch.setattr(kc, "_scope_resolver", None)
    monkeypatch.setattr(kc, "_endpoint_clients", {})


class _ScopedAllocator:
    """Records every (owner, scope) it is asked for; one endpoint per scope key."""

    def __init__(self) -> None:
        self.ensured: list[tuple[str, SandboxScope | None]] = []
        self.new_turns: list[bool] = []
        self.peeked: list[tuple[str, SandboxScope | None]] = []
        self.live: bool = True

    async def ensure(
        self, *, owner_user_id: str, scope: SandboxScope | None = None, new_turn: bool = False
    ) -> SandboxLease:
        self.ensured.append((owner_user_id, scope))
        self.new_turns.append(new_turn)
        key = scope.key if scope else "owner"
        return SandboxLease(
            endpoint=SandboxEndpoint(sandbox_id=key, base_url=f"https://{key}.pool", token="t")
        )

    async def peek(
        self, *, owner_user_id: str, scope: SandboxScope | None = None
    ) -> SandboxLease | None:
        self.peeked.append((owner_user_id, scope))
        if not self.live:
            return None
        key = scope.key if scope else "owner"
        return SandboxLease(
            endpoint=SandboxEndpoint(sandbox_id=key, base_url=f"https://{key}.pool", token="t")
        )

    async def release(self, *, owner_user_id: str, scope: SandboxScope | None = None) -> None:
        return None


async def test_exec_ops_default_to_session_scope(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)

    class _FakeClient:
        async def run_turn(self, *a, **k):  # noqa: ANN002, ANN003
            return "MSG"

    monkeypatch.setattr(kc, "_endpoint_clients", {"https://session:s1.pool": _FakeClient()})
    await kc.run_turn("u1", "s1", "hi")
    assert alloc.ensured == [("u1", SandboxScope(kind="session", id="s1"))]
    # run_turn signals a fresh conversation turn to the allocator.
    assert alloc.new_turns == [True]


async def test_non_turn_ops_do_not_set_new_turn(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)

    class _FakeClient:
        async def submit_action(self, *a, **k):  # noqa: ANN002, ANN003
            return {}

    monkeypatch.setattr(kc, "_endpoint_clients", {"https://session:s1.pool": _FakeClient()})
    await kc.submit_action("u1", "s1", object())
    assert alloc.new_turns == [False]  # mid-turn op reuses the current instance


async def test_explicit_create_scope_seeds_cache_for_later_ops(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)
    task_scope = SandboxScope(kind="task", id="t42")

    class _FakeClient:
        async def create_session(self, user_id, req):  # noqa: ANN001
            return "SESSION"

        async def run_turn(self, *a, **k):  # noqa: ANN002, ANN003
            return "MSG"

    monkeypatch.setattr(kc, "_endpoint_clients", {"https://task:t42.pool": _FakeClient()})

    class _Req:
        id = "lead-1"

    await kc.create_session("u1", _Req(), scope=task_scope)
    await kc.run_turn("u1", "lead-1", "go")  # later op, no explicit scope
    assert [s for _, s in alloc.ensured] == [task_scope, task_scope]


async def test_resolver_maps_task_sessions(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)

    async def _resolver(user_id: str, session_id: str) -> SandboxScope | None:
        return SandboxScope(kind="task", id="t7") if session_id == "member-1" else None

    kc.bind_sandbox_scope_resolver(_resolver)

    class _FakeClient:
        async def interrupt(self, *a, **k):  # noqa: ANN002, ANN003
            return None

    monkeypatch.setattr(
        kc,
        "_endpoint_clients",
        {"https://task:t7.pool": _FakeClient(), "https://session:chat-1.pool": _FakeClient()},
    )
    await kc.interrupt("u1", "member-1")
    await kc.interrupt("u1", "chat-1")
    assert [s for _, s in alloc.ensured] == [
        SandboxScope(kind="task", id="t7"),
        SandboxScope(kind="session", id="chat-1"),
    ]


async def test_prescope_allocator_still_works(monkeypatch) -> None:
    """An allocator written against the pre-scope port signature is never
    handed a scope kwarg — additive contract (ADR-001 spirit)."""

    calls: list[str] = []

    class _Legacy:
        async def ensure(self, *, owner_user_id: str) -> SandboxLease:
            calls.append(owner_user_id)
            return SandboxLease(endpoint=None)

        async def release(self, *, owner_user_id: str) -> None:
            return None

    monkeypatch.setattr(ext, "sandbox_allocator", _Legacy())
    assert await kc._kernel_for("u1", SandboxScope(kind="session", id="s1")) is kc.client
    assert calls == ["u1"]


async def test_subscribe_existing_never_provisions(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    alloc.live = False  # no live kernel for any scope
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)

    frames = [f async for f in kc.subscribe_session_events_existing("u1", "s1")]
    assert frames == []
    assert alloc.ensured == []  # peek-only: opening history never provisions
    assert alloc.peeked == [("u1", SandboxScope(kind="session", id="s1"))]


async def test_emit_live_event_noops_without_live_kernel(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    alloc.live = False
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)

    await kc.emit_live_event("u1", "s1", "todo_update", {"todos": []})
    assert alloc.ensured == []  # never provisions just to broadcast a live frame
