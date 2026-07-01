"""Tests for the sandbox provisioning policy seam (PR-C).

Proves: the OSS default is allow-all (local single-user path unchanged); a
bound policy can deny; ``authorize_sandbox_provision`` is **fail-closed** (an
errored policy denies, unlike ``BillingPort.check_budget``'s fail-open bias);
``get/set`` round-trip; and ``owner_user_id`` threads through the provision
contract (PR-B).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

from collections.abc import Iterator

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.sandbox_policy import (
    AllowAllSandboxPolicy,
    SandboxDecision,
    SandboxProvisionContext,
    authorize_sandbox_provision,
    get_sandbox_policy,
    set_sandbox_policy,
)
from valuz_agent.ports.sandbox_provider import SandboxBootContext, SandboxSpec


@pytest.fixture
def restore_policy() -> Iterator[None]:
    original = ext.sandbox_policy
    try:
        yield
    finally:
        ext.sandbox_policy = original


async def test_oss_default_allows_provision() -> None:
    assert isinstance(ext.sandbox_policy, AllowAllSandboxPolicy)
    decision = await authorize_sandbox_provision(owner_user_id="")
    assert decision.allowed is True


async def test_bound_policy_can_deny(restore_policy: None) -> None:
    class DenyPolicy:
        async def authorize_provision(self, ctx: SandboxProvisionContext) -> SandboxDecision:
            return SandboxDecision(allowed=False, reason="not in plan")

    set_sandbox_policy(DenyPolicy())
    decision = await authorize_sandbox_provision(owner_user_id="u1", project_id="p1")
    assert decision.allowed is False
    assert decision.reason == "not in plan"


async def test_authorize_is_fail_closed_on_error(restore_policy: None) -> None:
    class ExplodingPolicy:
        async def authorize_provision(self, ctx: SandboxProvisionContext) -> SandboxDecision:
            raise RuntimeError("policy backend unreachable")

    set_sandbox_policy(ExplodingPolicy())
    decision = await authorize_sandbox_provision(owner_user_id="u1")
    assert decision.allowed is False  # fail-closed: an errored policy denies


async def test_policy_receives_owner_and_project(restore_policy: None) -> None:
    seen: dict[str, str] = {}

    class RecordingPolicy:
        async def authorize_provision(self, ctx: SandboxProvisionContext) -> SandboxDecision:
            seen["owner"] = ctx.owner_user_id
            seen["project"] = ctx.project_id
            return SandboxDecision(allowed=True)

    set_sandbox_policy(RecordingPolicy())
    await authorize_sandbox_provision(owner_user_id="owner-42", project_id="proj-9")
    assert seen == {"owner": "owner-42", "project": "proj-9"}


def test_get_set_round_trip(restore_policy: None) -> None:
    policy = AllowAllSandboxPolicy()
    set_sandbox_policy(policy)
    assert get_sandbox_policy() is policy


def test_owner_threads_through_provision_contract() -> None:
    # PR-B: owner_user_id is a backward-compatible field (defaults empty) on
    # both the boot context and the provision spec.
    assert SandboxBootContext(host="127.0.0.1", port=0).owner_user_id == ""
    ctx = SandboxBootContext(host="127.0.0.1", port=0, owner_user_id="owner-7")
    spec = SandboxSpec(sandbox_id="s", kernel_db_path="/tmp/k.db", owner_user_id=ctx.owner_user_id)
    assert spec.owner_user_id == "owner-7"
