import { defineComponent } from "@openuidev/react-lang";
import { afterEach, describe, expect, it } from "vitest";
import { z } from "zod/v4";

import { createValuzLibrary } from "./library";
import { promptOptionsForScope } from "./prompt";
import { registerBlocks, resetRuntimeBlocks } from "./registry";
import { ROOT_COMPONENT_NAME } from "./root";
import { COMPONENT_SCOPES, type ComponentScope } from "./scope";

/**
 * `generate_ui` takes a `components` argument so one generation can be offered
 * a single layer of the vocabulary. What has to hold is a pair of properties:
 * a narrower scope really costs less, and it never describes a component it
 * does not offer — an example or note naming a missing component teaches the
 * model to emit something it was never shown.
 */

// Components that exist in exactly one layer, so a scope cannot pass these
// assertions by reordering the catalog.
const BLOCK_ONLY = "MarketIndexGrid";
const ATOM_ONLY = "SwitchGroup";

const EditionCard = defineComponent({
  name: "EditionCard",
  props: z.object({ label: z.string() }),
  description: "An edition's own block, standing in for a curated vertical set.",
  component: ({ props }) => <div>{props.label}</div>,
});

afterEach(() => resetRuntimeBlocks());

describe("component scope", () => {
  it("offers only the primitives under atoms", () => {
    const library = createValuzLibrary("atoms");
    expect(library.components[ATOM_ONLY]).toBeTruthy();
    expect(library.components[BLOCK_ONLY]).toBeUndefined();
  });

  it("offers only the root and the blocks under edition", () => {
    const library = createValuzLibrary("edition");
    expect(library.components[BLOCK_ONLY]).toBeTruthy();
    expect(library.components[ATOM_ONLY]).toBeUndefined();
    expect(library.components[ROOT_COMPONENT_NAME]).toBeTruthy();
  });

  it("keeps the root in every scope", () => {
    // Dropping it would leave a document with no resolvable root — the one
    // failure narrowing must never introduce.
    for (const scope of COMPONENT_SCOPES) {
      expect(createValuzLibrary(scope).root).toBe(ROOT_COMPONENT_NAME);
    }
  });

  it("never describes a component it does not offer", () => {
    // Groups drive the prompt's signature section, so a group naming an absent
    // component describes it to the model anyway.
    for (const scope of COMPONENT_SCOPES) {
      const library = createValuzLibrary(scope);
      const offered = new Set(Object.keys(library.components));
      for (const group of library.componentGroups ?? []) {
        for (const name of group.components) {
          expect(offered.has(name), `${scope}: ${group.name} names absent ${name}`).toBe(true);
        }
      }
    }
  });

  it("drops examples that call a component the scope withheld", () => {
    // An example is the strongest signal in the prompt; one that calls an
    // absent component is the strongest way to teach the wrong thing.
    const atoms = promptOptionsForScope("atoms");
    expect(atoms.examples?.length).toBeGreaterThan(0);
    for (const example of atoms.examples ?? []) expect(example).not.toContain(BLOCK_ONLY);

    const edition = promptOptionsForScope("edition");
    for (const example of edition.examples ?? []) expect(example).not.toContain("TextContent");
  });

  it("makes a narrower scope genuinely cheaper", () => {
    const size = (scope: ComponentScope) =>
      createValuzLibrary(scope).prompt(promptOptionsForScope(scope)).length;
    expect(size("atoms")).toBeLessThan(size("all") / 2);
    expect(size("edition")).toBeLessThan(size("all"));
  });

  it("cannot widen back to a layer an edition removed", () => {
    // Under `replace` the primitives are gone from the renderer too, so asking
    // for them must narrow the offer rather than resurrect them in the prompt.
    registerBlocks("finance", [EditionCard], { mode: "replace", groupName: "Finance" });
    for (const scope of COMPONENT_SCOPES) {
      const library = createValuzLibrary(scope);
      expect(Object.keys(library.components)).toEqual([ROOT_COMPONENT_NAME, "EditionCard"]);
    }
  });
});
