"""Port: authorize kernel-sandbox provisioning.

The ① supply face (``SandboxProvider.provision`` / ``SandboxDriver.
provision_for_boot``) answers *"give me a running kernel"*. This port
answers the question that must come **first** on a shared multi-tenant
host: *"is this owner even allowed to bring up (another) sandbox right
now?"* — plan entitlement + per-org concurrency caps. It is deliberately
separate from ``BillingPort``: gating is not metering, and a host may cap
concurrency even when nothing is charged (see the commercial ADR-012).

OSS mode uses ``AllowAllSandboxPolicy`` — every provision is permitted
(single-user desktop, no governance), so the local path is unchanged. The
commercial overlay binds a real policy via ``set_sandbox_policy()`` at app
startup.

**Semantics: fail-closed.** Provisioning creates a limited/chargeable
resource, so the host refuses when the bound policy denies *or errors* —
use ``authorize_sandbox_provision`` (below), which converts an exception
into a deny. This is the opposite of ``BillingPort.check_budget``'s
fail-open bias, and intentional.

**Owner-scoped, never ambient.** ``authorize_provision`` receives the
resolved ``owner_user_id`` (the authenticated principal, threaded
explicitly by the caller) plus the ``project_id`` the sandbox will serve.
OSS provisions a single host-wide sandbox at boot with an empty owner, so
the default policy passes; the per-owner, on-demand provisioning path this
gate is built for is the commercial fleet (see
``docs/design/kernel-sandbox-deployment.md`` §3.10).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("valuz_agent.sandbox")


@dataclass
class SandboxProvisionContext:
    """Inputs the policy uses to decide whether a provision may proceed.

    ``owner_user_id`` is the authenticated principal the sandbox is being
    brought up for (empty on the OSS single-user boot path). ``project_id``
    is the project whose cwd the sandbox will serve, when known.
    """

    owner_user_id: str
    project_id: str = ""


@dataclass
class SandboxDecision:
    allowed: bool
    reason: str | None = None
    # Optional i18n key + params an overlay can attach so the client renders a
    # localized message (e.g. "cloud sandbox not in your plan") instead of the
    # raw ``reason``. OSS leaves these None; the default policy never sets them.
    message_key: str | None = None
    message_params: dict[str, Any] | None = None


class SandboxPolicyPort(ABC):
    """Gate kernel-sandbox provisioning. Called BEFORE the supply face."""

    @abstractmethod
    async def authorize_provision(self, ctx: SandboxProvisionContext) -> SandboxDecision:
        """Return a decision for the attempted sandbox provision."""
        ...


class AllowAllSandboxPolicy(SandboxPolicyPort):
    """Default policy — every provision is permitted (OSS single-user)."""

    async def authorize_provision(self, ctx: SandboxProvisionContext) -> SandboxDecision:
        return SandboxDecision(allowed=True)


def get_sandbox_policy() -> SandboxPolicyPort:
    from valuz_agent.ports.extensions import ext

    return ext.sandbox_policy


def set_sandbox_policy(policy: SandboxPolicyPort) -> None:
    """Replace the sandbox policy (called by the commercial app at startup)."""
    from valuz_agent.ports.extensions import ext

    ext.sandbox_policy = policy


async def authorize_sandbox_provision(owner_user_id: str, project_id: str = "") -> SandboxDecision:
    """Fail-closed wrapper the host calls before provisioning a sandbox.

    Reads the bound policy and evaluates it; **any exception is converted to
    a deny** (the policy backend being unreachable must not silently open the
    gate). Returns the policy's ``SandboxDecision`` on success.
    """
    from valuz_agent.ports.extensions import ext

    ctx = SandboxProvisionContext(owner_user_id=owner_user_id, project_id=project_id)
    try:
        return await ext.sandbox_policy.authorize_provision(ctx)
    except Exception:  # noqa: BLE001 — fail-closed: an errored policy denies
        logger.warning(
            "sandbox policy raised while authorizing provision for owner=%r "
            "project=%r — denying (fail-closed)",
            owner_user_id,
            project_id,
            exc_info=True,
        )
        return SandboxDecision(allowed=False, reason="sandbox authorization policy unavailable")


__all__ = [
    "AllowAllSandboxPolicy",
    "SandboxDecision",
    "SandboxPolicyPort",
    "SandboxProvisionContext",
    "authorize_sandbox_provision",
    "get_sandbox_policy",
    "set_sandbox_policy",
]
