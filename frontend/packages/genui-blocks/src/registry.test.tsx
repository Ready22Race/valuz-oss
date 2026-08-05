import { Renderer, defineComponent } from "@openuidev/react-lang";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { z } from "zod/v4";

import { blockNames } from "./catalog";
import { createValuzLibrary } from "./library";
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
 * draw, and a spec the prompt can describe. Either one alone is a silent
 * failure — an undescribed block is never emitted, and an unimplemented one
 * renders nothing.
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
    // prompt never mentions is dead weight.
    registerBlocks("demo", [DemoCard], { groupName: "Demo" });
    const prompt = createValuzLibrary().prompt();
    expect(prompt).toContain("DemoCard");
    expect(prompt).toContain("Demo");
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
    expect(createValuzLibrary().prompt()).not.toContain("DemoCard");
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

  it("leaves the built-in library untouched when nothing is registered", () => {
    const before = Object.keys(createValuzLibrary().components).length;
    registerBlocks("demo", [DemoCard]);
    unregisterBlocks("demo");
    expect(Object.keys(createValuzLibrary().components).length).toBe(before);
  });
});
