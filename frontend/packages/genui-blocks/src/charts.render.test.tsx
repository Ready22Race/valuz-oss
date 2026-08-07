import { Renderer } from "@openuidev/react-lang";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createValuzLibrary } from "./library";

/**
 * The hand-drawn charts through the real parser, on the real library.
 *
 * `createValuzLibrary()` rather than a library composed here: a chart dropped
 * from `blocks.ts` would otherwise keep passing its own tests, and only the
 * registry test would notice.
 *
 * Every call below is **positional**. That is the only way to catch a schema
 * whose key order does not match the order the model writes the arguments in —
 * OpenUI Lang binds by zod key order, so a misordered schema assigns the data
 * array to a label prop and renders an empty block with no error anywhere.
 */

function renderLang(source: string) {
  return render(<Renderer library={createValuzLibrary()} response={source} />);
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
  expect(
    screen.getAllByText(text).length,
    `missing: ${String(text)}`,
  ).toBeGreaterThan(0);
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
      expect(
        container.querySelector(`[data-slot="${slot}"]`),
        `missing: ${slot}`,
      ).not.toBeNull();
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

    const summaries = Array.from(
      container.querySelectorAll(".vgb-chart-sr"),
    ).map((node) => node.textContent ?? "");
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

  it("shows a stacked bar's total in its summary, with a layer per series", () => {
    // The sum is what a reader checks a stack against. The parts themselves
    // are no longer printed under the bar — recharts' tooltip carries them
    // now — so what's left to check statically is the total (still in the
    // accessible summary), each part's name (still in the legend), and that a
    // bar layer was actually drawn per series.
    const { container } = renderLang(`root = Stack([stacked])
stacked = StackedBar(["Sep"], [{ name: "EU", values: [4] }, { name: "US", values: [6] }])`);

    expect(
      container.querySelector('[data-slot="vgb-stacked-bar"] .vgb-chart-sr')
        ?.textContent,
    ).toContain("totals from 10 to 10");
    expectText("EU");
    expectText("US");
    expect(
      container.querySelectorAll('[data-slot="vgb-stacked-bar"] .recharts-bar'),
    ).toHaveLength(2);
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
    expectText(
      "Does not reconcile: reported 120, computed 108 (difference +12).",
    );
    // The mismatch marker now rides the recharts `<Cell>` for the closing bar
    // (and the footnote span) rather than a hand-drawn `.vgb-chart-row`.
    expect(
      container.querySelector('[data-chart-mismatch="true"]'),
    ).not.toBeNull();

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
      expect(
        container.querySelector(`[data-slot="${slot}"]`),
        `missing: ${slot}`,
      ).not.toBeNull();
    }
    expectText("Sole stage");
    expectText("Single bin");
    expectText("One group");
    expectText("Whole");
  });

  it("keeps a value that another dwarfs by a hundredfold readable", () => {
    // The bar is unreadable at that ratio, so the figure beside it is the only
    // thing carrying the small value — it has to be printed, always. GroupedBar
    // no longer prints every value beside its bar now that it draws through
    // recharts (the axis + tooltip carry it), so its own extremes are checked
    // through the accessible summary instead of a literal printed mark.
    const { container } =
      renderLang(`root = Stack([hist, funnel, tree, grouped])
hist = Histogram([{ label: "tiny", count: 3 }, { label: "huge", count: 3000 }])
funnel = Funnel([{ label: "All", value: 5000 }, { label: "Few", value: 50 }])
tree = Treemap([{ label: "Giant", value: 10000 }, { label: "Speck", value: 100 }])
grouped = GroupedBar(["Only"], [{ name: "S", values: [2] }, { name: "L", values: [200] }])`);

    expectText("3,000");
    expectText("3");
    expectText("5,000");
    expectText("50");
    expect(
      container.querySelector('[data-slot="vgb-grouped-bar"] .vgb-chart-sr')
        ?.textContent,
    ).toContain("values from 2 to 200");
  });

  it("handles all-zero and negative data without inventing marks", () => {
    const { container } = renderLang(`root = Stack([hist, grouped, stacked])
hist = Histogram([{ label: "a", count: 0 }, { label: "b", count: 0 }])
grouped = GroupedBar(["Q1"], [{ name: "Down", values: [-8] }, { name: "Up", values: [4] }])
stacked = StackedBar(["Q1"], [{ name: "Bad", values: [-3] }, { name: "Good", values: [6] }])`);

    // Zero has no bar to draw, but the bins are still real bins.
    expect(
      container.querySelector('[data-slot="vgb-histogram"]'),
    ).not.toBeNull();
    expect(container.querySelectorAll(".vgb-histogram-fill")).toHaveLength(0);

    // A negative still enters the domain rather than disappearing — recharts
    // draws it below its own native zero baseline now (no more hand-drawn
    // `.vgb-chart-zero` marker), so what's checked is the value surviving
    // into the accessible summary and a bar layer per series actually drawn.
    expectText("-8");
    expect(
      container.querySelector('[data-slot="vgb-grouped-bar"] .vgb-chart-sr')
        ?.textContent,
    ).toContain("values from -8 to 4");
    expect(
      container.querySelectorAll('[data-slot="vgb-grouped-bar"] .recharts-bar'),
    ).toHaveLength(2);

    // A stack cannot carry a negative part, so it says so instead of guessing.
    expectText(/1 negative value was dropped/);
    expect(
      container.querySelector('[data-slot="vgb-stacked-bar"] .vgb-chart-sr')
        ?.textContent,
    ).toContain("totals from 6 to 6");
    expectText("Good");
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
      container.querySelectorAll(
        ".vgb-funnel-name, .vgb-heatmap-row-head, .vgb-heatmap-head, .recharts-cartesian-axis-tick-value",
      ),
    ).map((node) => node.textContent);
    // Funnel stage (its own `<text>` now, not a `.vgb-chart-label-text` row),
    // grouped category and box plot category share a recharts axis tick, plus
    // heatmap's row head and column head — every one of them whole. recharts
    // does not truncate or rotate a category tick by default, so the CJK
    // string still comes through whole even though it draws through recharts.
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
    // An unnamed series is numbered, never dropped — the legend needs
    // something to call it, and the total (2 + 5) is still in the summary.
    expectText("Series 1");
    expectText("Series 2");
    expect(
      container.querySelector('[data-slot="vgb-stacked-bar"] .vgb-chart-sr')
        ?.textContent,
    ).toContain("totals from 7 to 7");
  });
});

/*
 * The four blocks added alongside the family above.
 *
 * They are NOT in `chartBlocks`: registration in `blocks.ts` is assembled
 * centrally, so until `BubbleChart` / `SmallMultiples` / `ComboChart` / `Sankey`
 * are listed there these specs fail on an unresolved component name and on
 * nothing else. That is the intended state — adding them to the private list
 * here would go green while `createValuzLibrary()` still knew nothing of them.
 */

/** Every `r` in the plot, keyed by the `size` that produced it. */
function bubbleRadii(container: HTMLElement): Map<number, number> {
  return new Map(
    Array.from(container.querySelectorAll(".vgb-bubble-dot")).map((node) => [
      Number(node.getAttribute("data-bubble-size")),
      Number(node.getAttribute("r")),
    ]),
  );
}

describe("the four blocks join the chart family", () => {
  it("binds every block's shortest positional call to the props it reads", () => {
    const { container } = renderLang(`root = Stack([bubble, multi, combo, flow])
bubble = BubbleChart([{ x: 1, y: 2, size: 4, label: "Alpha" }], "Revenue", "Margin", "Headcount")
multi = SmallMultiples([{ label: "EMEA", values: [1, 4, 3] }, { label: "APAC", values: [2, 2, 5] }])
combo = ComboChart(["Mar", "Jun"], { name: "Volume", values: [4, 6] }, { name: "Price", values: [7, 3] })
flow = Sankey([{ id: "a", label: "Budget" }, { id: "b", label: "Salaries" }], [{ from: "a", to: "b", value: 40 }])`);

    for (const slot of [
      "vgb-bubble-chart",
      "vgb-small-multiples",
      "vgb-combo-chart",
      "vgb-sankey",
    ]) {
      expect(
        container.querySelector(`[data-slot="${slot}"]`),
        `missing: ${slot}`,
      ).not.toBeNull();
    }

    // Data that could only have arrived through the first positional argument…
    for (const text of ["Alpha", "EMEA", "Mar", "Budget"]) expectText(text);
    // …and through the ones after it: the bubble's axis names, the combo's
    // categories and series names, the sankey's link target.
    for (const text of [
      "Revenue",
      "Margin",
      "Headcount",
      "Jun",
      "Volume",
      "Price",
      "Salaries",
    ]) {
      expectText(text);
    }
  });

  it("gives each of the four a visually hidden summary of what it shows", () => {
    const { container } = renderLang(`root = Stack([bubble, multi, combo, flow])
bubble = BubbleChart([{ x: 1, y: 2, size: 4, label: "Alpha" }], "Revenue", "Margin", "Headcount", "Portfolio")
multi = SmallMultiples([{ label: "EMEA", values: [1, 4] }], "Regional revenue", "USD m")
combo = ComboChart(["Mar"], { name: "Volume", values: [4] }, { name: "Price", values: [7] })
flow = Sankey([{ id: "a", label: "Budget" }], [{ from: "a", to: "b", value: 40 }], "Spending")`);

    const summaries = Array.from(
      container.querySelectorAll(".vgb-chart-sr"),
    ).map((node) => node.textContent ?? "");
    expect(summaries).toHaveLength(4);
    expect(summaries[0]).toContain("Bubble chart of Portfolio");
    expect(summaries[0]).toContain("bubble area is proportional to Headcount");
    expect(summaries[1]).toContain("Small multiples of Regional revenue");
    expect(summaries[1]).toContain("shared scale");
    expect(summaries[2]).toContain("Combination chart");
    expect(summaries[3]).toContain("Sankey diagram of Spending");
  });

  it("renders nothing at all when there is no data", () => {
    // An empty plot holding its full height is a defect this repo has fixed
    // twice. Zero rows is not a chart with nothing in it, it is no chart.
    const { container } = renderLang(`root = Stack([bubble, multi, combo, flow])
bubble = BubbleChart([])
multi = SmallMultiples([])
combo = ComboChart([], { values: [] }, { values: [] })
flow = Sankey([], [])`);

    expect(container.querySelectorAll('[data-slot^="vgb-"]')).toHaveLength(0);
    expect(container.querySelectorAll(".vgb-chart-sr")).toHaveLength(0);
  });

  it("draws each of the four from a single data point", () => {
    const { container } = renderLang(`root = Stack([bubble, multi, combo, flow])
bubble = BubbleChart([{ x: 3, y: 3, size: 3, label: "Only" }])
multi = SmallMultiples([{ label: "Sole", values: [5] }])
combo = ComboChart(["Only"], { name: "V", values: [4] }, { name: "P", values: [2] })
flow = Sankey([{ id: "a", label: "In" }, { id: "b", label: "Out" }], [{ from: "a", to: "b", value: 1 }])`);

    for (const slot of [
      "vgb-bubble-chart",
      "vgb-small-multiples",
      "vgb-combo-chart",
      "vgb-sankey",
    ]) {
      expect(
        container.querySelector(`[data-slot="${slot}"]`),
        `missing: ${slot}`,
      ).not.toBeNull();
    }
    // One reading is a level, not a trend: SmallMultiples still draws a line
    // layer for it (with a visible dot, from its own `dot={...}` branch for a
    // single-value panel) rather than vanishing or implying movement.
    expect(
      container.querySelector(
        '[data-slot="vgb-small-multiples"] .recharts-line',
      ),
    ).not.toBeNull();
    expectText("Only");
    expectText("Sole");
  });

  it("keeps every figure printed when one value dwarfs the rest a hundredfold", () => {
    // The mark is unreadable at that ratio, so the text beside it is the only
    // thing still carrying the small value.
    renderLang(`root = Stack([bubble, multi, combo])
bubble = BubbleChart([{ x: 1, y: 1, size: 1, label: "Speck" }, { x: 2, y: 2, size: 100, label: "Giant" }])
multi = SmallMultiples([{ label: "Small", values: [1, 2] }, { label: "Large", values: [100, 200] }])
combo = ComboChart(["Q1"], { name: "V", values: [3] }, { name: "P", values: [300] })`);

    // Regexes, not exact strings: several of these sit inside a longer run of
    // text ("100–200", "size 100") and the point is that the figure survived at
    // all, not which span it landed in.
    for (const text of [/\b100\b/, /\b200\b/, /\b300\b/, /\b3\b/])
      expectText(text);
  });

  it("handles all-zero, negative and long CJK labels without inventing marks", () => {
    const { container } = renderLang(`root = Stack([multi, combo, bubble])
multi = SmallMultiples([{ label: "${LONG_CJK}", values: [0, 0] }, { label: "短", values: [0, 0] }])
combo = ComboChart(["${LONG_CJK}", "短"], { name: "V", values: [-8, 4] }, { name: "P", values: [0, 0] })
bubble = BubbleChart([{ x: -5, y: -5, size: 0, label: "${LONG_CJK}" }])`);

    // A flat, all-zero series still draws a line — down the middle, because
    // there is no range to scale against and inventing one would be a claim.
    expect(
      container.querySelector('[data-slot="vgb-small-multiples"]'),
    ).not.toBeNull();
    expectText(/Every value is 0: there is no range to scale against/);
    // A negative still enters the domain rather than disappearing — recharts
    // draws it below its own native zero baseline now (no more hand-drawn
    // `.vgb-combo-zero` marker), checked through the combo's own accessible
    // summary and a drawn bar layer.
    expect(
      container.querySelector('[data-slot="vgb-combo-chart"] .vgb-chart-sr')
        ?.textContent,
    ).toContain("-8 to 4");
    expect(
      container.querySelector('[data-slot="vgb-combo-chart"] .recharts-bar'),
    ).not.toBeNull();
    expectText("-8");
    // A bubble with no positive size has no area, so it is an outline, and the
    // note says so rather than letting it read as a small value.
    expect(
      container
        .querySelector('.vgb-bubble-dot[data-bubble-size="0"]')
        ?.getAttribute("fill"),
    ).toBe("none");
    expectText(/no positive size/);

    const labels = Array.from(
      container.querySelectorAll(".vgb-multiple-label, .vgb-bubble-key-name"),
    ).map((node) => node.textContent);
    // Small-multiples panel and bubble key, whole. The combo's category is a
    // recharts XAxis tick now — jsdom's canvas text measurement is unreliable
    // with three charts sharing one pass, so that specific tick is checked in
    // isolation below rather than here.
    expect(labels.filter((text) => text === LONG_CJK)).toHaveLength(2);
    expect(labels).toContain("短");
  });

  it("carries on when the optional fields are missing", () => {
    const { container } = renderLang(`root = Stack([multi, combo])
multi = SmallMultiples([{ label: "A", values: [1, 2] }])
combo = ComboChart(["Q1"], { values: [2] }, { values: [5] })`);

    expect(
      container.querySelector('[data-slot="vgb-combo-chart"]'),
    ).not.toBeNull();
    expect(container.querySelector(".vgb-chart-title")).toBeNull();
    // An unnamed series is numbered, never dropped — the legend needs something
    // to call it.
    expectText("Bars");
    expectText("Line");
  });
});

describe("BubbleChart encodes its third dimension as area", () => {
  it("sizes a bubble by the square root of its value, never by the value", () => {
    // The failure this guards is invisible: mapping the value to the radius
    // makes a 4x value look 16x bigger, which is the single most common way a
    // bubble chart lies. Four times the value must be twice the radius.
    const { container } = renderLang(`root = Stack([bubble])
bubble = BubbleChart([{ x: 1, y: 1, size: 1, label: "One" }, { x: 2, y: 2, size: 4, label: "Four" }])`);

    const radii = bubbleRadii(container);
    const small = radii.get(1) ?? 0;
    const large = radii.get(4) ?? 0;
    expect(small).toBeGreaterThan(0);
    expect(large / small).toBeCloseTo(2, 5);
    // …and the area ratio is therefore the value ratio, which is the claim.
    expect((large * large) / (small * small)).toBeCloseTo(4, 5);
  });

  it("scales a nine-to-one value gap to a three-to-one radius", () => {
    const { container } = renderLang(`root = Stack([bubble])
bubble = BubbleChart([{ x: 1, y: 1, size: 9, label: "Nine" }, { x: 2, y: 2, size: 81, label: "81" }])`);

    // Two decimals, because the radius is rounded to two before it reaches the
    // attribute — 13 / 4.33 is 3.002, and that is the real granularity here.
    const radii = bubbleRadii(container);
    expect((radii.get(81) ?? 0) / (radii.get(9) ?? 1)).toBeCloseTo(3, 2);
  });

  it("centres a lone bubble instead of pinning it to the origin", () => {
    // One point gives both axes a domain of zero width. Placed at the raw
    // domain bounds it would sit hard against a corner, which reads as
    // "lowest of everything" rather than as "there is nothing to compare" —
    // `domainOf` pads a flat span by a unit either side so the mark lands
    // away from any edge. The exact pixel position is recharts' internal
    // layout to decide (axis widths, label measurement); what's checked is
    // that the dot isn't pinned to an edge of the 640×200 test viewport.
    const { container } = renderLang(`root = Stack([bubble])
bubble = BubbleChart([{ x: 5, y: 5, size: 2, label: "Only" }])`);

    const dot = container.querySelector(".vgb-bubble-dot");
    const cx = Number(dot?.getAttribute("cx"));
    const cy = Number(dot?.getAttribute("cy"));
    expect(cx).toBeGreaterThan(640 / 3);
    expect(cx).toBeLessThan((640 * 2) / 3);
    expect(cy).toBeGreaterThan(200 / 3);
    expect(cy).toBeLessThan((200 * 2) / 3);
  });

  it("draws fifty points but stops listing them, and says so", () => {
    const points = Array.from(
      { length: 50 },
      (_, i) =>
        `{ x: ${i}, y: ${(i * 7) % 13}, size: ${i + 1}, label: "P${i}" }`,
    ).join(", ");
    const { container } = renderLang(`root = Stack([bubble])
bubble = BubbleChart([${points}])`);

    expect(container.querySelectorAll(".vgb-bubble-dot")).toHaveLength(50);
    expect(container.querySelectorAll(".vgb-bubble-key-item")).toHaveLength(0);
    expectText(/Individual labels are listed up to 16 points/);
  });

  it("says in the note that area, not radius, carries the value", () => {
    renderLang(`root = Stack([bubble])
bubble = BubbleChart([{ x: 1, y: 1, size: 4, label: "A" }], "Revenue", "Margin", "Headcount")`);

    expectText(/Bubble area — not radius — is proportional to Headcount/);
  });
});

describe("SmallMultiples draws every panel against one domain", () => {
  it("scales a small series and a large one against the same min and max", () => {
    // The whole point of the grid. A per-panel scale would draw a series moving
    // 0 to 1 with exactly the shape of one moving 0 to 100.
    const { container } = renderLang(`root = Stack([multi])
multi = SmallMultiples([{ label: "Tiny", values: [0, 1] }, { label: "Huge", values: [0, 100] }])`);

    // Every panel's `<LineChart>` gets the same computed `yDomain` — that's
    // the shared-scale contract — checked here through the grid's own
    // `data-scale-min`/`data-scale-max` (what every panel's YAxis domain is
    // built from) plus a drawn line layer per panel, rather than parsing
    // pixel geometry out of recharts' internal `<path>` — the panels no
    // longer render a `.vgb-multiple-line` with a `points` attribute.
    const grid = container.querySelector(".vgb-multiples");
    expect(grid?.getAttribute("data-scale-min")).toBe("0");
    expect(grid?.getAttribute("data-scale-max")).toBe("100");
    expect(container.querySelectorAll(".recharts-line")).toHaveLength(2);
  });

  it("states the shared domain under the grid", () => {
    renderLang(`root = Stack([multi])
multi = SmallMultiples([{ label: "A", values: [4, 8] }, { label: "B", values: [1, 2] }], "Revenue", "USD m")`);

    expectText(/Every panel is drawn against one shared scale, 1 to 8 USD m/);
  });
});

describe("ComboChart refuses a second axis unless it is earned", () => {
  it("shares one scale by default", () => {
    const { container } = renderLang(`root = Stack([combo])
combo = ComboChart(["Q1", "Q2"], { name: "Volume", values: [10, 20] }, { name: "Price", values: [1, 2] }, "units", "USD")`);

    expect(
      container.querySelector('[data-combo-scales="shared"]'),
    ).not.toBeNull();
    // One YAxis only — the hand-drawn `.vgb-combo-axis-right` marker is gone,
    // replaced by a second recharts `<YAxis yAxisId="line">` that's only
    // mounted when the scales actually split (below).
    expect(
      container.querySelectorAll(
        '[data-slot="vgb-combo-chart"] .recharts-yAxis-ticks',
      ),
    ).toHaveLength(1);
    expect(container.querySelector(".vgb-chart-sr")?.textContent).toContain(
      "one shared scale",
    );
  });

  it("splits only when sameScale is false and the units genuinely differ", () => {
    const { container } = renderLang(`root = Stack([combo])
combo = ComboChart(["Q1", "Q2"], { name: "Volume", values: [10, 20] }, { name: "Margin", values: [1, 2] }, "units", "%", false)`);

    expect(
      container.querySelector('[data-combo-scales="split"]'),
    ).not.toBeNull();
    // Both axes drawn (left + right), and a visible note that the two are
    // not comparable — an unlabelled second axis can show any correlation.
    expect(
      container.querySelectorAll(
        '[data-slot="vgb-combo-chart"] .recharts-yAxis-ticks',
      ),
    ).toHaveLength(2);
    expectText(/the bars are read against the left axis \(units/);
    expectText(/the line against the right axis \(%/);
    expectText(/not comparable/);
  });

  it("keeps one scale when a split is asked for but the units are the same", () => {
    const { container } = renderLang(`root = Stack([combo])
combo = ComboChart(["Q1"], { name: "A", values: [10] }, { name: "B", values: [1] }, "USD m", "USD m", false)`);

    expect(
      container.querySelector('[data-combo-scales="shared"]'),
    ).not.toBeNull();
    expectText(
      /A separate scale was requested but both series carry the same unit/,
    );
  });

  it("keeps one scale when a split is asked for but no unit is named", () => {
    const { container } = renderLang(`root = Stack([combo])
combo = ComboChart(["Q1"], { name: "A", values: [10] }, { name: "B", values: [1] })`);

    expect(
      container.querySelector('[data-combo-scales="shared"]'),
    ).not.toBeNull();
  });

  it("renders a long CJK category in full on its own axis tick", () => {
    // No spaces to break at, longer than the plot is wide. recharts does not
    // truncate or rotate a category tick by default, so the string comes
    // through whole rather than needing a hover to recover it.
    const { container } = renderLang(`root = Stack([combo])
combo = ComboChart(["${LONG_CJK}", "短"], { name: "V", values: [1, 2] }, { name: "P", values: [3, 4] })`);

    expect(
      container.querySelector('[data-slot="vgb-combo-chart"] svg')?.textContent,
    ).toContain(LONG_CJK);
  });
});

describe("Sankey checks that flow is conserved", () => {
  it("leaves a balanced diagram unflagged", () => {
    const { container } = renderLang(`root = Stack([flow])
flow = Sankey([{ id: "a", label: "Revenue" }, { id: "b", label: "Costs" }, { id: "c", label: "Profit" }], [{ from: "a", to: "b", value: 60 }, { from: "a", to: "c", value: 40 }])`);

    expect(
      container.querySelector('[data-sankey-balanced="true"]'),
    ).not.toBeNull();
    expect(container.querySelector('[data-chart-mismatch="true"]')).toBeNull();
    expect(container.querySelector(".vgb-chart-sr")?.textContent).toContain(
      "Every node's inflow matches its outflow",
    );
  });

  it("renders a node whose inflow and outflow disagree, and marks it", () => {
    // The invariant: neither figure is trusted silently and neither is adjusted.
    // Both are printed, the node is flagged, and a note says the difference was
    // left where the data put it.
    const { container } = renderLang(`root = Stack([flow])
flow = Sankey([{ id: "a", label: "Intake" }, { id: "b", label: "Warehouse" }, { id: "c", label: "Shipped" }], [{ from: "a", to: "b", value: 40 }, { from: "b", to: "c", value: 35 }])`);

    expect(
      container.querySelector('[data-sankey-balanced="false"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('.vgb-sankey-label[data-chart-mismatch="true"]'),
    ).not.toBeNull();
    expectText(/Flow does not balance at Warehouse \(in 40, out 35\)/);
    expectText(/not distributed/);
    expect(container.querySelector(".vgb-chart-sr")?.textContent).toContain(
      "Warehouse takes in 40 and sends out 35",
    );
  });

  it("tolerates the float noise a conserved diagram still produces", () => {
    // 0.1 + 0.2 is 0.30000000000000004. An exact comparison would flag a
    // diagram that balances perfectly.
    const { container } = renderLang(`root = Stack([flow])
flow = Sankey([{ id: "a", label: "In" }, { id: "b", label: "Mid" }, { id: "c", label: "X" }, { id: "d", label: "Y" }], [{ from: "a", to: "b", value: 0.3 }, { from: "b", to: "c", value: 0.1 }, { from: "b", to: "d", value: 0.2 }])`);

    expect(container.querySelector('[data-chart-mismatch="true"]')).toBeNull();
  });

  it("truncates past twelve nodes and says so", () => {
    const nodes = Array.from(
      { length: 18 },
      (_, i) => `{ id: "n${i}", label: "N${i}" }`,
    ).join(", ");
    const links = Array.from(
      { length: 17 },
      (_, i) => `{ from: "n${i}", to: "n${i + 1}", value: ${20 - i} }`,
    ).join(", ");
    const { container } = renderLang(`root = Stack([flow])
flow = Sankey([${nodes}], [${links}])`);

    expect(
      container.querySelectorAll(".vgb-sankey-label").length,
    ).toBeLessThanOrEqual(12);
    expectText(
      /were not\s+drawn, so the ribbons do not sum to the whole|nodes and .* flows were not/,
    );
  });

  it("folds a loop into the layered layout without leaving empty columns", () => {
    // A cycle drives both nodes up against the column cap. Left there it would
    // squeeze the whole diagram into the right-hand half; the depths are
    // compacted instead, and the flow that runs backwards is declared.
    const { container } = renderLang(`root = Stack([flow])
flow = Sankey([{ id: "a", label: "A" }, { id: "b", label: "B" }], [{ from: "a", to: "b", value: 5 }, { from: "b", to: "a", value: 5 }])`);

    expect(container.querySelector(".vgb-chart-sr")?.textContent).toContain(
      "2 columns",
    );
    expectText(/does not run left to right/);
  });

  it("drops a flow with no positive value rather than drawing a backwards ribbon", () => {
    const { container } = renderLang(`root = Stack([flow])
flow = Sankey([{ id: "a", label: "A" }, { id: "b", label: "B" }], [{ from: "a", to: "b", value: 10 }, { from: "a", to: "b", value: -4 }])`);

    expect(container.querySelectorAll(".vgb-sankey-ribbon")).toHaveLength(1);
    expectText(/without a positive value/);
  });
});
