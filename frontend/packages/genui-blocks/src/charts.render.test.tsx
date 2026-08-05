import { Renderer, createLibrary } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BlockComponent } from "./blocks";
import { BoxPlot } from "./BoxPlot";
import { GroupedBar, StackedBar } from "./CategoryBars";
import { Funnel } from "./Funnel";
import { Heatmap } from "./Heatmap";
import { Histogram } from "./Histogram";
import { Sparkline } from "./Sparkline";
import { Treemap } from "./Treemap";
import { BridgeChart, Waterfall } from "./Waterfall";

/**
 * The hand-drawn charts through the real parser.
 *
 * The library is composed here rather than through `createValuzLibrary()`
 * because registration in `blocks.ts` is assembled centrally; swap this for
 * `createValuzLibrary()` once these ten names are listed there.
 *
 * Every call below is **positional**. That is the only way to catch a schema
 * whose key order does not match the order the model writes the arguments in —
 * OpenUI Lang binds by zod key order, so a misordered schema assigns the data
 * array to a label prop and renders an empty block with no error anywhere.
 */
const chartBlocks: BlockComponent[] = [
  Sparkline,
  Waterfall,
  BridgeChart,
  Funnel,
  Heatmap,
  Histogram,
  BoxPlot,
  Treemap,
  GroupedBar,
  StackedBar,
];

function renderLang(source: string) {
  const library = createLibrary({
    root: openuiLibrary.root ?? "Stack",
    components: [
      ...(Object.values(openuiLibrary.components) as BlockComponent[]),
      ...chartBlocks,
    ],
  });
  return render(<Renderer library={library} response={source} />);
}

/*
 * `getAllByText`, never `getByText`.
 *
 * A chart's label sits in a span inside a wrapper that holds nothing else, so
 * both elements match the same string and `getByText` throws on the duplicate.
 * That failure says nothing about the block under test, so the helper asks the
 * question these tests actually mean: is this text on the page at all.
 */
function expectText(text: string | RegExp) {
  expect(screen.getAllByText(text).length, `missing: ${String(text)}`).toBeGreaterThan(0);
}

/** A category name longer than its bar, with no spaces to break at. */
const LONG_CJK = "中国证券监督管理委员会关于上市公司信息披露的监管问答第三期";

describe("chart family renders through the OpenUI Lang parser", () => {
  it("binds every block's shortest positional call to the props it reads", () => {
    const { container } =
      renderLang(`root = Stack([spark, bridge, alias, funnel, heat, hist, box, tree, grouped, stacked])
spark = Sparkline([1, 3, 2, 6])
bridge = Waterfall([{ label: "Opening", value: 100, kind: "start" }, { label: "Price", value: 12 }])
alias = BridgeChart([{ label: "Alias open", value: 5, kind: "start" }])
funnel = Funnel([{ label: "Visitors", value: 1000 }, { label: "Signups", value: 250 }])
heat = Heatmap([{ label: "EMEA", values: [1, 4] }], ["Q1", "Q2"])
hist = Histogram([{ label: "0-10", count: 4 }, { label: "10-20", count: 9 }])
box = BoxPlot([{ label: "Region A", min: 1, q1: 4, median: 6, q3: 9, max: 12 }])
tree = Treemap([{ label: "Energy", value: 40 }, { label: "Banks", value: 60 }])
grouped = GroupedBar(["Mar", "Jun"], [{ name: "EU", values: [4, 6] }, { name: "US", values: [7, 3] }])
stacked = StackedBar(["Sep"], [{ name: "EU", values: [4] }, { name: "US", values: [6] }])`);

    for (const slot of [
      "vgb-sparkline",
      "vgb-waterfall",
      "vgb-bridge-chart",
      "vgb-funnel",
      "vgb-heatmap",
      "vgb-histogram",
      "vgb-box-plot",
      "vgb-treemap",
      "vgb-grouped-bar",
      "vgb-stacked-bar",
    ]) {
      expect(container.querySelector(`[data-slot="${slot}"]`), `missing: ${slot}`).not.toBeNull();
    }

    // Data that could only have arrived through the first positional argument.
    for (const text of [
      "Opening",
      "Alias open",
      "Visitors",
      "Signups",
      "EMEA",
      "0-10",
      "Region A",
      "Energy",
      "Mar",
      "Sep",
    ]) {
      expectText(text);
    }

    // …and data that could only have arrived through the second: the heatmap's
    // column headers and the grouped chart's series names.
    expectText("Q1");
    expectText("US");
  });

  it("gives every chart a visually hidden summary of what it shows", () => {
    // An SVG polyline and a row of spans announce nothing at all. Without this
    // line a screen reader is told a chart is present by exactly nothing.
    const { container } = renderLang(`root = Stack([spark, funnel])
spark = Sparkline([4, 9], "Weekly revenue")
funnel = Funnel([{ label: "Visitors", value: 1000 }, { label: "Signups", value: 250 }], "Signup funnel", "visitors")`);

    const summaries = Array.from(container.querySelectorAll(".vgb-chart-sr")).map(
      (node) => node.textContent ?? "",
    );
    expect(summaries).toHaveLength(2);
    expect(summaries[0]).toContain("Sparkline of Weekly revenue");
    expect(summaries[0]).toContain("2 points");
    expect(summaries[1]).toContain("Funnel of Signup funnel");
    expect(summaries[1]).toContain("in visitors");
  });

  it("states each funnel stage's share of the first stage", () => {
    renderLang(`root = Stack([funnel])
funnel = Funnel([{ label: "Visitors", value: 1000 }, { label: "Signups", value: 250 }, { label: "Paid", value: 50 }])`);

    expectText("100%");
    expectText("25%");
    expectText("5%");
  });

  it("shows a stacked bar's total alongside its parts", () => {
    // The sum is what a reader checks a stack against; without it the segments
    // are unfalsifiable.
    renderLang(`root = Stack([stacked])
stacked = StackedBar(["Sep"], [{ name: "EU", values: [4] }, { name: "US", values: [6] }])`);

    expectText("10");
    expectText("EU 4");
    expectText("US 6");
  });

  it("merges a treemap past twelve slices into one 'other' tile", () => {
    const items = Array.from(
      { length: 20 },
      (_, index) => `{ label: "S${index}", value: ${20 - index} }`,
    ).join(", ");
    const { container } = renderLang(`root = Stack([tree])
tree = Treemap([${items}])`);

    const summary = container.querySelector(".vgb-chart-sr")?.textContent ?? "";
    expect(summary).toContain("12 slices");
    expect(summary).toContain("other");
    expectText(/9 smaller slices merged into "other"/);
  });
});

describe("Waterfall reconciles its running total", () => {
  it("computes the closing figure from the start plus every delta", () => {
    const { container } = renderLang(`root = Stack([bridge])
bridge = Waterfall([{ label: "FY24", value: 100, kind: "start" }, { label: "Price", value: 12 }, { label: "Volume", value: -4 }])`);

    // No end was supplied, so the block computes and labels one: 100 + 12 - 4.
    expectText("Total");
    expectText("108");
    expectText("+12");
    expectText("-4");
    expect(container.querySelector('[data-chart-mismatch="true"]')).toBeNull();
  });

  it("accepts a supplied end that agrees, without flagging it", () => {
    const { container } = renderLang(`root = Stack([bridge])
bridge = Waterfall([{ label: "FY24", value: 100, kind: "start" }, { label: "Price", value: 12 }, { label: "FY25", value: 112, kind: "end" }])`);

    expectText("FY25");
    expectText("112");
    expect(container.querySelector('[data-chart-mismatch="true"]')).toBeNull();
  });

  it("renders a supplied end that disagrees, and marks the mismatch", () => {
    // The invariant: neither figure is trusted silently. The reported number is
    // printed (dropping it would hide the disagreement), the bar is drawn at the
    // computed total, and the row plus a note say the two do not reconcile.
    const { container } = renderLang(`root = Stack([bridge])
bridge = Waterfall([{ label: "FY24", value: 100, kind: "start" }, { label: "Price", value: 12 }, { label: "Volume", value: -4 }, { label: "FY25", value: 120, kind: "end" }])`);

    expectText("120");
    expectText("Does not reconcile: reported 120, computed 108 (difference +12).");
    expect(container.querySelector('.vgb-chart-row[data-chart-mismatch="true"]')).not.toBeNull();

    const summary = container.querySelector(".vgb-chart-sr")?.textContent ?? "";
    expect(summary).toContain("does not match the reported 120");
  });

  it("tolerates the float noise a correct bridge still produces", () => {
    // 100 + 0.1 + 0.2 is 100.30000000000001. An exact comparison would flag a
    // bridge that reconciles perfectly.
    const { container } = renderLang(`root = Stack([bridge])
bridge = Waterfall([{ label: "Open", value: 100, kind: "start" }, { label: "A", value: 0.1 }, { label: "B", value: 0.2 }, { label: "Close", value: 100.3, kind: "end" }])`);

    expect(container.querySelector('[data-chart-mismatch="true"]')).toBeNull();
  });
});

describe("charts degrade rather than reserving empty space", () => {
  it("renders nothing at all when there is no data", () => {
    // An empty plot holding its full height is a defect this repo has fixed
    // twice. Zero rows is not a chart with nothing in it, it is no chart.
    const { container } =
      renderLang(`root = Stack([spark, bridge, funnel, heat, hist, box, tree, grouped, stacked])
spark = Sparkline([])
bridge = Waterfall([])
funnel = Funnel([])
heat = Heatmap([], [])
hist = Histogram([])
box = BoxPlot([])
tree = Treemap([])
grouped = GroupedBar([], [])
stacked = StackedBar([], [])`);

    expect(container.querySelectorAll('[data-slot^="vgb-"]')).toHaveLength(0);
    expect(container.querySelectorAll(".vgb-chart-sr")).toHaveLength(0);
  });

  it("drops a sparkline with a single point, which is not a trend", () => {
    const { container } = renderLang(`root = Stack([spark])
spark = Sparkline([5])`);

    expect(container.querySelector('[data-slot="vgb-sparkline"]')).toBeNull();
  });

  it("draws every other chart from a single data point", () => {
    const { container } =
      renderLang(`root = Stack([bridge, funnel, heat, hist, box, tree, grouped, stacked])
bridge = Waterfall([{ label: "Only", value: 40, kind: "start" }])
funnel = Funnel([{ label: "Sole stage", value: 7 }])
heat = Heatmap([{ label: "Row", values: [3] }], ["Col"])
hist = Histogram([{ label: "Single bin", count: 2 }])
box = BoxPlot([{ label: "One group", min: 1, q1: 2, median: 3, q3: 4, max: 5 }])
tree = Treemap([{ label: "Whole", value: 9 }])
grouped = GroupedBar(["Only"], [{ name: "S", values: [3] }])
stacked = StackedBar(["Only"], [{ name: "S", values: [3] }])`);

    for (const slot of [
      "vgb-waterfall",
      "vgb-funnel",
      "vgb-heatmap",
      "vgb-histogram",
      "vgb-box-plot",
      "vgb-treemap",
      "vgb-grouped-bar",
      "vgb-stacked-bar",
    ]) {
      expect(container.querySelector(`[data-slot="${slot}"]`), `missing: ${slot}`).not.toBeNull();
    }
    expectText("Sole stage");
    expectText("Single bin");
    expectText("One group");
    expectText("Whole");
  });

  it("keeps a value that another dwarfs by a hundredfold readable", () => {
    // The bar is unreadable at that ratio, so the figure beside it is the only
    // thing carrying the small value — it has to be printed, always.
    renderLang(`root = Stack([hist, funnel, tree, grouped])
hist = Histogram([{ label: "tiny", count: 3 }, { label: "huge", count: 3000 }])
funnel = Funnel([{ label: "All", value: 5000 }, { label: "Few", value: 50 }])
tree = Treemap([{ label: "Giant", value: 10000 }, { label: "Speck", value: 100 }])
grouped = GroupedBar(["Only"], [{ name: "S", values: [2] }, { name: "L", values: [200] }])`);

    expectText("3,000");
    expectText("3");
    expectText("5,000");
    expectText("50");
    expectText("200");
    expectText("2");
  });

  it("handles all-zero and negative data without inventing marks", () => {
    const { container } = renderLang(`root = Stack([hist, grouped, stacked])
hist = Histogram([{ label: "a", count: 0 }, { label: "b", count: 0 }])
grouped = GroupedBar(["Q1"], [{ name: "Down", values: [-8] }, { name: "Up", values: [4] }])
stacked = StackedBar(["Q1"], [{ name: "Bad", values: [-3] }, { name: "Good", values: [6] }])`);

    // Zero has no bar to draw, but the bins are still real bins.
    expect(container.querySelector('[data-slot="vgb-histogram"]')).not.toBeNull();
    expect(container.querySelectorAll(".vgb-histogram-fill")).toHaveLength(0);

    // A negative grows from a zero line rather than disappearing.
    expectText("-8");
    expect(container.querySelector(".vgb-chart-zero")).not.toBeNull();

    // A stack cannot carry a negative part, so it says so instead of guessing.
    expectText(/1 negative value was dropped/);
    expectText("Good 6");
  });

  it("renders a long CJK label in full rather than truncating it", () => {
    // No spaces to break at, longer than the bar beside it. The label wraps and
    // stays whole: truncating it would need a hover to recover, and these blocks
    // are static by design.
    const { container } = renderLang(`root = Stack([funnel, grouped, box, heat])
funnel = Funnel([{ label: "${LONG_CJK}", value: 10 }, { label: "短", value: 4 }])
grouped = GroupedBar(["${LONG_CJK}"], [{ name: "系列", values: [3] }])
box = BoxPlot([{ label: "${LONG_CJK}", min: 1, q1: 2, median: 3, q3: 4, max: 5 }])
heat = Heatmap([{ label: "${LONG_CJK}", values: [1] }], ["${LONG_CJK}"])`);

    const labels = Array.from(
      container.querySelectorAll(".vgb-chart-label-text, .vgb-heatmap-row-head, .vgb-heatmap-head"),
    ).map((node) => node.textContent);
    // Funnel stage, grouped category, box plot row, heatmap row head, heatmap
    // column head — every one of them whole.
    expect(labels.filter((text) => text === LONG_CJK)).toHaveLength(5);
    expect(labels).toContain("短");
  });

  it("carries on when the optional fields are missing", () => {
    // No unit, no title, no series name — routine in model output.
    const { container } = renderLang(`root = Stack([funnel, stacked])
funnel = Funnel([{ label: "A", value: 3 }])
stacked = StackedBar(["Q1"], [{ values: [2] }, { values: [5] }])`);

    expect(container.querySelector('[data-slot="vgb-funnel"]')).not.toBeNull();
    expect(container.querySelector(".vgb-chart-title")).toBeNull();
    expect(container.querySelector(".vgb-chart-unit")).toBeNull();
    expectText("Series 1 2");
    expectText("7");
  });
});
