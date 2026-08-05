import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { Renderer } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BlockComponent } from "./blocks";
import { createValuzLibrary } from "./library";
import {
  AspectRatio,
  Cluster,
  Collapsible,
  DashboardGrid,
  Divider,
  Inline,
  Page,
  PageFooter,
  PageHeader,
  ScrollArea,
  Spacer,
} from "./Layout";

/**
 * The layout family through the real parser, on the real library.
 *
 * `createValuzLibrary()` is what the product renders with, so using it here
 * means a block that stops being registered fails this file rather than only
 * the registry test. Every call below is positional, which is the only way to
 * catch a schema whose key order does not match the order the model would write
 * the arguments in — a mismatch that produces an empty block and no error
 * anywhere.
 */
const layoutBlocks: BlockComponent[] = [
  Page,
  PageHeader,
  PageFooter,
  Inline,
  Cluster,
  DashboardGrid,
  Divider,
  Spacer,
  AspectRatio,
  ScrollArea,
  Collapsible,
];

function renderLang(source: string) {
  return render(<Renderer library={createValuzLibrary()} response={source} />);
}

/** A heading several times longer than any design assumed, in a script that has no spaces. */
const LONG_CJK = "本季度中国内地与香港市场权益类资产配置回顾以及下一阶段战术性调整建议与风险提示";
/** The other end of the same axis. */
const ONE_CHAR = "无";

describe("layout family renders through the OpenUI Lang parser", () => {
  it("binds every block's shortest positional call to the props it reads", () => {
    renderLang(`root = Page([header, grid, inline, cluster, rule, gap, ratio, scroll, fold, footer], "Q3 Review", "Group revenue and margin", "As of 30 Jun 2026")
header = PageHeader("Segment detail", "Four reporting units", "Unaudited")
grid = DashboardGrid([g1, g2], "18rem")
g1 = TextContent("EMEA")
g2 = TextContent("APAC")
inline = Inline([i1, i2], "large", "center")
i1 = TextContent("Revenue")
i2 = TextContent("$4.2M")
cluster = Cluster([c1, c2], "small")
c1 = Tag("infrastructure")
c2 = Tag("renewals")
rule = Divider("Assumptions")
gap = Spacer("large")
ratio = AspectRatio([media], "16/9")
media = TextContent("Adoption chart")
scroll = ScrollArea([wide], "12rem", "both")
wide = TextContent("A very wide table would go here")
fold = Collapsible([detail], "Methodology", true)
detail = TextContent("Figures are unaudited.")
footer = PageFooter([], "Source: exchange filings")`);

    for (const text of [
      "Q3 Review",
      "Group revenue and margin",
      "As of 30 Jun 2026",
      "Segment detail",
      "Four reporting units",
      "Unaudited",
      "EMEA",
      "APAC",
      "Revenue",
      "$4.2M",
      "infrastructure",
      "renewals",
      "Assumptions",
      "Adoption chart",
      "A very wide table would go here",
      "Methodology",
      "Figures are unaudited.",
      "Source: exchange filings",
    ]) {
      expect(screen.getByText(text), `missing: ${text}`).toBeTruthy();
    }
  });

  it("emits every block's stable slot hook", () => {
    const { container } = renderLang(`root = Page([header, grid, inline, cluster, rule, gap, ratio, scroll, fold, footer], "Title")
header = PageHeader("Section")
grid = DashboardGrid([g1])
g1 = TextContent("cell")
inline = Inline([i1])
i1 = TextContent("one")
cluster = Cluster([c1])
c1 = Tag("tag")
rule = Divider()
gap = Spacer()
ratio = AspectRatio([m])
m = TextContent("media")
scroll = ScrollArea([w])
w = TextContent("wide")
fold = Collapsible([d], "More")
d = TextContent("detail")
footer = PageFooter([], "note")`);

    for (const slot of [
      "vgb-page",
      "vgb-page-header",
      "vgb-page-footer",
      "vgb-inline",
      "vgb-cluster",
      "vgb-dashboard-grid",
      "vgb-divider",
      "vgb-spacer",
      "vgb-aspect-ratio",
      "vgb-scroll-area",
      "vgb-collapsible",
    ]) {
      expect(
        container.querySelector(`[data-slot="${slot}"]`),
        `missing: ${slot}`,
      ).not.toBeNull();
    }
  });
});

describe("Collapsible is a native disclosure", () => {
  it("renders a real <details>/<summary>, not a div with a click handler", () => {
    // The browser owns the open/closed state here. A hand-rolled version would
    // need React state, would lose keyboard operation and the print behaviour,
    // and would promise an interaction these blocks cannot honour — they render
    // model output and nothing behind them listens.
    const { container } = renderLang(
      `root = Collapsible([d], "Methodology")\nd = TextContent("Figures are unaudited.")`,
    );
    const details = container.querySelector('[data-slot="vgb-collapsible"]');
    expect(details).not.toBeNull();
    expect(details?.tagName).toBe("DETAILS");
    expect(details?.querySelector("summary")).not.toBeNull();
    expect(container.querySelector("button")).toBeNull();
    expect(details?.hasAttribute("open")).toBe(false);
  });

  it("opens on defaultOpen and leaves the attribute off otherwise", () => {
    const open = renderLang(
      `root = Collapsible([d], "Full holdings", true)\nd = TextContent("rows")`,
    );
    expect(
      open.container.querySelector("details")?.hasAttribute("open"),
    ).toBe(true);
    open.unmount();

    const shut = renderLang(
      `root = Collapsible([d], "Full holdings", false)\nd = TextContent("rows")`,
    );
    expect(
      shut.container.querySelector("details")?.hasAttribute("open"),
    ).toBe(false);
  });

  it("keeps its contents rather than hiding them behind a nameless toggle", () => {
    // An empty summary is a focusable control with no accessible name.
    const Component = Collapsible.component;
    render(
      <Component
        props={{ children: ["x"], title: "" }}
        renderNode={(value) => <span>{String(value)}</span>}
      />,
    );
    expect(screen.getByText("x")).toBeTruthy();
    expect(document.querySelector("details")).toBeNull();
  });
});

describe("DashboardGrid concedes to a narrow container", () => {
  it("floors its columns with min(100%, …) rather than a bare width", () => {
    // `minmax(16rem, 1fr)` is a floor the track cannot go below: in a 12rem
    // column the track is still 16rem, so the grid overflows and paints across
    // whatever sits beside it. Nothing errors — this assertion is the guard.
    const { container } = renderLang(
      `root = DashboardGrid([a, b], "18rem")\na = TextContent("a")\nb = TextContent("b")`,
    );
    const grid = container.querySelector<HTMLElement>('[data-slot="vgb-dashboard-grid"]');
    expect(grid?.style.gridTemplateColumns).toBe(
      "repeat(auto-fit, minmax(min(100%, 18rem), 1fr))",
    );
  });

  it("falls back to the default column floor when the width is unusable", () => {
    // Free-form lengths arrive straight from the model. A value that is not a
    // length voids the whole declaration, and the grid silently collapses to
    // one column — so an unusable value has to become the default instead.
    const { container } = renderLang(
      `root = DashboardGrid([a], "wide; color: red")\na = TextContent("a")`,
    );
    const grid = container.querySelector<HTMLElement>('[data-slot="vgb-dashboard-grid"]');
    expect(grid?.style.gridTemplateColumns).toBe(
      "repeat(auto-fit, minmax(min(100%, 16rem), 1fr))",
    );
  });

  it("holds fifty children in one grid without splitting them", () => {
    const ids = Array.from({ length: 50 }, (_, i) => `c${i}`);
    const rows = ids.map((id, i) => `${id} = TextContent("行 ${i}")`).join("\n");
    const { container } = renderLang(`root = DashboardGrid([${ids.join(", ")}])\n${rows}`);
    const grid = container.querySelector('[data-slot="vgb-dashboard-grid"]');
    expect(grid?.children.length).toBe(50);
    expect(screen.getByText("行 49")).toBeTruthy();
  });
});

describe("AspectRatio reserves space before its media loads", () => {
  it("normalises a written ratio and rejects a degenerate one", () => {
    const good = renderLang(`root = AspectRatio([m], "4:3")\nm = TextContent("m")`);
    expect(
      good.container.querySelector<HTMLElement>('[data-slot="vgb-aspect-ratio"]')?.style
        .aspectRatio,
    ).toBe("4 / 3");
    good.unmount();

    // A zero side collapses the box, which is the exact jump this block exists
    // to prevent — so it falls back rather than honouring the value.
    const bad = renderLang(`root = AspectRatio([m], "16/0")\nm = TextContent("m")`);
    expect(
      bad.container.querySelector<HTMLElement>('[data-slot="vgb-aspect-ratio"]')?.style
        .aspectRatio,
    ).toBe("16 / 9");
  });
});

describe("ScrollArea contains its own overflow", () => {
  it("caps its height on the vertical axis and marks the axis it scrolls", () => {
    const { container } = renderLang(
      `root = ScrollArea([t], "12rem")\nt = TextContent("long")`,
    );
    const box = container.querySelector<HTMLElement>('[data-slot="vgb-scroll-area"]');
    expect(box?.getAttribute("data-axis")).toBe("vertical");
    expect(box?.style.maxHeight).toBe("12rem");
  });

  it("leaves the height alone when it scrolls sideways", () => {
    const { container } = renderLang(
      `root = ScrollArea([t], "12rem", "horizontal")\nt = TextContent("wide")`,
    );
    const box = container.querySelector<HTMLElement>('[data-slot="vgb-scroll-area"]');
    expect(box?.getAttribute("data-axis")).toBe("horizontal");
    expect(box?.style.maxHeight).toBe("");
  });
});

/*
 * Content fitting. Every block in this family is something else's frame, so its
 * failures cascade: a heading that will not wrap, a gap left for a prop nobody
 * set, or an empty frame drawn around nothing shows up in every answer that
 * uses it. The three cases below are run against each block in turn.
 */

interface FitCase {
  /** Block name, for the test title. */
  name: string;
  /** `data-slot` the block emits. */
  slot: string;
  /** A program whose block carries a very long CJK heading (or CJK content). */
  long: string;
  /** The shortest call that omits every optional prop. */
  bare: string;
  /** A call with nothing to show: no children, no text. */
  empty: string;
  /** Whether the block still paints when it has nothing to show. */
  keepsEmptyFrame?: boolean;
  /** Set for the blocks that hold no text at all — a Spacer has nothing to fit. */
  holdsNoText?: boolean;
  /** Selectors that must be absent once the optional props are omitted. */
  absentWhenBare?: string[];
}

const FIT_CASES: FitCase[] = [
  {
    name: "Page",
    slot: "vgb-page",
    long: `root = Page([t], "${LONG_CJK}", "${LONG_CJK}", "${LONG_CJK}")\nt = TextContent("${ONE_CHAR}")`,
    bare: `root = Page([t])\nt = TextContent("body")`,
    empty: `root = Page([])`,
    absentWhenBare: [".vgb-page-head", ".vgb-page-title", ".vgb-page-subtitle", ".vgb-page-meta"],
  },
  {
    name: "PageHeader",
    slot: "vgb-page-header",
    long: `root = PageHeader("${LONG_CJK}", "${LONG_CJK}", "${LONG_CJK}")`,
    bare: `root = PageHeader("${ONE_CHAR}")`,
    empty: `root = PageHeader("")`,
    absentWhenBare: [".vgb-page-subtitle", ".vgb-page-meta", ".vgb-page-head-slot"],
  },
  {
    name: "PageFooter",
    slot: "vgb-page-footer",
    long: `root = PageFooter([], "${LONG_CJK}")`,
    bare: `root = PageFooter([], "${ONE_CHAR}")`,
    empty: `root = PageFooter([])`,
    absentWhenBare: [".vgb-page-foot-slot"],
  },
  {
    name: "Inline",
    slot: "vgb-inline",
    long: `root = Inline([t])\nt = TextContent("${LONG_CJK}")`,
    bare: `root = Inline([t])\nt = TextContent("${ONE_CHAR}")`,
    empty: `root = Inline([])`,
  },
  {
    name: "Cluster",
    slot: "vgb-cluster",
    long: `root = Cluster([t])\nt = Tag("${LONG_CJK}")`,
    bare: `root = Cluster([t])\nt = Tag("${ONE_CHAR}")`,
    empty: `root = Cluster([])`,
  },
  {
    name: "DashboardGrid",
    slot: "vgb-dashboard-grid",
    long: `root = DashboardGrid([t])\nt = TextContent("${LONG_CJK}")`,
    bare: `root = DashboardGrid([t])\nt = TextContent("${ONE_CHAR}")`,
    empty: `root = DashboardGrid([])`,
  },
  {
    name: "Divider",
    slot: "vgb-divider",
    long: `root = Divider("${LONG_CJK}")`,
    bare: `root = Divider()`,
    empty: `root = Divider()`,
    keepsEmptyFrame: true,
    absentWhenBare: [".vgb-divider-label"],
  },
  {
    name: "Spacer",
    slot: "vgb-spacer",
    long: `root = Spacer("large")`,
    bare: `root = Spacer()`,
    empty: `root = Spacer()`,
    keepsEmptyFrame: true,
    holdsNoText: true,
  },
  {
    name: "AspectRatio",
    slot: "vgb-aspect-ratio",
    long: `root = AspectRatio([t])\nt = TextContent("${LONG_CJK}")`,
    bare: `root = AspectRatio([t])\nt = TextContent("${ONE_CHAR}")`,
    empty: `root = AspectRatio([])`,
  },
  {
    name: "ScrollArea",
    slot: "vgb-scroll-area",
    long: `root = ScrollArea([t])\nt = TextContent("${LONG_CJK}")`,
    bare: `root = ScrollArea([t])\nt = TextContent("${ONE_CHAR}")`,
    empty: `root = ScrollArea([])`,
  },
  {
    name: "Collapsible",
    slot: "vgb-collapsible",
    long: `root = Collapsible([t], "${LONG_CJK}")\nt = TextContent("${ONE_CHAR}")`,
    bare: `root = Collapsible([t], "${ONE_CHAR}")\nt = TextContent("body")`,
    empty: `root = Collapsible([], "")`,
  },
];

describe.each(FIT_CASES)("$name fits its content", (testCase) => {
  it("survives a heading far longer than the design assumed", () => {
    const { container } = renderLang(testCase.long);
    expect(container.querySelector(`[data-slot="${testCase.slot}"]`)).not.toBeNull();
    // CJK is one unbroken run with no spaces: a block that only breaks at word
    // boundaries pushes the whole column sideways instead of wrapping. Spacer
    // is the one block with nothing to fit — it holds no text by design.
    if (!testCase.holdsNoText) expect(container.textContent).toContain(LONG_CJK);
  });

  it("leaves no gap where an omitted optional prop would have been", () => {
    const { container } = renderLang(testCase.bare);
    const root = container.querySelector(`[data-slot="${testCase.slot}"]`);
    expect(root, `${testCase.name} did not render its shortest call`).not.toBeNull();
    for (const selector of testCase.absentWhenBare ?? []) {
      // An element rendered for an absent prop still costs its line-height and
      // the gap above it — which is how a heading area ends up with a hole in
      // it for something nobody set.
      expect(container.querySelector(selector), `${selector} rendered empty`).toBeNull();
    }
  });

  it("renders nothing rather than an empty padded frame", () => {
    const { container } = renderLang(testCase.empty);
    const root = container.querySelector(`[data-slot="${testCase.slot}"]`);
    if (testCase.keepsEmptyFrame) {
      // A rule and a gap *are* their empty state; they carry no content by
      // definition.
      expect(root).not.toBeNull();
    } else {
      expect(root, `${testCase.name} painted a frame around nothing`).toBeNull();
    }
  });
});

describe("layout blocks tolerate the props a model actually sends", () => {
  it("treats null and whitespace text as absent", () => {
    // Props reach a renderer unvalidated — the schema teaches the prompt, it
    // does not gate the render — so `null` and `""` both arrive in practice and
    // both have to read as "not set" rather than as an empty line.
    const Component = Page.component;
    const { container } = render(
      <Component
        props={{
          children: [],
          title: "Kept",
          subtitle: null as unknown as string,
          meta: "   ",
        }}
        renderNode={() => null}
      />,
    );
    expect(screen.getByText("Kept")).toBeTruthy();
    expect(container.querySelector(".vgb-page-subtitle")).toBeNull();
    expect(container.querySelector(".vgb-page-meta")).toBeNull();
  });

  it("accepts a single child where an array was declared", () => {
    const Component = Inline.component;
    render(
      <Component
        props={{ children: "solo" as unknown as unknown[] }}
        renderNode={(value) => <span>{String(value)}</span>}
      />,
    );
    expect(screen.getByText("solo")).toBeTruthy();
  });
});

describe("layout schemas bind positionally in the order a human would write", () => {
  it("declares every required prop before the first optional one", () => {
    // OpenUI Lang binds arguments positionally in zod key order, so a required
    // prop declared after an optional one cannot be reached by the shortest
    // call that supplies it — the argument lands on the optional prop instead,
    // silently, and the block renders empty.
    type ZodField = { safeParse: (value: unknown) => { success: boolean } };
    const offenders: string[] = [];
    for (const block of layoutBlocks) {
      const shape = (block.props as unknown as { shape?: Record<string, ZodField> }).shape ?? {};
      let seenOptional: string | null = null;
      for (const [key, field] of Object.entries(shape)) {
        if (field.safeParse(undefined).success) seenOptional ??= key;
        else if (seenOptional) {
          offenders.push(`${block.name}: required "${key}" after optional "${seenOptional}"`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("never shadows an OpenUI component", () => {
    // Blocks merge after OpenUI's own components, so a shared name silently
    // replaces theirs for every document — `Layout`, `Col`, `Content` and
    // `Separator` are all names this family was tempted by.
    const openuiNames = new Set(Object.keys(openuiLibrary.components));
    const shadowed = layoutBlocks.map((c) => c.name).filter((n) => openuiNames.has(n));
    expect(shadowed).toEqual([]);
  });

  it("describes every block for the model", () => {
    const thin = layoutBlocks
      .filter((c) => (c.description ?? "").trim().length < 40)
      .map((c) => c.name);
    expect(thin).toEqual([]);
  });
});

describe("layout.css", () => {
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), "styles", "layout.css"),
    "utf8",
  ).replace(/\/\*[\s\S]*?\*\//g, "");

  it("makes every wrapping container its own query container", () => {
    // A query resolved against an outer root measures the whole document, so a
    // child in a half-width column is told it has the full width — it never
    // reaches a breakpoint and keeps a floor wider than the column it sits in.
    // The rule has to be on the block that places children.
    const offenders: string[] = [];
    for (const selector of [
      ".vgb-page",
      ".vgb-page-head",
      ".vgb-page-foot",
      ".vgb-inline",
      ".vgb-cluster",
      ".vgb-grid",
      ".vgb-aspect",
      ".vgb-scroll",
      ".vgb-collapsible",
    ]) {
      const rule = new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`).exec(css)?.[1] ?? "";
      if (!/container-type:\s*inline-size/.test(rule)) offenders.push(`${selector}: no container-type`);
      if (!/container-name:\s*vgb/.test(rule)) offenders.push(`${selector}: no container-name`);
    }
    expect(offenders).toEqual([]);
  });

  it("writes every width floor as min(<w>, 100%)", () => {
    // A floor wider than its container does not shrink the container, it
    // overflows and paints over the neighbour.
    const offenders = [...css.matchAll(/(?:min-width|flex(?:-basis)?):\s*([^;]+);/g)]
      .map((m) => (m[1] ?? "").trim())
      .filter((value) => /\b\d+(?:\.\d+)?(?:px|rem|em)\b/.test(value) && !value.includes("min("));
    expect(offenders).toEqual([]);
  });
});
