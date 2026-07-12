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

/**
 * Run ``fetchOne`` against every registered target concurrently. Publishes
 * failures to the degraded-targets store. Throws (the first rejection) only
 * when no target answered.
 */
export async function fanOutTargets<T>(
  fetchOne: (target: ExecutionTarget) => Promise<T>,
): Promise<FanOutOutcome<T>> {
  const targets = getListFanOutTargets();
  const settled = await Promise.allSettled(targets.map(fetchOne));
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
