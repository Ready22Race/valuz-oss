/**
 * Execution targets — where a new conversation / project can run.
 *
 * OSS is single-backend and registers nothing: ``getExecutionTargets()``
 * returns ``[]``, creation entries render no location selector, and list
 * hooks fan out to nothing extra. A multi-target edition (commercial)
 * registers its targets at boot:
 *
 * ```ts
 * setExecutionTargets([
 *   { id: "local", labelKey: "commercial.exec.local", baseUrl: localBase, isDefault: true },
 *   { id: "cloud", labelKey: "commercial.exec.cloud", baseUrl: cloudBase },
 * ]);
 * ```
 *
 * Consumers:
 * - new-conversation / new-project entries: render a location selector when
 *   ``length > 1`` and pass the chosen target's ``baseUrl`` to the create call;
 * - list hooks: fan out to the non-default targets and tag each row's
 *   ``origin`` with the answering target id.
 */

import { useSyncExternalStore } from "react";

export interface ExecutionTarget {
  /** Stable id — also used as the row ``origin`` tag (e.g. "local"/"cloud"). */
  id: string;
  /** i18n key for the selector label. */
  labelKey: string;
  baseUrl: string;
  /** Marks the target that equals the module-default api base. */
  isDefault?: boolean;
  /**
   * Backend is NOT on this machine: local filesystem paths are meaningless
   * to it. Project creation switches to a managed cwd + initial-content
   * upload instead of a directory picker.
   */
  remote?: boolean;
}

let _targets: ExecutionTarget[] = [];
const _listeners = new Set<() => void>();

export function setExecutionTargets(targets: ExecutionTarget[]): void {
  _targets = [...targets];
  for (const fn of _listeners) fn();
}

export function getExecutionTargets(): ExecutionTarget[] {
  return _targets;
}

export function getDefaultExecutionTarget(): ExecutionTarget | undefined {
  return _targets.find((t) => t.isDefault) ?? _targets[0];
}

function subscribe(fn: () => void): () => void {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

/** Reactive view for creation entries (selector renders when length > 1). */
export function useExecutionTargets(): ExecutionTarget[] {
  return useSyncExternalStore(subscribe, getExecutionTargets);
}
