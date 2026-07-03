"""Active-sandbox registry — the host's grip on a running kernel sandbox.

One question, asked at every session creation against a sandboxed kernel:
*"is this project's cwd reachable inside the sandbox, and if not, make it
so."* This is the ② dynamic-mount face wired into the live path.

Most cwds need nothing: managed chat dirs and projects under ``~/Valuz``
sit beneath the static write-mounts baked into the provision profile, so
they are already reachable. The case this handles is the one a single
host-wide sandbox otherwise can't: a project bound to an arbitrary external
folder, created/bound AFTER the kernel is already serving. For those,
``ensure_workspace_granted`` issues a macOS sandbox-extension token and has
the running kernel consume it — no restart, no copy (see
``integrations/sandbox_seatbelt.py`` and ``kernel/app/sandbox_control.py``).

``kernel_cwd`` is the cloud seam: locally it equals the input cwd (host and
sandbox share a filesystem); a future cloud provider returns the staged
in-sandbox path. ``HttpKernelClient.create_session`` always uses the
returned value, so the same call site is correct for both.

Activation is **lazy from env** so it is robust to however uvicorn ends up
splitting provision vs. serve across processes under ``--reload``: any host
process that has ``VALUZ_SANDBOX_DRIVER=seatbelt`` + ``VALUZ_KERNEL_URL``
self-activates on first use. With no sandbox (in-process mode) every call
is a no-op returning the cwd unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from valuz_agent.ports.sandbox_provider import MountGrant, SandboxProvider

logger = logging.getLogger("valuz_agent.sandbox")


@dataclass
class _State:
    provider: SandboxProvider
    sandbox_id: str
    static_roots: tuple[str, ...]
    """Realpath roots already covered by the provision-time write mounts —
    a cwd under any of these needs no dynamic grant."""
    granted: dict[str, MountGrant] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_state: _State | None = None
_lazy_tried = False


def activate(provider: SandboxProvider, sandbox_id: str, static_roots: tuple[str, ...]) -> None:
    """Eagerly register the active sandbox (called by the provisioner).

    Optional — ``ensure_workspace_granted`` lazily activates from env if
    this was never called in the serving process.
    """
    global _state
    _state = _State(
        provider=provider,
        sandbox_id=sandbox_id,
        static_roots=tuple(os.path.realpath(r) for r in static_roots),
    )


def _lazy_activate() -> _State | None:
    """Build state from env on first use, if a sandbox is configured.

    Driver-agnostic: resolves the active driver from the registry and re-attaches
    to the already-running sandbox the provisioning process published via env.
    Returns None (→ no-op) when no driver is set or no sandbox is up.
    """
    global _state, _lazy_tried
    if _state is not None:
        return _state
    if _lazy_tried:
        return None
    _lazy_tried = True

    from valuz_agent.integrations import sandbox_registry
    from valuz_agent.ports.sandbox_provider import SandboxBootContext, SandboxEndpoint

    driver = sandbox_registry.get(os.getenv("VALUZ_SANDBOX_DRIVER"))
    base_url = os.getenv("VALUZ_KERNEL_URL")
    if driver is None or not base_url:
        return None

    ctx = SandboxBootContext(
        host="127.0.0.1",
        port=0,
        host_callback_url=os.getenv("VALUZ_BACKEND_BASE_URL", ""),
    )
    endpoint = SandboxEndpoint(
        sandbox_id="host-kernel", base_url=base_url, token=os.getenv("VALUZ_KERNEL_TOKEN", "")
    )
    result = driver.attach(ctx, endpoint)
    _state = _State(
        provider=result.provider,
        sandbox_id="host-kernel",
        static_roots=tuple(os.path.realpath(r) for r in result.static_roots),
    )
    return _state


def is_active() -> bool:
    return _lazy_activate() is not None


def _under_static_root(real: str, roots: tuple[str, ...]) -> bool:
    for root in roots:
        if real == root or real.startswith(root + os.sep):
            return True
    return False


async def ensure_workspace_granted(cwd: str, *, owner_user_id: str = "") -> str:
    """Ensure ``cwd`` is reachable inside the running sandbox; return the
    cwd the kernel should use (unchanged locally).

    No-op (returns ``cwd``) when there is no sandbox, when ``cwd`` already
    sits under a static mount, or when it was granted earlier. Otherwise
    issues+consumes a sandbox extension for it. Best-effort: a grant failure
    is swallowed and the original cwd returned, so a misconfiguration
    degrades to the pre-extension behaviour ("Operation not permitted" at
    the agent, surfaced there) rather than blocking session creation.

    ``owner_user_id`` is the authenticated principal, threaded explicitly to
    ``bind_workspace`` so a cloud driver stages the files under **that owner's**
    object-store subtree (multi-tenant isolation). Empty on the local
    single-user path. Never read from ambient context.
    """
    state = _lazy_activate()
    if state is None:
        logger.debug("ensure_workspace_granted(%s): no active sandbox — no-op", cwd)
        return cwd
    real = os.path.realpath(str(Path(cwd).expanduser()))
    if _under_static_root(real, state.static_roots):
        logger.info("workspace %s is under a static rw mount — no grant needed", real)
        return cwd
    async with state.lock:
        existing = state.granted.get(real)
        if existing is not None:
            logger.info("workspace %s already granted to this sandbox", real)
            return existing.kernel_cwd
        try:
            binding = await state.provider.bind_workspace(
                state.sandbox_id, real, "rw", owner_user_id=owner_user_id
            )
        except Exception:  # noqa: BLE001 — degrade, don't block session creation
            logger.warning(
                "dynamic workspace grant FAILED for %s — agent may hit Operation not permitted",
                real,
                exc_info=True,
            )
            return cwd
        state.granted[real] = binding
        logger.info(
            "dynamic workspace grant issued for %s (kernel_cwd=%s)", real, binding.kernel_cwd
        )
        return binding.kernel_cwd
