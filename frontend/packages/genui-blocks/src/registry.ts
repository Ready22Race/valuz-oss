import type { ComponentGroup } from "@openuidev/react-lang";

import { blockComponents } from "./blocks";
import type { BlockComponent } from "./blocks";
import { describeBlock, type BlockSpec } from "./catalog";

/**
 * Blocks registered at runtime, on top of the built-in set.
 *
 * A commercial edition ships components as a plugin rather than as a rebuild,
 * so the library cannot be a frozen module constant. What it must still be is
 * *consistent*: a block has an implementation and a spec, and the model only
 * benefits from the first if the second reaches the prompt. So the spec is
 * derived here from the implementation — `describeBlock()` reads the same zod
 * schema and description the build-time generator reads — and never authored
 * separately. Two hand-written halves drift, and the drift is silent in both
 * directions: a spec with no implementation renders nothing, an implementation
 * with no spec is never emitted.
 *
 * Registration is keyed by `source` (a plugin id) so unloading a plugin takes
 * its blocks with it.
 */

interface Registration {
  blocks: BlockComponent[];
  group: ComponentGroup;
}

const registered = new Map<string, Registration>();
const listeners = new Set<() => void>();
let version = 0;

export interface RegisterOptions {
  /**
   * Names this registration may not take — normally the host's OpenUI
   * component names. Supplied by the caller because this package deliberately
   * does not import the renderer.
   */
  reserved?: Iterable<string>;
  /**
   * How the group reads in the prompt. A block outside every group renders
   * fine but is never described to the model, so it is effectively invisible —
   * which is why this is not optional in spirit even though it has a default.
   */
  groupName?: string;
  groupNotes?: string[];
}

export interface RegisterResult {
  /** Names now renderable, in registration order. */
  accepted: string[];
  /** Names refused, with why — a caller should surface these, not ignore them. */
  rejected: { name: string; reason: string }[];
  /** Specs for the accepted blocks, to be sent to the prompt assembler. */
  specs: BlockSpec[];
}

function builtInNames(): Set<string> {
  return new Set(blockComponents.map((b) => b.name));
}

function registeredNames(exceptSource?: string): Set<string> {
  const names = new Set<string>();
  for (const [source, entry] of registered) {
    if (source === exceptSource) continue;
    for (const b of entry.blocks) names.add(b.name);
  }
  return names;
}

/**
 * Add blocks under `source`, replacing anything that source registered before.
 *
 * Reserved names are refused rather than resolved by merge order: a plugin
 * shadowing `Card` or `Table` would break every document that uses one, and
 * whichever way the merge happened to fall would be silent. `reserved` is
 * usually the host's OpenUI component names — the caller supplies them because
 * this package deliberately does not import the renderer.
 */
export function registerBlocks(
  source: string,
  blocks: BlockComponent[],
  options: RegisterOptions = {},
): RegisterResult {
  const taken = new Set([
    ...builtInNames(),
    ...registeredNames(source),
    ...(options.reserved ?? []),
  ]);

  const accepted: BlockComponent[] = [];
  const rejected: RegisterResult["rejected"] = [];
  const seen = new Set<string>();

  for (const block of blocks) {
    const name = block.name;
    if (!name) {
      rejected.push({ name: String(name), reason: "block has no name" });
    } else if (taken.has(name)) {
      rejected.push({ name, reason: "name is already taken by a built-in or another plugin" });
    } else if (seen.has(name)) {
      rejected.push({ name, reason: "duplicate name within this registration" });
    } else {
      seen.add(name);
      accepted.push(block);
    }
  }

  if (accepted.length) {
    registered.set(source, {
      blocks: accepted,
      group: {
        name: options.groupName ?? source,
        components: accepted.map((b) => b.name),
        ...(options.groupNotes?.length ? { notes: options.groupNotes } : {}),
      },
    });
  } else {
    registered.delete(source);
  }
  emit();

  return {
    accepted: accepted.map((b) => b.name),
    rejected,
    specs: accepted.map(describeBlock),
  };
}

/** Remove everything `source` registered. */
export function unregisterBlocks(source: string): void {
  if (registered.delete(source)) emit();
}

/** Every runtime-registered block, in registration order. */
export function runtimeBlocks(): BlockComponent[] {
  return [...registered.values()].flatMap((entry) => entry.blocks);
}

/**
 * One prompt group per registering source.
 *
 * Grouping is not cosmetic: the prompt is assembled from groups, so a block in
 * none of them is renderable and undescribed — it exists for a payload that
 * names it, and nothing will ever tell the model to.
 */
export function runtimeBlockGroups(): ComponentGroup[] {
  return [...registered.values()].map((entry) => entry.group);
}

/** Specs for every runtime-registered block, for the prompt assembler. */
export function runtimeBlockSpecs(): BlockSpec[] {
  return runtimeBlocks().map(describeBlock);
}

/**
 * Changes to the registry, for `useSyncExternalStore`.
 *
 * `getRegistryVersion` is the snapshot: a number rather than the block array,
 * because the array identity would change on every call and drive an infinite
 * re-render.
 */
export function subscribeBlocks(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getRegistryVersion(): number {
  return version;
}

function emit(): void {
  version += 1;
  for (const listener of listeners) listener();
}

/** Test seam: drop every runtime registration. */
export function resetRuntimeBlocks(): void {
  if (registered.size) {
    registered.clear();
    emit();
  }
}
