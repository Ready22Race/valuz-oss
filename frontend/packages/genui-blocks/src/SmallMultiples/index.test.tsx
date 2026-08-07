import { createLibrary, Renderer } from "@openuidev/react-lang";
import type { DefinedComponent } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SmallMultiples } from "./index";

const lib = createLibrary({
  root: "Stack",
  components: [
    ...(Object.values(openuiLibrary.components) as DefinedComponent[]),
    SmallMultiples,
  ] as unknown as DefinedComponent[],
});

function renderLang(source: string) {
  return render(<Renderer library={lib} response={source} />);
}

function expectText(text: string | RegExp) {
  expect(
    screen.getAllByText(text).length,
    `missing: ${String(text)}`,
  ).toBeGreaterThan(0);
}

describe("SmallMultiples draws through recharts", () => {
  it("binds panels positionally and draws one line layer per panel", () => {
    const { container } = renderLang(
      `root = Stack([multi])\nmulti = SmallMultiples([{ label: "EMEA", values: [1, 4, 3] }, { label: "APAC", values: [2, 2, 5] }])`,
    );

    expect(
      container.querySelector('[data-slot="vgb-small-multiples"]'),
    ).not.toBeNull();
    expect(container.querySelectorAll(".recharts-line")).toHaveLength(2);
    expectText("EMEA");
    expectText("APAC");
  });

  it("gives the grid a visually hidden summary naming the shared scale", () => {
    renderLang(
      `root = Stack([multi])\nmulti = SmallMultiples([{ label: "EMEA", values: [1, 4] }], "Regional revenue", "USD m")`,
    );

    const summary = screen.getByText(/Small multiples of Regional revenue/);
    expect(summary.textContent).toContain("shared scale");
  });

  it("renders nothing at all when there are no panels", () => {
    const { container } = renderLang(
      `root = Stack([multi])\nmulti = SmallMultiples([])`,
    );

    expect(container.querySelectorAll('[data-slot^="vgb-"]')).toHaveLength(0);
  });

  it("scales a small series and a large one against the same shared domain", () => {
    // The whole point of the grid. A per-panel scale would draw a series
    // moving 0 to 1 with exactly the shape of one moving 0 to 100.
    const { container } = renderLang(
      `root = Stack([multi])\nmulti = SmallMultiples([{ label: "Tiny", values: [0, 1] }, { label: "Huge", values: [0, 100] }])`,
    );

    const grid = container.querySelector(".vgb-multiples");
    expect(grid?.getAttribute("data-scale-min")).toBe("0");
    expect(grid?.getAttribute("data-scale-max")).toBe("100");
  });

  it("states the shared domain under the grid", () => {
    renderLang(
      `root = Stack([multi])\nmulti = SmallMultiples([{ label: "A", values: [4, 8] }, { label: "B", values: [1, 2] }], "Revenue", "USD m")`,
    );

    expectText(/Every panel is drawn against one shared scale, 1 to 8 USD m/);
  });

  it("draws a single-reading panel as a level rather than a trend", () => {
    const { container } = renderLang(
      `root = Stack([multi])\nmulti = SmallMultiples([{ label: "Sole", values: [5] }])`,
    );

    expect(
      container.querySelector('[data-slot="vgb-small-multiples"]'),
    ).not.toBeNull();
    expect(container.querySelector(".recharts-line")).not.toBeNull();
    expectText("Sole");
  });

  it("centres every panel when the data is flat, and says so", () => {
    const { container } = renderLang(
      `root = Stack([multi])\nmulti = SmallMultiples([{ label: "A", values: [0, 0] }, { label: "B", values: [0, 0] }])`,
    );

    expect(
      container.querySelector('[data-slot="vgb-small-multiples"]'),
    ).not.toBeNull();
    expectText(/Every value is 0: there is no range to scale against/);
  });

  it("drops a panel with no values and says how many", () => {
    renderLang(
      `root = Stack([multi])\nmulti = SmallMultiples([{ label: "A", values: [1, 2] }, { label: "B", values: [] }])`,
    );

    expectText(/1 series had no values/);
  });

  it("truncates past sixteen panels and says so", () => {
    const items = Array.from(
      { length: 20 },
      (_, i) => `{ label: "P${i}", values: [${i}, ${i + 1}] }`,
    ).join(", ");
    const { container } = renderLang(
      `root = Stack([multi])\nmulti = SmallMultiples([${items}])`,
    );

    expect(container.querySelectorAll(".vgb-multiple")).toHaveLength(16);
    expectText(/4 further panels were not drawn/);
  });

  it("carries on when title and unit are missing", () => {
    const { container } = renderLang(
      `root = Stack([multi])\nmulti = SmallMultiples([{ label: "A", values: [1, 2] }])`,
    );

    expect(
      container.querySelector('[data-slot="vgb-small-multiples"]'),
    ).not.toBeNull();
    expect(container.querySelector(".vgb-chart-title")).toBeNull();
    expect(container.querySelector(".vgb-chart-unit")).toBeNull();
  });
});
