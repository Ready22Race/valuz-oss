"""``sandbox_status`` reaches the SSE wire over BOTH of the host's transports.

The host (a commercial allocator) announces a cold sandbox boot into the one
window where the session stream is otherwise silent: ``run_turn`` blocks on
provisioning for tens of seconds and no runtime exists yet to speak for the
session. Which transport carries the announcement depends on whether a kernel
happens to be reachable at that moment, and the two phases differ:

- ``ready`` always has a kernel (we just provisioned it) → live bus.
- ``starting`` usually does NOT (that is the definition of a cold boot) → the
  durable log, which this stream backfills from every couple of seconds.

Both legs are pinned here, because a mapping that only worked on the live path
would silently drop exactly the phase users are waiting on.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import asyncio
import json

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from app.schemas import EventData

from valuz_agent.adapters import event_sse_adapter


def _drive(monkeypatch, *, backfill: list[EventData], live: list[EventData]) -> list[dict]:
    """Run ``iter_events_sse`` against a fake seam; return the delivered frames."""
    seen_initial = False

    async def _fake_get_events(_user_id, _session_id, *, limit=200, offset=0, after_seq=None):
        nonlocal seen_initial
        first = not seen_initial
        seen_initial = True
        return list(backfill) if first else []

    async def _fake_subscribe(_user_id, _session_id):
        for item in live:
            yield item
        await asyncio.Event().wait()  # idle → the adapter falls into its poll branch

    monkeypatch.setattr(event_sse_adapter.kernel_client, "get_events", _fake_get_events)
    monkeypatch.setattr(
        event_sse_adapter.kernel_client, "subscribe_session_events", _fake_subscribe
    )
    monkeypatch.setattr(
        event_sse_adapter.kernel_client, "subscribe_session_events_existing", _fake_subscribe
    )
    monkeypatch.setattr(event_sse_adapter, "IDLE_HEARTBEAT_SECONDS", 0.3)
    monkeypatch.setattr(event_sse_adapter, "POLL_INTERVAL_SECONDS", 0.05)

    async def _collect() -> list[dict]:
        frames: list[dict] = []
        gen = event_sse_adapter.iter_events_sse("sess-1", "owner-1", after_seq=0)
        try:
            while True:
                frame = await asyncio.wait_for(gen.__anext__(), timeout=2)
                if frame.get("event") == "heartbeat":
                    break  # idle reached — everything deliverable was delivered
                frames.append(frame)
        except TimeoutError:
            pass
        finally:
            await gen.aclose()
        return frames

    return asyncio.run(_collect())


def test_should_deliver_ready_over_the_live_bus_when_the_kernel_is_up(monkeypatch) -> None:
    frames = _drive(
        monkeypatch,
        backfill=[],
        live=[
            EventData(
                type="sandbox_status",
                data={
                    "phase": "ready",
                    "scope": "session:sess-1",
                    "instance_id": "ins-abc",
                    "elapsed_ms": 24310,
                },
                timestamp=1,
                seq=101,
                event_uid=None,  # live-only: the host never persists this one
            )
        ],
    )

    assert [f["event"] for f in frames] == ["session.sandbox_status"]
    payload = json.loads(frames[0]["data"])["payload"]
    assert payload["phase"] == "ready"
    assert payload["instance_id"] == "ins-abc"
    assert payload["elapsed_ms"] == "24310"


def test_should_deliver_starting_from_the_durable_log_when_no_kernel_exists(monkeypatch) -> None:
    """The cold-boot case: nothing is live, so the announcement can only come
    off the durable backfill — the leg that makes the silent wait visible."""
    frames = _drive(
        monkeypatch,
        backfill=[
            EventData(
                type="sandbox_status",
                data={"phase": "starting", "scope": "session:sess-1"},
                timestamp=1,
                seq=5,
                message_id="m1",
                event_uid="uid-start",
            )
        ],
        live=[],
    )

    assert [f["event"] for f in frames] == ["session.sandbox_status"]
    payload = json.loads(frames[0]["data"])["payload"]
    assert payload["phase"] == "starting"
    # Absent on ``starting`` — no instance exists yet — but present and empty so
    # the wire shape does not change between phases.
    assert payload["instance_id"] == ""


def test_should_deliver_both_phases_in_order_across_the_two_transports(monkeypatch) -> None:
    """The whole boot as a client sees it: ``starting`` off the durable
    backfill, then ``ready`` live once the kernel it announces is up."""
    frames = _drive(
        monkeypatch,
        backfill=[
            EventData(
                type="sandbox_status",
                data={"phase": "starting"},
                timestamp=1,
                seq=5,
                message_id="m1",
                event_uid="uid-start",
            )
        ],
        live=[
            EventData(
                type="sandbox_status",
                data={"phase": "ready", "instance_id": "ins-abc", "elapsed_ms": 24310},
                timestamp=2,
                seq=101,
            )
        ],
    )

    phases = [json.loads(f["data"])["payload"]["phase"] for f in frames]
    assert phases == ["starting", "ready"]
