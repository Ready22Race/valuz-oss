import type { ComponentGroup, Library } from "@openuidev/react-lang";
import { createLibrary } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

import type { BlockComponent } from "./blocks";
import { blockComponents, blockComponentGroups } from "./blocks";
import {
  builtInBlocksSuppressed,
  effectiveBlocks,
  runtimeBlockGroups,
} from "./registry";
import { ROOT_COMPONENT_NAME } from "./root";

export type { BlockComponent };
export { blockComponents, blockComponentGroups };

/**
 * OpenUI's own library plus every block in this package.
 *
 * Composed from `openuiLibrary.components` rather than by re-importing
 * OpenUI's component definitions, because `@openuidev/react-ui/genui-lib`
 * exports the assembled library but not the individual definitions. Reading
 * them back off the library is the only public path, and it has the useful
 * property that an OpenUI upgrade adding a component picks it up for free.
 *
 * Later entries win on name collision, so a block here can deliberately
 * override an OpenUI component of the same name. None currently does.
 */
export function createValuzLibrary(): Library {
  const suppressed = builtInBlocksSuppressed();
  const openuiComponents = openuiOwnComponents(suppressed);
  const openuiGroups = suppressed ? rootOnlyGroups() : (openuiLibrary.componentGroups ?? []);
  // Runtime blocks last: they were refused at registration if they took a name
  // already in use, so by the time they get here the merge cannot shadow
  // anything. Order is registration order, which keeps the prompt stable
  // between boots.
  // Groups follow the same suppression as the components. A group naming a
  // component that is no longer registered would describe it to the model
  // anyway, which is exactly the failure this whole seam exists to avoid.
  const groups: ComponentGroup[] = [
    ...openuiGroups,
    ...(suppressed ? [] : blockComponentGroups),
    ...runtimeBlockGroups(),
  ];
  return createLibrary({
    root: ROOT_COMPONENT_NAME,
    components: [...openuiComponents, ...effectiveBlocks()],
    componentGroups: groups,
  });
}

/**
 * OpenUI's components, or — under an edition that replaces the set — only the
 * root.
 *
 * The root is not kept for symmetry: `createLibrary` throws when its `root` is
 * absent from `components`, and a document that cannot resolve its root renders
 * nothing at all. Everything above it is vocabulary the edition has taken over.
 */
function openuiOwnComponents(suppressed: boolean): BlockComponent[] {
  const all = Object.values(openuiLibrary.components) as BlockComponent[];
  return suppressed ? all.filter((c) => c.name === ROOT_COMPONENT_NAME) : all;
}

/**
 * The group describing the root, narrowed to it alone.
 *
 * Kept rather than dropped because grouping is what puts a component into the
 * prompt's signature section — an ungrouped root would still be *named* by the
 * syntax rules while its arguments went undocumented, so the model would guess
 * at the one component every document begins with.
 *
 * Its notes are filtered the same way: a note explaining `Modal` or `Tabs` is
 * misinformation once those components are gone, and misinformation in a prompt
 * is worse than silence.
 */
function rootOnlyGroups(): ComponentGroup[] {
  const source = (openuiLibrary.componentGroups ?? []).find((g) =>
    g.components.includes(ROOT_COMPONENT_NAME),
  );
  if (!source) return [{ name: "Layout", components: [ROOT_COMPONENT_NAME] }];
  const dropped = Object.keys(openuiLibrary.components).filter(
    (name) => name !== ROOT_COMPONENT_NAME,
  );
  const notes = (source.notes ?? []).filter(
    (note) => !dropped.some((name) => new RegExp(`\\b${name}\\b`).test(note)),
  );
  return [
    { name: source.name, components: [ROOT_COMPONENT_NAME], ...(notes.length ? { notes } : {}) },
  ];
}

/**
 * A library of *only* the blocks in this package.
 *
 * Not useful for rendering on its own — the blocks accept OpenUI components as
 * children and there is no `Stack` to root a document in. It exists so tests
 * and tooling can inspect this package's prompt contribution in isolation.
 */
export function createBlockOnlyLibrary(): Library {
  return createLibrary({
    components: blockComponents,
    componentGroups: blockComponentGroups,
  });
}
