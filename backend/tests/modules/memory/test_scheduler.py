"""Memory P1: idle-debounced extraction scheduler tests."""

# ruff: noqa: I001
from __future__ import annotations

import asyncio

from valuz_agent.modules.memory.scheduler import IdleExtractionScheduler


def test_fires_once_after_idle():
    calls: list[tuple[str, str | None]] = []

    async def runner(sid: str, uid: str | None) -> None:
        calls.append((sid, uid))

    sched = IdleExtractionScheduler(runner, delay=0.01)

    async def go() -> None:
        sched.notify_turn("s1", "u1")
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert calls == [("s1", "u1")]


def test_reschedule_cancels_prior():
    calls: list[str] = []

    async def runner(sid: str, _uid: str | None) -> None:
        calls.append(sid)

    sched = IdleExtractionScheduler(runner, delay=0.03)

    async def go() -> None:
        sched.notify_turn("s1", "u1")
        await asyncio.sleep(0.01)
        sched.notify_turn("s1", "u1")  # resets the timer — prior task cancelled
        await asyncio.sleep(0.01)
        sched.notify_turn("s1", "u1")
        await asyncio.sleep(0.06)

    asyncio.run(go())
    assert calls == ["s1"]  # fired exactly once despite three notifies


def test_noop_without_runner():
    sched = IdleExtractionScheduler(None, delay=0.01)

    async def go() -> None:
        sched.notify_turn("s1", "u1")  # must not raise
        await asyncio.sleep(0.03)

    asyncio.run(go())  # no error, nothing scheduled


def test_runner_failure_is_swallowed():
    async def runner(_sid: str, _uid: str | None) -> None:
        raise RuntimeError("boom")

    sched = IdleExtractionScheduler(runner, delay=0.01)

    async def go() -> None:
        sched.notify_turn("s1", "u1")
        await asyncio.sleep(0.05)

    asyncio.run(go())  # the failing runner must not propagate


def test_task_finish_scheduler_fires_immediately():
    from valuz_agent.modules.memory.scheduler import TaskFinishScheduler

    calls: list[tuple[str, str | None]] = []

    async def runner(tid: str, uid: str | None) -> None:
        calls.append((tid, uid))

    sched = TaskFinishScheduler(runner)

    async def go() -> None:
        sched.notify_finished("t1", "u1")  # no debounce — fires right away
        await asyncio.sleep(0.02)

    asyncio.run(go())
    assert calls == [("t1", "u1")]


def test_task_finish_scheduler_noop_without_runner():
    from valuz_agent.modules.memory.scheduler import TaskFinishScheduler

    sched = TaskFinishScheduler(None)

    async def go() -> None:
        sched.notify_finished("t1", "u1")  # must not raise
        await asyncio.sleep(0.01)

    asyncio.run(go())


def test_task_finish_scheduler_swallows_failure():
    from valuz_agent.modules.memory.scheduler import TaskFinishScheduler

    async def runner(_tid: str, _uid: str | None) -> None:
        raise RuntimeError("boom")

    sched = TaskFinishScheduler(runner)

    async def go() -> None:
        sched.notify_finished("t1", "u1")
        await asyncio.sleep(0.02)

    asyncio.run(go())  # failing runner must not propagate
