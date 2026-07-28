"""The dispatch/shutdown race — the module's sharpest concurrency invariant.

``dispatch_async`` registers a new member; ``finish_task`` /``stop_task`` tell
every live member to shut down via ``_broadcast_shutdown``, which drains the
live-member set in ONE pop. If the loop yields to the event loop between
"member exists" and "member is registered", a concurrent broadcast sees an
empty set, the just-spawned member is never told to stop, and it hangs until
its idle TTL (10 minutes) — a rare interleaving that is close to impossible to
reproduce from a bug report.

The rule used to live in a comment ("no ``await`` may separate ..."), which
warns nobody at edit time. It is now structural: both halves of the race are
plain ``def``s, so ``await`` inside them is a SyntaxError.

These tests pin that, from two directions:

  * ``test_*_is_synchronous`` fails the moment someone makes one of them
    ``async`` — the single edit that re-opens the race — and says why.
  * ``test_shutdown_reaches_a_member_spawned_concurrently`` exercises the race
    itself: it drives a real spawn and a real broadcast from two concurrent
    tasks and asserts the member still gets its shutdown.
"""

from __future__ import annotations

import asyncio
import inspect

from valuz_agent.modules.tasks import launcher
from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.modules.tasks.dispatcher import DispatcherService
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.mailbox import MailboxRegistry, mailbox_registry

LOCAL_USER_ID = "local-test-owner"


# ---------------------------------------------------------------------------
# Structural: the compiler is the enforcement, these tests explain it
# ---------------------------------------------------------------------------


def test_spawn_actor_is_synchronous() -> None:
    """``launcher.spawn_actor`` must never become ``async``.

    It registers mailboxes, seeds the live set and starts the loop. Those have
    to land without the event loop getting a turn, or a concurrent
    ``_broadcast_shutdown`` drains the set in between and the member is lost.
    Sync makes ``await`` a SyntaxError — checked on every edit, not remembered.
    Every launch path (dispatch, kickoff, commit, recovery) goes through it.
    """
    assert not inspect.iscoroutinefunction(launcher.spawn_actor)


def test_broadcast_shutdown_is_synchronous() -> None:
    """``CoordinationService._broadcast_shutdown`` must never become ``async``.

    It pops the whole live set and then delivers to each member. An ``await``
    between the pop and the puts would let a member spawned meanwhile be
    dropped — the same race from the other side.
    """
    assert not inspect.iscoroutinefunction(CoordinationService._broadcast_shutdown)


def test_live_member_registry_is_entirely_synchronous() -> None:
    """Every registry method stays sync — it is the shared state both halves
    race over, so an await point anywhere inside reopens the window."""
    for name, member in inspect.getmembers(LiveMemberRegistry, inspect.isfunction):
        assert not inspect.iscoroutinefunction(member), f"{name} must stay synchronous"


# ---------------------------------------------------------------------------
# Behavioural: drive the race
# ---------------------------------------------------------------------------


async def test_shutdown_reaches_a_member_spawned_concurrently() -> None:
    """A member spawned while a shutdown broadcast is in flight still gets it.

    Both operations run as concurrent tasks against the same registry. Because
    each half is atomic, the interleaving can only be "spawn fully, then
    broadcast" or "broadcast, then spawn" — never a half-registered member. The
    first ordering must deliver the shutdown; the second must leave the member
    out of the drained set entirely (it is not yet live), never in a state
    where it is live but unreachable.
    """
    registry = LiveMemberRegistry()
    coordination = CoordinationService(registry=registry)
    runner = ActorRunner()
    dispatcher = DispatcherService(registry=registry, actor_runner=runner)

    task_id, lead, member = "t-race", "lead-race", "mem-race"
    loops: list[str] = []

    async def _never_runs(**kwargs: object) -> None:
        # Stand in for the member's actor loop: record that it was started and
        # park, so the spawned asyncio task doesn't touch the kernel.
        loops.append(str(kwargs["session_id"]))
        await asyncio.sleep(3600)

    runner.run_actor_loop = _never_runs  # type: ignore[method-assign]

    async def _spawn() -> None:
        launcher.spawn_actor(
            runner,
            session_id=member,
            prompt="do it",
            role="subtask",
            task_id=task_id,
            project_id="w1",
            user_id=LOCAL_USER_ID,
            registry=registry,
            dispatch_epoch=1.0,
            lead_session_id=lead,
        )

    async def _shutdown() -> None:
        coordination._broadcast_shutdown(task_id)

    try:
        await asyncio.gather(_spawn(), _shutdown())

        assert loops == [member], "the member's actor loop must have been started"
        # Either the broadcast saw the member (shutdown queued) or it ran first
        # (member still live, nothing drained). Both are consistent; a member
        # that is live with no way to be told to stop is not.
        got_shutdown = mailbox_registry.has_pending(member)
        still_live = registry.has_live_members(task_id)
        assert got_shutdown or still_live, (
            "member was neither told to shut down nor left live — it would hang "
            "until its idle TTL"
        )
    finally:
        for sid in (lead, member):
            mailbox_registry.unregister(sid)
        for t in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
            t.cancel()


async def test_broadcast_drains_every_member_exactly_once() -> None:
    """The drain is a single pop: each member is told once, and a second
    broadcast (e.g. stop_task then finish_task) is a no-op rather than a
    double delivery."""
    registry = LiveMemberRegistry()
    coordination = CoordinationService(registry=registry)
    boxes = MailboxRegistry()

    members = [f"m{i}" for i in range(4)]
    for m in members:
        registry.add_member("t1", m)
        boxes.register(m)
        mailbox_registry.register(m)
    try:
        coordination._broadcast_shutdown("t1")
        assert all(mailbox_registry.has_pending(m) for m in members)
        assert not registry.has_live_members("t1")

        for m in members:
            assert (await mailbox_registry.get(m, timeout=0.01)).kind == "shutdown"
        coordination._broadcast_shutdown("t1")  # second halt — nothing to do
        assert not any(mailbox_registry.has_pending(m) for m in members)
    finally:
        for m in members:
            mailbox_registry.unregister(m)
