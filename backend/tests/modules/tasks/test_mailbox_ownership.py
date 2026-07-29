"""Mailbox ownership — a stale loop's teardown cannot steal the live box.

The race: stop_task interrupts the lead; the old loop is still unwinding SDK
teardown (seconds) when a rapid resume — user click, or inject's TASK_HALTED
auto-revive — claims the same session id and spawns a new loop. Before claim
tokens, the old loop's ``finally`` popped the SHARED box: recovery's queued
member_done results died with it, and the new loop's next ``get`` raised
KeyError (uncaught) → spurious auto-finalize → a freshly resumed task went
``blocked``.
"""

from __future__ import annotations

import asyncio

from valuz_agent.modules.tasks.mailbox import InboxMsg, MailboxRegistry


def test_stale_release_cannot_steal_the_resumed_loops_box() -> None:
    reg = MailboxRegistry()
    old_token = reg.claim("s1")  # the original loop

    # Rapid resume: launcher claims eagerly, recovery seeds a result, the new
    # loop claims for its own release token.
    reg.claim("s1")
    reg.put("s1", InboxMsg(kind="member_done", from_session="m1"))
    new_token = reg.claim("s1")

    # The old loop's finally fires late — must be a no-op.
    reg.release("s1", old_token)
    assert reg.is_registered("s1"), "stale release must not drop the live box"
    assert reg.has_pending("s1"), "queued member_done must survive the stale teardown"

    # The rightful owner's release still works.
    reg.release("s1", new_token)
    assert not reg.is_registered("s1")


def test_release_after_unowned_register_keeps_current_owner() -> None:
    """Non-owning register (await_members belt-and-suspenders, recovery
    pre-seed) must never invalidate the running loop's token."""
    reg = MailboxRegistry()
    token = reg.claim("lead")
    reg.register("lead")  # e.g. coordination.await_member_results
    reg.release("lead", token)
    assert not reg.is_registered("lead"), "the owner's release must still land"


def test_keyerror_from_get_when_box_dropped() -> None:
    async def _run() -> None:
        reg = MailboxRegistry()
        reg.claim("s1")
        reg.unregister("s1")  # external drop
        try:
            await reg.get("s1", timeout=0.01)
        except KeyError:
            return
        raise AssertionError("get on a dropped box must raise KeyError")

    asyncio.run(_run())
