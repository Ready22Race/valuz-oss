/**
 * Global "running runs" overview, shared across consumers.
 *
 * A single module-level connection backs every mount (the sidebar count badge +
 * the Activity page), so we never open N streams. It seeds once from
 * ``/v1/runs?status=running`` then refreshes on the control-plane stream — no
 * periodic polling. Lifecycle frames (``run.started`` / ``run.finished`` /
 * ``run.status``) are the trigger; the REST snapshot stays the source of the
 * enriched {@link RunSummary} rows (title / project / todo). Keeps the last good
 * snapshot on error.
 */

import { useEffect, useState } from "react";

import { runsApi, type RunSummary } from "../api/runs-api";
import { subscribeUserStream } from "../api/user-stream";

// Collapse a burst of lifecycle frames (a turn emits several) into one refresh.
const REFRESH_DEBOUNCE_MS = 250;

let _running: RunSummary[] = [];
const _subscribers = new Set<() => void>();
let _unsubStream: (() => void) | null = null;
let _debounce: number | null = null;
let _inFlight = false;

const _notify = (): void => {
  _subscribers.forEach((fn) => fn());
};

const _poll = async (): Promise<void> => {
  if (_inFlight) return;
  _inFlight = true;
  try {
    const res = await runsApi.list({ status: "running" });
    _running = res.runs;
    _notify();
  } catch {
    // keep the last good snapshot; the next frame/refresh retries
  } finally {
    _inFlight = false;
  }
};

const _scheduleRefresh = (): void => {
  if (_debounce !== null) return;
  _debounce = window.setTimeout(() => {
    _debounce = null;
    void _poll();
  }, REFRESH_DEBOUNCE_MS);
};

/**
 * Force an immediate refresh of the shared running-runs snapshot — call after an
 * action that mints a run (sending the first message of a session) so the
 * sidebar's runs-derived lists paint without waiting for the stream frame.
 *
 * The control-plane stream normally delivers the ``run.started`` transition on
 * its own; this is a belt-and-suspenders nudge for the mint path.
 */
export const refreshRunningRuns = (): void => {
  void _poll();
};

const _start = (): void => {
  if (_unsubStream) return;
  void _poll(); // cold-start snapshot
  _unsubStream = subscribeUserStream((frame) => {
    // Any run lifecycle transition may add/remove/restate a running run.
    if (
      frame.eventType === "run.started" ||
      frame.eventType === "run.finished" ||
      frame.eventType === "run.status"
    ) {
      _scheduleRefresh();
    }
  });
};

const _stop = (): void => {
  _unsubStream?.();
  _unsubStream = null;
  if (_debounce !== null) {
    window.clearTimeout(_debounce);
    _debounce = null;
  }
};

export interface UseRunningRunsResult {
  runs: RunSummary[];
  count: number;
}

export const useRunningRuns = (): UseRunningRunsResult => {
  const [, setTick] = useState(0);
  useEffect(() => {
    const sub = (): void => setTick((t) => t + 1);
    _subscribers.add(sub);
    _start();
    return () => {
      _subscribers.delete(sub);
      if (_subscribers.size === 0) _stop();
    };
  }, []);
  return { runs: _running, count: _running.length };
};
