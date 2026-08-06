import { Renderer, defineComponent } from "@openuidev/react-lang";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { z } from "zod/v4";

import { blockNames } from "./catalog";
import { createValuzLibrary } from "./library";
import { ROOT_COMPONENT_NAME } from "./root";
import {
  registerBlocks,
  resetRuntimeBlocks,
  runtimeBlockSpecs,
  unregisterBlocks,
} from "./registry";

/**
 * Runtime injection is how an edition adds components without forking this
 * package. What the tests here pin is not that registration "works" but that
 * the two halves of a block stay together: an implementation the renderer can
 * draw, and a spec the catalog can describe. Either one alone is a silent
 * failure — an undescribed block is never emitted, and an unimplemented one
 * renders nothing.
 *
 * `createValuzLibrary()` stands in for the renderer here. A2UI is the wire
 * protocol, but its renderer lives in `@valuz/ui`, above this package; the
 * library drives the identical component objects, so it is the closest proof
 * available from inside.
 */

const DemoCard = defineComponent({
  name: "DemoCard",
  props: z.object({ label: z.string(), value: z.string().optional() }),
  description:
    "A demonstration block registered at runtime, long enough to satisfy the description-quality guard.",
  component: ({ props }) => (
    <div data-slot="demo-card">
      <span>{props.label}</span>
      {props.value ? <span>{props.value}</span> : null}
    </div>
  ),
});

afterEach(() => resetRuntimeBlocks());

describe("runtime block registration", () => {
  it("makes a registered block renderable through the real library", () => {
    registerBlocks("demo", [DemoCard], { groupName: "Demo" });
    render(
      <Renderer library={createValuzLibrary()} response={`root = DemoCard("营收", "4.2亿")`} />,
    );
    expect(screen.getByText("营收")).toBeTruthy();
    expect(screen.getByText("4.2亿")).toBeTruthy();
  });

  it("describes it to the model in the same pass", () => {
    // The half that is easy to forget: a block the renderer can draw but the
    // catalog never mentions is dead weight. The specs are what an edition
    // hands the backend to splice into the prompt.
    registerBlocks("demo", [DemoCard], { groupName: "Demo" });
    expect(runtimeBlockSpecs().map((s) => s.name)).toContain("DemoCard");
  });

  it("derives the spec from the implementation rather than taking one", () => {
    const { specs } = registerBlocks("demo", [DemoCard]);
    expect(specs).toHaveLength(1);
    expect(specs[0]?.name).toBe("DemoCard");
    // Props come off the zod schema, so a spec cannot describe a prop the
    // component does not actually read.
    expect(specs[0]?.props.map((p) => p.name)).toEqual(["label", "value"]);
    expect(specs[0]?.props[1]?.optional).toBe(true);
  });

  it("refuses a name that would shadow a built-in", () => {
    const Impostor = defineComponent({
      name: "MiniCard",
      props: z.object({ label: z.string() }),
      description: "Would quietly replace the built-in MiniCard in every document.",
      component: () => <div />,
    });
    const result = registerBlocks("demo", [Impostor]);
    expect(result.accepted).toEqual([]);
    expect(result.rejected[0]?.name).toBe("MiniCard");
    // And the built-in still resolves.
    expect(blockNames).toContain("MiniCard");
  });

  it("refuses a name another source already registered", () => {
    registerBlocks("first", [DemoCard]);
    const second = registerBlocks("second", [DemoCard]);
    expect(second.accepted).toEqual([]);
    expect(second.rejected).toHaveLength(1);
    // First registration keeps it — deterministic, rather than last-write-wins.
    expect(runtimeBlockSpecs().map((s) => s.name)).toEqual(["DemoCard"]);
  });

  it("takes both halves away on unregister", () => {
    registerBlocks("demo", [DemoCard], { groupName: "Demo" });
    unregisterBlocks("demo");
    expect(runtimeBlockSpecs()).toEqual([]);
    expect(createValuzLibrary().components["DemoCard"]).toBeUndefined();
  });

  it("lets a source replace its own registration", () => {
    registerBlocks("demo", [DemoCard]);
    const again = registerBlocks("demo", [DemoCard]);
    // Re-registering the same source is an update, not a collision with itself.
    expect(again.accepted).toEqual(["DemoCard"]);
    expect(runtimeBlockSpecs()).toHaveLength(1);
  });

  it("honours a reserved list the caller supplies", () => {
    // The host passes OpenUI's component names; this package does not import
    // the renderer, so it cannot know them on its own.
    const result = registerBlocks("demo", [DemoCard], { reserved: ["DemoCard"] });
    expect(result.accepted).toEqual([]);
  });

  it("uses only the edition's set in replace mode", () => {
    registerBlocks("finance", [DemoCard], { mode: "replace", groupName: "Finance" });
    const library = createValuzLibrary();
    expect(library.components["DemoCard"]).toBeTruthy();
    // The built-in blocks are gone from both halves — renderable and described.
    expect(library.components["MiniCard"]).toBeUndefined();
    expect(runtimeBlockSpecs().map((s) => s.name)).toEqual(["DemoCard"]);
  });

  it("keeps only the root of OpenUI in replace mode", () => {
    registerBlocks("finance", [DemoCard], { mode: "replace" });
    const library = createValuzLibrary();
    // The root survives every mode: createLibrary throws without it, and a
    // document that cannot resolve its root renders nothing at all.
    expect(library.components[ROOT_COMPONENT_NAME]).toBeTruthy();
    expect(library.root).toBe(ROOT_COMPONENT_NAME);
    // Everything above it is vocabulary the edition has taken over.
    expect(library.components["Card"]).toBeUndefined();
    expect(library.components["Table"]).toBeUndefined();
    expect(Object.keys(library.components)).toEqual([ROOT_COMPONENT_NAME, "DemoCard"]);
  });

  it("stops claiming a vocabulary it no longer has, in replace mode", () => {
    // Groups are the library's account of what it offers. One still naming
    // Modal or Tabs would let a block test pass against a component the
    // renderer would never resolve.
    registerBlocks("finance", [DemoCard], { mode: "replace", groupName: "Finance" });
    const library = createValuzLibrary();
    const offered = new Set(Object.keys(library.components));
    for (const group of library.componentGroups ?? []) {
      for (const name of group.components) expect(offered.has(name)).toBe(true);
    }
  });

  it("frees every name but the root in replace mode", () => {
    // With the built-ins and OpenUI's set suppressed an edition may ship its
    // own MiniCard, or its own Card; refusing either would be protecting
    // something no longer there.
    const own = ["MiniCard", "Card"].map((name) =>
      defineComponent({
        name,
        props: z.object({ label: z.string() }),
        description: `An edition's own ${name}, replacing the built-in vocabulary entirely.`,
        component: ({ props }) => <div data-slot="edition-block">{props.label}</div>,
      }),
    );
    const result = registerBlocks("finance", own, { mode: "replace" });
    expect(result.accepted).toEqual(["MiniCard", "Card"]);
    render(
      <Renderer
        library={createValuzLibrary()}
        response={`root = ${ROOT_COMPONENT_NAME}([body])\nbody = MiniCard("自有")`}
      />,
    );
    expect(screen.getByText("自有")).toBeTruthy();
  });

  it("still refuses the root's name in replace mode", () => {
    const Impostor = defineComponent({
      name: ROOT_COMPONENT_NAME,
      props: z.object({ label: z.string() }),
      description: "Would shadow the root every document is required to begin with.",
      component: () => <div />,
    });
    const result = registerBlocks("finance", [Impostor], { mode: "replace" });
    expect(result.accepted).toEqual([]);
    expect(createValuzLibrary().root).toBe(ROOT_COMPONENT_NAME);
  });

  it("refuses a second source once one replaces", () => {
    registerBlocks("finance", [DemoCard], { mode: "replace" });
    const Other = defineComponent({
      name: "OtherCard",
      props: z.object({ label: z.string() }),
      description: "A second edition trying to register while another already replaced the set.",
      component: () => <div />,
    });
    const second = registerBlocks("team", [Other]);
    expect(second.accepted).toEqual([]);
    expect(second.rejected[0]?.reason).toContain("finance");
  });

  it("brings the built-ins back when the replacing source unregisters", () => {
    registerBlocks("finance", [DemoCard], { mode: "replace" });
    expect(createValuzLibrary().components["MiniCard"]).toBeUndefined();
    unregisterBlocks("finance");
    expect(createValuzLibrary().components["MiniCard"]).toBeTruthy();
  });

  it("leaves the built-in library untouched when nothing is registered", () => {
    const before = Object.keys(createValuzLibrary().components).length;
    registerBlocks("demo", [DemoCard]);
    unregisterBlocks("demo");
    expect(Object.keys(createValuzLibrary().components).length).toBe(before);
  });
});
