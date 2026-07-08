"""Port: per-user sandbox allocation — which kernel serves this owner.

The ② control face today binds ONE process-wide kernel client (in-process, or
the single boot-provisioned sandbox). A shared multi-tenant host must instead
resolve a kernel **per ``owner_user_id``** (one sandbox per user; that user's
projects are cwds within it — see ``docs/design/sandbox-fleet-seam.md``).

This port is the seam. ``kernel_client._kernel_for(user_id)`` calls
``ext.sandbox_allocator.ensure(owner_user_id=...)`` to get a lease, then talks to
that lease's endpoint. The OSS default (``BootSingletonAllocator``) returns a
lease with ``endpoint=None`` meaning "use the process/global kernel client" — so
local single-user behavior is **unchanged**. A commercial overlay binds an
allocator that provisions/reuses one sandbox per user and returns its endpoint.

Only EXECUTION / LIVE ops route through here (create_session, run_turn,
interrupt, submit_action, emit_live_event, subscribe_session_events). STORE
reads/writes go to the host durable data service, not a per-user kernel.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from valuz_agent.ports.sandbox_provider import SandboxEndpoint


@dataclass(frozen=True)
class SandboxLease:
    """The kernel a given owner's execution should use.

    ``endpoint=None`` is the sentinel for "use the host's process/global kernel
    client" (in-process kernel, or the single boot-attached sandbox) — the OSS
    default. A non-None endpoint is a per-user kernel the caller reaches over
    HTTP (``HttpKernelClient(endpoint.base_url, token=endpoint.token)``).
    """

    endpoint: SandboxEndpoint | None = None


class SandboxAllocatorPort(ABC):
    """Resolve / release the kernel that serves ``owner_user_id``."""

    @abstractmethod
    async def ensure(self, *, owner_user_id: str) -> SandboxLease:
        """Return the running kernel lease for ``owner_user_id`` (provision or
        reuse). ``owner_user_id`` is the authenticated principal, threaded
        explicitly — never ambient."""
        ...

    @abstractmethod
    async def release(self, *, owner_user_id: str) -> None:
        """Best-effort teardown for ``owner_user_id`` (idle TTL). Idempotent."""
        ...

    @abstractmethod
    async def peek(self, *, owner_user_id: str) -> SandboxLease | None:
        """Return the owner's CURRENT lease **without provisioning**; ``None`` if
        the owner has no live kernel.

        For GLOBAL-LIVE taps (the decision inbox): opening the inbox must never
        spin up a sandbox. ``ensure`` provisions; ``peek`` only reveals what's
        already running.
        """
        ...


class BootSingletonAllocator(SandboxAllocatorPort):
    """Default OSS allocator: every owner shares the one process/boot kernel.

    ``ensure`` returns ``SandboxLease(endpoint=None)`` for everyone → the facade
    uses the process-global kernel client. This preserves the current single
    in-process / single boot-sandbox behavior exactly; a shared multi-tenant host
    replaces it via ``ext.sandbox_allocator``.
    """

    async def ensure(self, *, owner_user_id: str) -> SandboxLease:
        return SandboxLease(endpoint=None)

    async def release(self, *, owner_user_id: str) -> None:
        return None

    async def peek(self, *, owner_user_id: str) -> SandboxLease | None:
        # The boot / in-process kernel always exists → route to the global
        # client (``endpoint=None``), same as ``ensure``.
        return SandboxLease(endpoint=None)


__all__ = ["BootSingletonAllocator", "SandboxAllocatorPort", "SandboxLease"]
