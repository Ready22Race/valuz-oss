import { createLibrary, Renderer } from "@openuidev/react-lang";
import type { DefinedComponent } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Histogram } from "./index";

/**
 * Rendered through the real OpenUI Lang parser on a minimal library — the
 * schema and component are the same objects `createValuzLibrary()` composes,
 * so a positional-argument regression here would show up in the product too.
 */
const lib = createLibrary({
  root: "Stack",
  components: [
    ...(Object.values(openuiLibrary.components) as DefinedComponent[]),
    Histogram,
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

describe("Histogram draws through recharts", () => {
  it("binds bins positionally and draws a bar layer", () => {
    const { container } = renderLang(
      `root = Stack([hist])\nhist = Histogram([{ label: "0-10", count: 4 }, { label: "10-20", count: 9 }])`,
    );

    expect(
      container.querySelector('[data-slot="vgb-histogram"]'),
    ).not.toBeNull();
    expect(container.querySelector(".recharts-bar")).not.toBeNull();
    expectText("0-10");
    expectText("10-20");
  });

  it("gives the chart a visually hidden summary", () => {
    renderLang(
      `root = Stack([hist])\nhist = Histogram([{ label: "0-10", count: 4 }, { label: "10-20", count: 9 }], "Return buckets", "companies")`,
    );

    const summary = screen.getByText(/Histogram of Return buckets/);
    expect(summary.className).toContain("vgb-chart-sr");
    expect(summary.textContent).toContain("2 bins");
    expect(summary.textContent).toContain("counting companies");
    expect(summary.textContent).toContain("13");
  });

  it("renders nothing at all when there are no bins", () => {
    const { container } = renderLang(
      `root = Stack([hist])\nhist = Histogram([])`,
    );

    expect(container.querySelectorAll('[data-slot^="vgb-"]')).toHaveLength(0);
  });

  it("draws a single bin without collapsing", () => {
    const { container } = renderLang(
      `root = Stack([hist])\nhist = Histogram([{ label: "Single bin", count: 2 }])`,
    );

    expect(
      container.querySelector('[data-slot="vgb-histogram"]'),
    ).not.toBeNull();
    expect(container.querySelector(".recharts-bar")).not.toBeNull();
    expectText("Single bin");
  });

  it("keeps a value that another dwarfs a hundredfold printed", () => {
    renderLang(
      `root = Stack([hist])\nhist = Histogram([{ label: "tiny", count: 3 }, { label: "huge", count: 3000 }])`,
    );

    expectText("3");
    expectText("3,000");
  });

  it("handles all-zero bins without crashing and states the total", () => {
    const { container } = renderLang(
      `root = Stack([hist])\nhist = Histogram([{ label: "a", count: 0 }, { label: "b", count: 0 }])`,
    );

    expect(
      container.querySelector('[data-slot="vgb-histogram"]'),
    ).not.toBeNull();
    const summary = container.querySelector(".vgb-chart-sr")?.textContent ?? "";
    expect(summary).toContain("0 in total");
  });

  it("clamps a negative count for geometry but still prints the reported figure", () => {
    // A negative count is not a height — the bar draws at the floor — but the
    // model's own figure is never silently corrected away.
    renderLang(
      `root = Stack([hist])\nhist = Histogram([{ label: "a", count: -5 }, { label: "b", count: 10 }])`,
    );

    expectText("-5");
    expectText("10");
  });

  it("carries on when title and unit are missing", () => {
    const { container } = renderLang(
      `root = Stack([hist])\nhist = Histogram([{ label: "a", count: 1 }])`,
    );

    expect(
      container.querySelector('[data-slot="vgb-histogram"]'),
    ).not.toBeNull();
    expect(container.querySelector(".vgb-chart-title")).toBeNull();
    expect(container.querySelector(".vgb-chart-unit")).toBeNull();
  });
});
