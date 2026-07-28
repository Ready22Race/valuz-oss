"""The ONE way a task actor comes to life.

Every path that starts an actor — kickoff, commit, dispatch, recovery — used
to hand-roll the same ceremony with local variations: create the kernel
session under the task's sandbox scope, index it, register mailboxes, seed the
live-member registry, spawn the loop. Four copies of the module's most
race-sensitive sequence; the spawn/shutdown race lived in exactly one of them.
These two primitives are now the only spelling.

:func:`create_task_session` — the awaitable half (kernel + index).
:func:`spawn_actor` — the SYNCHRONOUS half. A concurrent
``broadcast_shutdown`` drains the live-member set in one pop, so nothing may
yield between "the member is registered" and "its loop is spawned"; inside a
plain ``def``, ``await`` is a SyntaxError, so the compiler enforces the rule on
every edit. Work that must await belongs before the call, not inside it.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
from typing import Any, Literal

from valuz_agent.adapters import kernel_client
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.mailbox import mailbox_registry
from valuz_agent.ports.sandbox_allocator import SandboxScope


async def create_task_session(
    user_id: str,
    session: Any,
    *,
    task_id: str,
    project_id: str,
    kind: str,
) -> None:
    """Create the kernel session under the task's sandbox scope and index it.

    One task = ONE sandbox: lead and members share ``task:{task_id}`` so member
    manifests hand off to the lead through the shared filesystem.
    """
    await kernel_client.create_session(
        user_id, session, scope=SandboxScope(kind="task", id=task_id)
    )
    await project_index.record(
        project_id, session.id, kind=kind, origin="task", user_id=user_id
    )


def spawn_actor(
    actor: ActorRunner,
    *,
    session_id: str,
    prompt: str,
    role: Literal["lead", "subtask"],
    task_id: str,
    project_id: str,
    user_id: str,
    registry: LiveMemberRegistry | None = None,
    dispatch_epoch: float | None = None,
    lead_session_id: str | None = None,
) -> None:
    """Register and start one actor loop — ATOMICALLY (plain ``def``, on purpose).

    ``registry`` + ``dispatch_epoch``: members only. Dispatch passes the epoch
    (manifest mtime attribution under the shared cwd); recovery passes None —
    a resumed member's artifacts predate the respawn, so attribution restarts
    from zero. The Step-1 invariant (seed the registry BEFORE the loop spawns)
    holds on both paths because both are this function.

    ``lead_session_id``: registering the lead's inbox here (idempotent)
    guarantees a member's ``member_done`` can never land on an unregistered
    inbox and vanish — even when the lead was not started via async kickoff.
    """
    if lead_session_id:
        mailbox_registry.register(lead_session_id)
    if registry is not None:
        if dispatch_epoch is not None:
            registry.add_member(task_id, session_id, dispatch_epoch=dispatch_epoch)
        else:
            registry.add_member(task_id, session_id)
    # Eager so a shutdown racing ahead of the loop's first tick is queued, not
    # dropped (run_actor_loop's own register() is idempotent).
    mailbox_registry.register(session_id)
    asyncio.create_task(
        actor.run_actor_loop(
            session_id=session_id,
            initial_prompt=prompt,
            role=role,
            task_id=task_id,
            project_id=project_id,
            user_id=user_id,
        )
    )


__all__ = ["create_task_session", "spawn_actor"]
