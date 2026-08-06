import type { ComponentGroup, Library } from "@openuidev/react-lang";
import { createLibrary } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

import type { BlockComponent } from "./blocks";
import { blockComponents, blockComponentGroups } from "./blocks";
import { builtInBlocksSuppressed, effectiveBlocks, runtimeBlockGroups } from "./registry";
import { ROOT_COMPONENT_NAME } from "./root";

export type { BlockComponent };
export { blockComponents, blockComponentGroups };

/**
 * OpenUI's own components plus every live block, as one renderable library.
 *
 * This is the package's **rendering-contract harness**, not a wire format. A2UI
 * is the only generative-UI protocol, and the product renders through
 * `A2UIRenderer`; but that renderer lives in `@valuz/ui`, which sits above this
 * package and cannot be imported from here. Composing the same components into
 * an OpenUI Lang library is the cheapest way for a block's own tests to prove it
 * renders from props — the schemas and components are the identical objects the
 * A2UI adapter drives.
 *
 * Composed from `openuiLibrary.components` rather than by re-importing OpenUI's
 * component definitions, because `@openuidev/react-ui/genui-lib` exports the
 * assembled library but not the individual definitions. Reading them back off
 * the library is the only public path, and it has the useful property that an
 * OpenUI upgrade adding a component picks it up for free.
 *
 * Suppression mirrors the renderer: under an edition holding `replace` only the
 * root and that edition's blocks resolve here, exactly as in A2UI — a harness
 * that resolved more than the product would prove the wrong thing.
 */
export function createValuzLibrary(): Library {
  const openuiComponents = Object.values(openuiLibrary.components) as BlockComponent[];
  const atoms = builtInBlocksSuppressed()
    ? openuiComponents.filter((c) => c.name === ROOT_COMPONENT_NAME)
    : openuiComponents;
  // Runtime blocks last: they were refused at registration if they took a name
  // already in use, so by the time they get here the merge cannot shadow
  // anything. Order is registration order, which keeps it stable between boots.
  const components = [...atoms, ...effectiveBlocks()];

  const groups: ComponentGroup[] = [
    ...(openuiLibrary.componentGroups ?? []),
    ...(builtInBlocksSuppressed() ? [] : blockComponentGroups),
    ...runtimeBlockGroups(),
  ];
  return createLibrary({
    root: ROOT_COMPONENT_NAME,
    components,
    componentGroups: narrowGroups(groups, new Set(components.map((c) => c.name))),
  });
}

/**
 * Groups rewritten to describe only what is actually in the library.
 *
 * `createLibrary` tolerates a group naming an absent component, which is
 * exactly why this is here: the harness would then claim a vocabulary the
 * product does not have, and a block test could pass against a component the
 * renderer would never resolve.
 */
export function narrowGroups(groups: ComponentGroup[], available: Set<string>): ComponentGroup[] {
  const missing = missingNames(available);
  const narrowed: ComponentGroup[] = [];
  for (const group of groups) {
    const components = group.components.filter((name) => available.has(name));
    if (!components.length) continue;
    const notes = (group.notes ?? []).filter((note) => !namesAnyOf(note, missing));
    narrowed.push(notes.length ? { ...group, components, notes } : { name: group.name, components });
  }
  return narrowed;
}

/**
 * Known component names that this scope does not offer.
 *
 * Derived from the known set rather than by looking for "any capitalised word
 * the library does not have": prose is full of capitalised words that are not
 * components, and dropping a note for saying "Prefer" would gut the guidance.
 */
export function missingNames(available: Set<string>): string[] {
  const known = [
    ...Object.keys(openuiLibrary.components),
    ...blockComponents.map((b) => b.name),
    ...effectiveBlocks().map((b) => b.name),
  ];
  return [...new Set(known)].filter((name) => !available.has(name));
}

/** True when `text` names any of `names`, matched on word boundaries. */
export function namesAnyOf(text: string, names: string[]): boolean {
  return names.some((name) => new RegExp(`\\b${name}\\b`).test(text));
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
