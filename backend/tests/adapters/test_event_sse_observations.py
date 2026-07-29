"""Host observations reach the SSE stream without any kernel involved.

Sandbox boot is the case this exists for: while the allocator provisions an
instance, bootstraps it over envd and waits for its kernel service, there is by
definition no kernel to carry a frame — and once one exists it is a brand-new
instance the host's live tap has not rebound onto. Both kernel-mediated paths
are therefore dead exactly when the client is waiting, which is why the
observation channel bypasses them entirely.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import asyncio
import json

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from valuz_agent.adapters import event_sse_adapter
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.session_observations import (
    InProcessSessionObservations,
    publish_session_observation,
)


async def _no_history(_user_id, _session_id, *, limit=200, offset=0, after_seq=None):
    return []


async def _no_live(_user_id, _session_id):
    return
    yield  # pragma: no cover — makes this an async generator


async def _no_kernel(_user_id, _session_id):
    return None


def _run_stream(monkeypatch, phases: list[str]) -> list[dict]:
    """Open a stream against NO kernel, publish ``phases``, collect the frames."""
    monkeypatch.setattr(event_sse_adapter.kernel_client, "get_events", _no_history)
    monkeypatch.setattr(
        event_sse_adapter.kernel_client, "subscribe_session_events_existing", _no_live
    )
    monkeypatch.setattr(event_sse_adapter.kernel_client, "current_kernel_id", _no_kernel)
    monkeypatch.setattr(event_sse_adapter, "POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(event_sse_adapter, "DB_BACKFILL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(event_sse_adapter, "IDLE_HEARTBEAT_SECONDS", 1.5)
    monkeypatch.setattr(ext, "session_observations", InProcessSessionObservations())

    async def _collect() -> list[dict]:
        async def _publisher() -> None:
            await asyncio.sleep(0.1)  # let the stream's subscription register
            for phase in phases:
                await publish_session_observation(
                    "owner-1", "sess-1", "sandbox_status", {"phase": phase}
                )

        pub = asyncio.create_task(_publisher())
        frames: list[dict] = []
        gen = event_sse_adapter.iter_events_sse("sess-1", "owner-1", after_seq=0)
        try:
            while len(frames) < len(phases):
                frame = await asyncio.wait_for(gen.__anext__(), timeout=3)
                if frame.get("event") == "heartbeat":
                    continue
                frames.append(frame)
        except TimeoutError:
            pass
        finally:
            pub.cancel()
            await asyncio.gather(pub, return_exceptions=True)
            await gen.aclose()
        return frames

    return asyncio.run(_collect())


def test_should_deliver_a_boot_phase_with_no_kernel_in_the_picture(monkeypatch) -> None:
    frames = _run_stream(monkeypatch, ["starting"])

    assert [f["event"] for f in frames] == ["session.sandbox_status"]
    body = json.loads(frames[0]["data"])
    assert body["payload"]["phase"] == "starting"


def test_should_mark_observations_as_live_only(monkeypatch) -> None:
    """No seq and no uid: nothing to replay on reconnect, nothing to dedup
    against — an observation is stale the moment the thing it narrates is over."""
    frames = _run_stream(monkeypatch, ["starting"])

    body = json.loads(frames[0]["data"])
    assert body["seq"] == 0
    assert body["event_uid"] is None


def test_should_stamp_the_host_observation_time(monkeypatch) -> None:
    """An observation has no store to date it, and the timing is the whole point."""
    frames = _run_stream(monkeypatch, ["starting"])

    assert json.loads(frames[0]["data"])["timestamp"] > 0


def test_should_deliver_the_whole_boot_sequence_in_order(monkeypatch) -> None:
    phases = ["starting", "provisioned", "running", "bootstrapping", "kernel_starting", "ready"]

    frames = _run_stream(monkeypatch, phases)

    assert [json.loads(f["data"])["payload"]["phase"] for f in frames] == phases


def test_publish_never_raises_when_the_transport_is_broken(monkeypatch) -> None:
    """Producers publish from inside provisioning — the work being narrated
    matters more than the narration."""

    class _Broken(InProcessSessionObservations):
        async def publish(self, *_args, **_kwargs):
            raise RuntimeError("transport down")

    monkeypatch.setattr(ext, "session_observations", _Broken())

    asyncio.run(publish_session_observation("owner-1", "s", "sandbox_status", {"phase": "x"}))


def test_should_drop_frames_rather_than_block_on_a_stalled_subscriber() -> None:
    """A subscriber that never drains must not stall the provision publishing to it."""

    async def _main() -> None:
        port = InProcessSessionObservations()
        agen = port.subscribe("owner-1", "sess-1")
        pending = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0.01)  # let the subscription register its queue
        for i in range(500):  # far past the queue bound
            await port.publish("owner-1", "sess-1", "sandbox_status", {"phase": str(i)})
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await agen.aclose()

    asyncio.run(asyncio.wait_for(_main(), timeout=5))
