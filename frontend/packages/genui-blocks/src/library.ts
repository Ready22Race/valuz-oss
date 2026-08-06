import type { ComponentGroup, Library } from "@openuidev/react-lang";
import { createLibrary } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

import type { BlockComponent } from "./blocks";
import { blockComponents, blockComponentGroups } from "./blocks";
import { builtInBlocksSuppressed, effectiveBlocks, runtimeBlockGroups } from "./registry";
import { ROOT_COMPONENT_NAME } from "./root";
import { resolveScope, type ComponentScope } from "./scope";

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
 *
 * `scope` narrows the offer to one layer of the vocabulary — see `scope.ts` for
 * why that is worth doing. The root is in every scope: `createLibrary` throws
 * without it, and a document that cannot resolve its root renders nothing.
 */
export function createValuzLibrary(scope: ComponentScope = "all"): Library {
  const resolved = resolveScope(scope);
  const openuiComponents = Object.values(openuiLibrary.components) as BlockComponent[];
  const atoms =
    resolved === "edition"
      ? openuiComponents.filter((c) => c.name === ROOT_COMPONENT_NAME)
      : openuiComponents;
  // Runtime blocks last: they were refused at registration if they took a name
  // already in use, so by the time they get here the merge cannot shadow
  // anything. Order is registration order, which keeps the prompt stable
  // between boots.
  const blocks = resolved === "atoms" ? [] : effectiveBlocks();
  const components = [...atoms, ...blocks];

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
 * Groups are not cosmetic — the prompt's signature section is built from them,
 * so a group naming a component that is not registered describes it to the
 * model anyway, and the model emits something the renderer cannot draw. The
 * notes get the same treatment for the same reason: a note explaining `Modal`
 * or `Tabs` is misinformation once those are gone, and misinformation in a
 * prompt is worse than silence.
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
