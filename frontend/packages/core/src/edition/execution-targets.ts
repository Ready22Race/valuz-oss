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

/**
 * Which target an entity belongs to when nobody observed one.
 *
 * origin is a client-side observation, so it is ``undefined`` for anything the
 * index never saw — and a single-backend build registers no targets at all,
 * leaving nothing to fall back to. Consumers still have to answer "where does
 * this run", and until now they each hardcoded ``"local"``. That is right for
 * OSS, whose one backend is a sidecar on the user's own machine, and wrong for
 * a browser-only edition whose one backend IS the cloud execution plane —
 * there, no local runtime exists to name.
 *
 * The base URL is unaffected either way: the value names the target that the
 * module-default api base already points at, so routing resolves to the same
 * backend it would have without it.
 */
export type RuntimeLocation = "local" | "cloud";

let _defaultRuntimeLocation: RuntimeLocation = "local";

/** Declare the build's default location (editions call this at boot). */
export function setDefaultRuntimeLocation(location: RuntimeLocation): void {
  if (_defaultRuntimeLocation === location) return;
  _defaultRuntimeLocation = location;
  for (const fn of _listeners) fn();
}

export function getDefaultRuntimeLocation(): RuntimeLocation {
  return _defaultRuntimeLocation;
}

function subscribe(fn: () => void): () => void {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

/** Reactive view for creation entries (selector renders when length > 1). */
export function useExecutionTargets(): ExecutionTarget[] {
  return useSyncExternalStore(subscribe, getExecutionTargets);
}

/**
 * Reactive form of {@link getDefaultRuntimeLocation} — an edition may declare
 * it from runtime config that lands after the tree has already rendered.
 */
export function useDefaultRuntimeLocation(): RuntimeLocation {
  return useSyncExternalStore(subscribe, getDefaultRuntimeLocation);
}
