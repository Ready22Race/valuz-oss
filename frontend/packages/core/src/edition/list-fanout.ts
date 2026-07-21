/**
 * Multi-target list fan-out.
 *
 * List surfaces (projects / sessions / runs / activity) must show BOTH
 * backends' rows on a multi-target edition, each row tagged with the target
 * that answered (``exec_origin`` — a client-side field, never a server
 * column; distinct from the server-side ``origin`` initiator field on
 * sessions/runs). OSS registers no targets → ``getListFanOutTargets()`` is
 * empty and every list keeps its single-backend path unchanged.
 *
 * Degraded mode: when one target's fetch fails, the merged list shows the
 * other target's rows and the failing target id is published through
 * ``useDegradedListTargets`` so shells can render a "list may be incomplete"
 * hint. Only when EVERY target fails does the fan-out throw.
 *
 * A target that never settles counts as failed too: browser fetch has no
 * default timeout, so a black-holed backend (connection accepted, response
 * never sent — e.g. an OOM-wedged cloud deployment) would otherwise hold
 * ``Promise.allSettled`` open forever and pin every list surface on
 * "loading" even though the healthy target answered in milliseconds. Each
 * target therefore races ``LIST_TARGET_TIMEOUT_MS``; on timeout the target
 * goes down the same degraded path as a rejection.
 */

import { useSyncExternalStore } from "react";
import { getExecutionTargets, type ExecutionTarget } from "./execution-targets";

/** Targets to fan a list call out to; [] = single-backend fast path. */
export function getListFanOutTargets(): ExecutionTarget[] {
  const targets = getExecutionTargets();
  return targets.length >= 2 ? targets : [];
}

export interface FanOutOutcome<T> {
  /** Fulfilled per-target values, in registration order. */
  values: Array<{ target: ExecutionTarget; value: T }>;
  /** Ids of targets whose fetch rejected. */
  failedTargets: string[];
}

/** Per-target budget before a hung list fetch counts as a failed target. */
export const LIST_TARGET_TIMEOUT_MS = 10_000;

/**
 * Reject after ``LIST_TARGET_TIMEOUT_MS`` when ``promise`` hasn't settled.
 * The late settlement of the losing promise stays observed (routed into the
 * already-settled deferred, a no-op) so it can't surface as an unhandled
 * rejection.
 */
function withTargetTimeout<T>(
  promise: Promise<T>,
  targetId: string,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () =>
        reject(
          new Error(
            `list target '${targetId}' timed out after ${LIST_TARGET_TIMEOUT_MS}ms`,
          ),
        ),
      LIST_TARGET_TIMEOUT_MS,
    );
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

/**
 * Run ``fetchOne`` against every registered target concurrently. Publishes
 * failures to the degraded-targets store. Throws (the first rejection) only
 * when no target answered. A target that neither resolves nor rejects within
 * ``LIST_TARGET_TIMEOUT_MS`` is treated as failed.
 */
export async function fanOutTargets<T>(
  fetchOne: (target: ExecutionTarget) => Promise<T>,
): Promise<FanOutOutcome<T>> {
  const targets = getListFanOutTargets();
  const settled = await Promise.allSettled(
    targets.map((target) => withTargetTimeout(fetchOne(target), target.id)),
  );
  const values: Array<{ target: ExecutionTarget; value: T }> = [];
  const failedTargets: string[] = [];
  let firstError: unknown;
  settled.forEach((result, i) => {
    if (result.status === "fulfilled") {
      values.push({ target: targets[i]!, value: result.value });
    } else {
      failedTargets.push(targets[i]!.id);
      firstError ??= result.reason;
    }
  });
  publishDegradedTargets(failedTargets);
  if (values.length === 0 && failedTargets.length > 0) {
    throw firstError;
  }
  return { values, failedTargets };
}

// --- degraded-targets store (shells render a "list incomplete" hint) -------

let _degraded: string[] = [];
const _listeners = new Set<() => void>();

function publishDegradedTargets(failed: string[]): void {
  const next = [...failed].sort();
  if (next.join(",") === _degraded.join(",")) return;
  _degraded = next;
  for (const fn of _listeners) fn();
}

function subscribe(fn: () => void): () => void {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

/** Target ids whose most recent list fan-out failed ([] when healthy). */
export function useDegradedListTargets(): string[] {
  return useSyncExternalStore(subscribe, () => _degraded);
}
