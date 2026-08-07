import { createLibrary, Renderer } from "@openuidev/react-lang";
import type { DefinedComponent } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BubbleChart } from "./index";

const lib = createLibrary({
  root: "Stack",
  components: [
    ...(Object.values(openuiLibrary.components) as DefinedComponent[]),
    BubbleChart,
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

/** Every `r` in the plot, keyed by the raw `size` that produced it. */
function bubbleRadii(container: HTMLElement): Map<number, number> {
  return new Map(
    Array.from(container.querySelectorAll(".vgb-bubble-dot")).map((node) => [
      Number(node.getAttribute("data-bubble-size")),
      Number(node.getAttribute("r")),
    ]),
  );
}

describe("BubbleChart draws through recharts", () => {
  it("binds points positionally and draws a scatter layer", () => {
    const { container } = renderLang(
      `root = Stack([bubble])\nbubble = BubbleChart([{ x: 1, y: 2, size: 4, label: "Alpha" }], "Revenue", "Margin", "Headcount")`,
    );

    expect(
      container.querySelector('[data-slot="vgb-bubble-chart"]'),
    ).not.toBeNull();
    expect(container.querySelector(".recharts-scatter")).not.toBeNull();
    expectText("Alpha");
    expectText("Revenue");
    expectText("Margin");
  });

  it("gives the chart a visually hidden summary naming the size measure", () => {
    renderLang(
      `root = Stack([bubble])\nbubble = BubbleChart([{ x: 1, y: 2, size: 4, label: "Alpha" }], "Revenue", "Margin", "Headcount", "Portfolio")`,
    );

    const summary = screen.getByText(/Bubble chart of Portfolio/);
    expect(summary.textContent).toContain(
      "bubble area is proportional to Headcount",
    );
  });

  it("renders nothing at all when there are no points", () => {
    const { container } = renderLang(
      `root = Stack([bubble])\nbubble = BubbleChart([])`,
    );

    expect(container.querySelectorAll('[data-slot^="vgb-"]')).toHaveLength(0);
  });

  it("sizes a bubble by the square root of its value, never by the value", () => {
    // The failure this guards is invisible: mapping value to radius makes a 4x
    // value look 16x bigger. Four times the value must be twice the radius.
    const { container } = renderLang(
      `root = Stack([bubble])\nbubble = BubbleChart([{ x: 1, y: 1, size: 1, label: "One" }, { x: 2, y: 2, size: 4, label: "Four" }])`,
    );

    const radii = bubbleRadii(container);
    const small = radii.get(1) ?? 0;
    const large = radii.get(4) ?? 0;
    expect(small).toBeGreaterThan(0);
    expect(large / small).toBeCloseTo(2, 1);
    expect((large * large) / (small * small)).toBeCloseTo(4, 1);
  });

  it("draws a sizeless point as an outline at the floor rather than a filled dot", () => {
    const { container } = renderLang(
      `root = Stack([bubble])\nbubble = BubbleChart([{ x: 1, y: 1, size: 3, label: "Real" }, { x: 2, y: 2, size: 0, label: "Empty" }])`,
    );

    const outline = container.querySelector(
      '.vgb-bubble-dot[data-bubble-size="0"]',
    );
    expect(outline?.getAttribute("fill")).toBe("none");
    expectText(/no positive/);
  });

  it("draws fifty points but stops listing them, and says so", () => {
    const points = Array.from(
      { length: 50 },
      (_, i) =>
        `{ x: ${i}, y: ${(i * 7) % 13}, size: ${i + 1}, label: "P${i}" }`,
    ).join(", ");
    const { container } = renderLang(
      `root = Stack([bubble])\nbubble = BubbleChart([${points}])`,
    );

    expect(container.querySelectorAll(".vgb-bubble-dot")).toHaveLength(50);
    expect(container.querySelectorAll(".vgb-bubble-key-item")).toHaveLength(0);
    expectText(/Individual labels are listed up to 16 points/);
  });

  it("draws a lone point without crashing on a zero-width domain", () => {
    const { container } = renderLang(
      `root = Stack([bubble])\nbubble = BubbleChart([{ x: 5, y: 5, size: 2, label: "Only" }])`,
    );

    expect(
      container.querySelector('[data-slot="vgb-bubble-chart"]'),
    ).not.toBeNull();
    expect(container.querySelector(".vgb-bubble-dot")).not.toBeNull();
  });

  it("says in the note that area, not radius, carries the value", () => {
    renderLang(
      `root = Stack([bubble])\nbubble = BubbleChart([{ x: 1, y: 1, size: 4, label: "A" }], "Revenue", "Margin", "Headcount")`,
    );

    expectText(/Bubble area — not radius — is proportional to Headcount/);
  });

  it("carries on when title and axis labels are missing", () => {
    const { container } = renderLang(
      `root = Stack([bubble])\nbubble = BubbleChart([{ x: 1, y: 1, size: 1, label: "A" }])`,
    );

    expect(
      container.querySelector('[data-slot="vgb-bubble-chart"]'),
    ).not.toBeNull();
    expect(container.querySelector(".vgb-chart-title")).toBeNull();
  });
});
