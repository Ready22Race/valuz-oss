import { Renderer, createLibrary } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BlockComponent } from "./blocks";
import { Avatar } from "./Avatar";
import { Footnote, FootnoteList } from "./Footnote";
import { JsonView } from "./JsonView";
import { formatJson } from "./JsonView/format";
import { KeyValue, KeyValueGroup } from "./KeyValue";
import { MetricGroup } from "./MetricGroup";
import { RichText } from "./RichText";
import { StatDelta } from "./StatDelta";

/**
 * The content family through the real parser.
 *
 * The library is composed here rather than through `createValuzLibrary()`
 * because registration in `blocks.ts` is assembled centrally; swap this for
 * `createValuzLibrary()` once these ten names are listed there. What the detour
 * cannot skip is the point of the file: every call below is positional, which
 * is the only way to catch a schema whose key order does not match the order
 * the model would write the arguments in — a mismatch that produces no parse
 * error, no type error, and an empty block.
 *
 * Beyond binding, three content-fitting hazards get a case each per block,
 * because these render model output rather than fixtures: a label far longer
 * than assumed (and in CJK, which has no spaces to break at), an optional field
 * that never arrives, and an `items` array that is empty.
 */
const contentBlocks: BlockComponent[] = [
  KeyValue,
  KeyValueGroup,
  MetricGroup,
  StatDelta,
  Avatar,
  Footnote,
  FootnoteList,
  JsonView,
  RichText,
];

function renderLang(source: string) {
  const library = createLibrary({
    root: openuiLibrary.root ?? "Stack",
    components: [
      ...(Object.values(openuiLibrary.components) as BlockComponent[]),
      ...contentBlocks,
    ],
  });
  return render(<Renderer library={library} response={source} />);
}

/** A term long enough to force a wrap in any column, with no spaces to break
 *  at — the case `overflow-wrap: anywhere` exists for. */
const LONG_CJK =
  "归属于母公司所有者的扣除非经常性损益后的净利润同比增长率（按可比口径追溯调整）";

describe("content family renders through the OpenUI Lang parser", () => {
  it("binds every block's shortest positional call to the props it reads", () => {
    renderLang(`root = Stack([pairs, group, delta, who, notes, data, prose])
pairs = KeyValueGroup([pairA, pairB])
pairA = KeyValue("流通市值", "4.2", "亿元")
pairB = KeyValue("Market capitalisation", "US$18.4bn")
group = MetricGroup([{ label: "营业收入", value: "¥12.4bn", delta: "+8.1%" }], "分部业绩", "FY2024, unaudited")
delta = StatDelta("+8.77%", "up", "vs Q3")
who = Avatar("Ada Lovelace")
notes = FootnoteList([note])
note = Footnote(1, "Segment figures exclude intra-group eliminations.")
data = JsonView({ ok: true }, "Tool result")
prose = RichText("The quarter turned on renewals.", "center", "large")`);

    for (const text of [
      "流通市值",
      "4.2",
      "亿元",
      "Market capitalisation",
      "US$18.4bn",
      "分部业绩",
      "营业收入",
      "¥12.4bn",
      "+8.1%",
      "FY2024, unaudited",
      "+8.77%",
      "vs Q3",
      "Ada Lovelace",
      "1",
      "Segment figures exclude intra-group eliminations.",
      "Tool result",
      "The quarter turned on renewals.",
    ]) {
      expect(screen.getByText(text), `missing: ${text}`).toBeTruthy();
    }
  });

  it("gives every block the DOM hook the host stylesheet and tests key on", () => {
    const { container } = renderLang(`root = Stack([pairs, group, delta, who, notes, data, prose])
pairs = KeyValueGroup([pairA])
pairA = KeyValue("Revenue", "4.2", "bn")
group = MetricGroup([{ label: "Margin", value: "38%" }])
delta = StatDelta("+1.0%")
who = Avatar("A B")
notes = FootnoteList([note])
note = Footnote(1, "Unaudited.")
data = JsonView({ ok: true })
prose = RichText("Text.")`);

    for (const slot of [
      "vgb-key-value",
      "vgb-key-value-group",
      "vgb-metric-group",
      "vgb-stat-delta",
      "vgb-avatar",
      "vgb-footnote",
      "vgb-footnote-list",
      "vgb-json-view",
      "vgb-rich-text",
    ]) {
      expect(container.querySelector(`[data-slot="${slot}"]`), `missing: ${slot}`).not.toBeNull();
    }
  });
});

describe("KeyValue / KeyValueGroup", () => {
  it("keeps a very long CJK label and renders a pair with no unit", () => {
    const { container } = renderLang(
      `root = KeyValueGroup([a, b])\na = KeyValue("${LONG_CJK}", "12.4%")\nb = KeyValue("A", "1")`,
    );
    expect(screen.getByText(LONG_CJK)).toBeTruthy();
    // No unit means no unit element — not an empty one taking up the gap.
    expect(container.querySelector(".vgb-kv-unit")).toBeNull();
    expect(container.querySelectorAll('[data-slot="vgb-key-value"]').length).toBe(2);
  });

  it("renders nothing for an empty group", () => {
    const { container } = renderLang(`root = Stack([g])\ng = KeyValueGroup([])`);
    expect(container.querySelector('[data-slot="vgb-key-value-group"]')).toBeNull();
  });
});

describe("MetricGroup", () => {
  it("renders a long CJK label, and a metric with no delta", () => {
    const { container } = renderLang(
      `root = MetricGroup([{ label: "${LONG_CJK}", value: "12.4%" }], "口径说明")`,
    );
    expect(screen.getByText(LONG_CJK)).toBeTruthy();
    expect(container.querySelector(".vgb-metric-group-delta")).toBeNull();
    // No basis passed: the line that would qualify the figures is absent
    // rather than present and empty.
    expect(container.querySelector(".vgb-metric-group-basis")).toBeNull();
  });

  it("states the shared basis under the figures", () => {
    renderLang(
      `root = MetricGroup([{ label: "Revenue", value: "$4.2M" }], "Segments", "FY2024, unaudited, RMB mn")`,
    );
    expect(screen.getByText("FY2024, unaudited, RMB mn")).toBeTruthy();
  });

  it("renders nothing — not a heading over an empty frame — for empty items", () => {
    const { container } = renderLang(
      `root = Stack([g])\ng = MetricGroup([], "分部业绩", "FY2024")`,
    );
    expect(container.querySelector('[data-slot="vgb-metric-group"]')).toBeNull();
    expect(screen.queryByText("分部业绩")).toBeNull();
  });

  it("scales to fifty items without dropping any", () => {
    const items = Array.from(
      { length: 50 },
      (_, i) => `{ label: "指标${i}", value: "${i}.0%" }`,
    ).join(", ");
    const { container } = renderLang(`root = MetricGroup([${items}])`);
    expect(container.querySelectorAll(".vgb-metric-group-item").length).toBe(50);
  });
});

describe("StatDelta", () => {
  it("colours a rise red and a fall green, the Greater China convention", () => {
    // `trendTone` is the single place this is decided; asserting the token here
    // is asserting that this block goes through it rather than around it.
    const up = renderLang(`root = StatDelta("+8.77%", "up")`);
    expect(
      up.container.querySelector(".vgb-stat-delta-figure")?.getAttribute("style"),
    ).toContain("--openui-text-danger-primary");

    const down = renderLang(`root = StatDelta("-1.2pp", "down")`);
    expect(
      down.container.querySelector(".vgb-stat-delta-figure")?.getAttribute("style"),
    ).toContain("--openui-text-success-primary");
  });

  it("infers the direction from the sign when trend is not stated", () => {
    const { container } = renderLang(`root = StatDelta("+8.77%")`);
    expect(container.querySelector('[data-slot="vgb-stat-delta"]')?.getAttribute("data-trend")).toBe(
      "up",
    );
    // No basis passed — the block is the figure alone.
    expect(container.querySelector(".vgb-stat-delta-basis")).toBeNull();
  });

  it("lets tone override the direction's colour", () => {
    // A fall in costs is a "down" that is good news, which is the only case
    // where stating both is right.
    const { container } = renderLang(`root = StatDelta("-6.0%", "down", "vs Q3", "success")`);
    expect(container.querySelector(".vgb-stat-delta-figure")?.getAttribute("style")).toContain(
      "--openui-text-success-primary",
    );
    expect(screen.getByText("vs Q3")).toBeTruthy();
  });
});

describe("Avatar", () => {
  it("falls back to initials when there is no image", () => {
    const { container } = renderLang(`root = Avatar("Grace Hopper")`);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("GH")).toBeTruthy();
  });

  it("keeps a CJK name whole in the initials", () => {
    // No spaces, so the name is one token and its first two characters stand
    // in — splitting on the first character alone would be a different person.
    renderLang(`root = Avatar("张伟民")`);
    expect(screen.getByText("张伟")).toBeTruthy();
  });

  it("never lets a non-http(s) URL become a src", () => {
    for (const url of ["javascript:alert(1)", "data:text/html,<script>", "/relative/a.png"]) {
      const { container } = renderLang(`root = Avatar("Grace Hopper", "${url}")`);
      expect(container.querySelector("img"), `accepted: ${url}`).toBeNull();
      expect(container.querySelector('[data-slot="vgb-avatar"]')).not.toBeNull();
    }
  });

  it("uses an http(s) image and names it for assistive technology", () => {
    const { container } = renderLang(`root = Avatar("Ada Lovelace", "https://example.com/a.png")`);
    const img = container.querySelector("img");
    expect(img?.getAttribute("src")).toBe("https://example.com/a.png");
    expect(img?.getAttribute("alt")).toBe("Ada Lovelace");
  });
});

describe("Footnote / FootnoteList", () => {
  it("prints the index it was given rather than the list's own counter", () => {
    // Markers in the prose decide the numbering; a list that renumbered itself
    // from 1 would silently disagree with them.
    const { container } = renderLang(
      `root = FootnoteList([a, b])\na = Footnote(4, "${LONG_CJK}")\nb = Footnote(9, "Rounded to one decimal.")`,
    );
    const indices = [...container.querySelectorAll(".vgb-footnote-index")].map(
      (n) => n.textContent,
    );
    expect(indices).toEqual(["4", "9"]);
    expect(screen.getByText(LONG_CJK)).toBeTruthy();
  });

  it("renders nothing for an empty list", () => {
    const { container } = renderLang(`root = Stack([l])\nl = FootnoteList([])`);
    expect(container.querySelector('[data-slot="vgb-footnote-list"]')).toBeNull();
  });
});


describe("JsonView", () => {
  it("caps the depth and marks where it stopped", () => {
    // The cap is the whole safety property: an uncapped pretty-printer turns
    // one oversized tool result into a page that scrolls for a screen and a
    // half and takes seconds to lay out.
    const deep = { a: { b: { c: { d: { e: 1 } } } } };
    const text = formatJson(deep);
    expect(text).toContain('"c"');
    expect(text).not.toContain('"d"');
    expect(text).not.toContain('"e"');
    expect(text).toContain("…");
  });

  it("honours an explicit collapsedDepth", () => {
    const deep = { a: { b: { c: 1 } } };
    expect(formatJson(deep, 1)).not.toContain('"b"');
    expect(formatJson(deep, 1)).toContain("…");
    expect(formatJson(deep, 3)).toContain('"c"');
  });

  it("keeps a cap in place whatever nonsense the depth prop carries", () => {
    // Never "uncapped": the failure mode of a bad prop has to stay safe, which
    // means missing, unparseable and negative all land on (or below) the
    // default rather than switching the cap off.
    const deep = { a: { b: { c: { d: 1 } } } };
    for (const bad of [undefined, null, "abc", -5, Number.NaN]) {
      expect(formatJson(deep, bad), `depth: ${String(bad)}`).not.toContain('"d"');
    }

    // An absurdly large depth is clamped to the ceiling rather than honoured,
    // so a deeper object is still cut off.
    let nested: Record<string, unknown> = { leaf: 1 };
    for (let i = 0; i < 12; i += 1) nested = { [`n${i}`]: nested };
    expect(formatJson(nested, 1e9)).not.toContain('"leaf"');
    expect(formatJson(nested, 1e9)).toContain("…");
  });

  it("caps how many keys one level prints", () => {
    const wide: Record<string, number> = {};
    for (let i = 0; i < 500; i += 1) wide[`k${i}`] = i;
    const text = formatJson(wide);
    expect(text).toContain('"k0"');
    expect(text).not.toContain('"k499"');
    expect(text).toMatch(/… \d+ more/);
  });

  it("survives a huge object without emitting an unbounded document", () => {
    const huge = Array.from({ length: 5000 }, (_, i) => ({ i, nested: { a: [1, 2, 3] } }));
    const text = formatJson(huge);
    expect(text.split("\n").length).toBeLessThan(500);
  });

  it("survives a circular graph", () => {
    const node: Record<string, unknown> = { name: "root" };
    node.self = node;
    expect(() => formatJson(node)).not.toThrow();
    expect(formatJson(node)).toContain("circular");
  });

  it("renders the capped tree as read-only text with no markup path", () => {
    const { container } = renderLang(
      `root = JsonView({ html: "<script>alert(1)</script>", nested: { a: { b: { c: 1 } } } }, "Raw response")`,
    );
    const body = container.querySelector(".vgb-json-body");
    expect(body?.tagName).toBe("PRE");
    // The value's angle brackets are characters, not elements.
    expect(container.querySelector("script")).toBeNull();
    expect(body?.textContent).toContain("<script>alert(1)</script>");
    // Static block: nothing to expand, so nothing that looks expandable.
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("details")).toBeNull();
    expect(screen.getByText("Raw response")).toBeTruthy();
  });

  it("shows a JSON string as the data it encodes", () => {
    const text = formatJson('{"a":1}');
    expect(text).toContain('"a": 1');
    // A string that merely starts with a brace is still a string.
    expect(formatJson("{not json")).toContain('"{not json"');
  });

  it("distinguishes an empty container from a withheld one", () => {
    expect(formatJson({ a: {}, b: [] })).toContain('"a": {}');
    expect(formatJson({ a: {}, b: [] })).toContain('"b": []');
  });
});

describe("RichText", () => {
  it("renders HTML and Markdown as characters, never as markup", () => {
    // This is the block's defining property: a second HTML path in a generated
    // document is a second injection surface, and OpenUI already ships the one
    // sanitised renderer (MarkDownRenderer).
    const { container } = renderLang(
      `root = RichText("<b>not bold</b> <img src=x onerror=alert(1)> **not bold** [not a link](https://x.example)")`,
    );
    expect(container.querySelector("b")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    const paragraph = container.querySelector('[data-slot="vgb-rich-text"]');
    expect(paragraph?.textContent).toContain("<b>not bold</b>");
    expect(paragraph?.textContent).toContain("**not bold**");
  });

  it("applies alignment and size without any other prop", () => {
    const { container } = renderLang(`root = RichText("${LONG_CJK}", "center")`);
    const style = container.querySelector('[data-slot="vgb-rich-text"]')?.getAttribute("style");
    expect(style).toContain("center");
    // No size passed: the medium step, not an empty font-size.
    expect(style).toContain("--openui-font-size-lg");
    expect(screen.getByText(LONG_CJK)).toBeTruthy();
  });
});
