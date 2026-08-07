import { describe, expect, it, vi } from "vitest";

import type { FetchResult } from "./patch";
import {
  createScheduler,
  type SchedulerClock,
  type SourceMeta,
  type SourceRegistryLookup,
  type VisibilitySource,
} from "./scheduler";

/**
 * Fully injectable time/visibility harnesses — no real timers, no
 * `Date.now()`, no `document` anywhere in these tests. A tick's "now" comes
 * entirely from `clock.now()`, advanced by the test between calls.
 */
function fakeClock(start = 0): {
  clock: SchedulerClock;
  advance: (ms: number) => void;
} {
  let now = start;
  return {
    clock: { now: () => now },
    advance: (ms: number) => {
      now += ms;
    },
  };
}

function fakeVisibility(initial = true): {
  visibility: VisibilitySource;
  hide: () => void;
  show: () => void;
} {
  let visible = initial;
  return {
    visibility: { isVisible: () => visible },
    hide: () => {
      visible = false;
    },
    show: () => {
      visible = true;
    },
  };
}

function registryOf(sources: Record<string, SourceMeta>): SourceRegistryLookup {
  return (sourceId) => sources[sourceId];
}

describe("createScheduler", () => {
  it("shares one poll across refs with identical source+params; different params poll separately", async () => {
    const { clock } = fakeClock();
    const { visibility } = fakeVisibility(true);
    const fetchSlot = vi.fn(async (): Promise<FetchResult<unknown>> => ({
      ok: true,
      data: 1,
    }));
    const sourceRegistry = registryOf({
      "test.source": { ttlMs: 60_000, minIntervalSec: 30 },
    });
    const scheduler = createScheduler({
      clock,
      visibility,
      sourceRegistry,
      fetchSlot,
    });

    const pathA1 = scheduler.registerRef({
      refId: "a1",
      source: "test.source",
      params: { symbol: "NVDA" },
    });
    const pathA2 = scheduler.registerRef({
      refId: "a2",
      source: "test.source",
      params: { symbol: "NVDA" },
    });
    const pathB = scheduler.registerRef({
      refId: "b",
      source: "test.source",
      params: { symbol: "AAPL" },
    });

    expect(pathA2).toBe(pathA1);
    expect(pathB).not.toBe(pathA1);

    await scheduler.tick();

    // Two refs on the same slot → one poll for that slot; a different
    // params slot polls independently → two calls total, not three.
    expect(fetchSlot).toHaveBeenCalledTimes(2);
    expect(fetchSlot).toHaveBeenCalledWith("test.source", { symbol: "NVDA" });
    expect(fetchSlot).toHaveBeenCalledWith("test.source", { symbol: "AAPL" });
  });

  it("floors a ref's requested refresh.interval against the source's minIntervalSec", async () => {
    const { clock, advance } = fakeClock();
    const { visibility } = fakeVisibility(true);
    const fetchSlot = vi.fn(async (): Promise<FetchResult<unknown>> => ({
      ok: true,
      data: 1,
    }));
    const sourceRegistry = registryOf({
      "test.source": { ttlMs: 60_000, minIntervalSec: 60 },
    });
    const scheduler = createScheduler({
      clock,
      visibility,
      sourceRegistry,
      fetchSlot,
    });

    scheduler.registerRef({
      refId: "a",
      source: "test.source",
      params: {},
      refreshIntervalSec: 5,
    });

    await scheduler.tick(); // t=0, due immediately (first load)
    expect(fetchSlot).toHaveBeenCalledTimes(1);

    advance(5_000); // t=5s — a naive 5s cadence would re-fire here
    await scheduler.tick();
    expect(fetchSlot).toHaveBeenCalledTimes(1); // floored to 60s, not due yet

    advance(55_000); // t=60s
    await scheduler.tick();
    expect(fetchSlot).toHaveBeenCalledTimes(2);
  });

  it("does not poll while the page is hidden, and resumes once visible", async () => {
    const { clock, advance } = fakeClock();
    const { visibility, hide, show } = fakeVisibility(true);
    const fetchSlot = vi.fn(async (): Promise<FetchResult<unknown>> => ({
      ok: true,
      data: 1,
    }));
    const sourceRegistry = registryOf({
      "test.source": { ttlMs: 1_000, minIntervalSec: 1 },
    });
    const scheduler = createScheduler({
      clock,
      visibility,
      sourceRegistry,
      fetchSlot,
    });

    scheduler.registerRef({ refId: "a", source: "test.source", params: {} });

    hide();
    await scheduler.tick(); // due at t=0, but hidden
    expect(fetchSlot).not.toHaveBeenCalled();

    advance(5_000);
    await scheduler.tick(); // still hidden — still no poll despite being well overdue
    expect(fetchSlot).not.toHaveBeenCalled();

    show();
    await scheduler.tick(); // visible again — resumes immediately
    expect(fetchSlot).toHaveBeenCalledTimes(1);
  });

  it("backs off on consecutive failures, and a 424 stops polling and marks the slot stale", async () => {
    const { clock, advance } = fakeClock();
    const { visibility } = fakeVisibility(true);
    const results: FetchResult<unknown>[] = [
      { ok: false, error: "HTTP 500" },
      { ok: false, error: "HTTP 500" },
      { ok: false, error: "connector_not_connected", notConnected: true },
    ];
    const fetchSlot = vi.fn(async (): Promise<FetchResult<unknown>> =>
      results.shift()!,
    );
    const sourceRegistry = registryOf({
      "test.source": { ttlMs: 60_000, minIntervalSec: 1 },
    });
    const scheduler = createScheduler({
      clock,
      visibility,
      sourceRegistry,
      fetchSlot,
      backoff: { baseMs: 1_000, multiplier: 2, maxMs: 60_000 },
    });

    const path = scheduler.registerRef({
      refId: "a",
      source: "test.source",
      params: {},
    });

    await scheduler.tick(); // t=0: 1st failure → backoff 1000ms
    expect(fetchSlot).toHaveBeenCalledTimes(1);
    expect(scheduler.getSlotState(path)?.state).toBe("error");

    advance(500); // t=500, not due (needs 1000)
    await scheduler.tick();
    expect(fetchSlot).toHaveBeenCalledTimes(1);

    advance(500); // t=1000
    await scheduler.tick(); // 2nd failure → backoff doubles to 2000ms
    expect(fetchSlot).toHaveBeenCalledTimes(2);

    advance(1_500); // t=2500, not due (needs 3000)
    await scheduler.tick();
    expect(fetchSlot).toHaveBeenCalledTimes(2);

    advance(500); // t=3000
    await scheduler.tick(); // 3rd call → 424
    expect(fetchSlot).toHaveBeenCalledTimes(3);
    expect(scheduler.getSlotState(path)?.state).toBe("stale");

    advance(1_000_000); // however long — a stopped slot never polls again
    await scheduler.tick();
    expect(fetchSlot).toHaveBeenCalledTimes(3);
  });

  it("never fetches an unregistered source; marks its slot stale immediately with a reason", async () => {
    const { clock } = fakeClock();
    const { visibility } = fakeVisibility(true);
    const fetchSlot = vi.fn();
    const scheduler = createScheduler({
      clock,
      visibility,
      sourceRegistry: () => undefined,
      fetchSlot,
    });

    const path = scheduler.registerRef({
      refId: "a",
      source: "unknown.source",
      params: {},
    });

    expect(scheduler.getSlotState(path)).toEqual({
      state: "stale",
      data: null,
      reason: 'unknown data source "unknown.source"',
    });

    await scheduler.tick();
    expect(fetchSlot).not.toHaveBeenCalled();
  });

  it("stops tracking a slot once its last ref unregisters", async () => {
    const { clock } = fakeClock();
    const { visibility } = fakeVisibility(true);
    const fetchSlot = vi.fn(async (): Promise<FetchResult<unknown>> => ({
      ok: true,
      data: 1,
    }));
    const sourceRegistry = registryOf({
      "test.source": { ttlMs: 60_000, minIntervalSec: 1 },
    });
    const scheduler = createScheduler({
      clock,
      visibility,
      sourceRegistry,
      fetchSlot,
    });

    const path = scheduler.registerRef({
      refId: "a",
      source: "test.source",
      params: {},
    });
    scheduler.unregisterRef("a");

    expect(scheduler.getSlotState(path)).toBeUndefined();
    await scheduler.tick();
    expect(fetchSlot).not.toHaveBeenCalled();
  });
});
