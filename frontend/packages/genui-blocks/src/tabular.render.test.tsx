import { Renderer } from "@openuidev/react-lang";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Calendar } from "./Calendar";
import { DataGrid } from "./DataGrid";
import { EventStrip } from "./EventStrip";
import { Kanban } from "./Kanban";
import { PivotTable } from "./PivotTable";
import type { BlockComponent } from "./blocks";
import { createValuzLibrary } from "./library";

/**
 * The data-collection views through the real library and the real parser.
 *
 * **These specs are expected to fail until the five blocks are listed in
 * `src/blocks.ts`.** Registration is assembled centrally, so `createValuzLibrary()`
 * does not know these names yet and the parser reports `unknown-component` for
 * every one of them. That is the only reason they are red; composing a private
 * library here to go green would test a library no host ever builds, and would
 * hide the one failure this file exists to catch — a block that renders in a
 * hand-made registry and vanishes from the real one.
 *
 * Every call below is positional, which is the only way to catch a schema whose
 * key order does not match the order the model writes its arguments in. That
 * failure is completely silent: no parse error, no type error, just an empty
 * block.
 */
function renderLang(source: string) {
  return render(<Renderer library={createValuzLibrary()} response={source} />);
}

const tabularBlocks: BlockComponent[] = [DataGrid, PivotTable, Calendar, EventStrip, Kanban];

/**
 * A label three times longer than any design assumed, in a script that wraps
 * between any two glyphs. Blocks render model output; this is what arrives.
 */
const LONG_CJK =
  "这是一个非常非常长的中文标签用来验证换行以及容器宽度处理不会导致溢出或者塌陷的情况";

describe("tabular blocks bind their positional calls", () => {
  it("binds every block's shortest positional call to the props it reads", () => {
    renderLang(`root = Stack([dg, pv, cal, es, kb])
dg = DataGrid([{ label: "标的", emphasis: true }, { label: "收盘价", unit: "CNY", align: "right" }], [["贵州茅台", "1,682.00"], ["宁德时代", "241.36"]], "重仓股", "收盘价, descending", "A 股仅限")
pv = PivotTable("区域", "季度", ["Q1", "Q2"], [{ label: "华东", values: [120, 140] }, { label: "华南", values: [80, 90] }], [260, 170], [200, 230], 430, "USD m", "分区域收入")
cal = Calendar("2026-08", [{ date: "2026-08-14", label: "中报", tone: "info" }, { date: "2026-08-28", label: "分红除权" }], "mon", "2026-08-14", "八月日程")
es = EventStrip("2026-01-01", "2026-12-31", [{ at: "2026-03-14", label: "年报发布" }, { at: "2026-09-01", label: "增发获批", tone: "warning" }], "trading days", "全年事件")
kb = Kanban([{ label: "待办", items: [{ title: "尽调纪要", meta: "张伟" }], limit: 3 }, { label: "进行中", items: [{ title: "估值模型", meta: "due 周五", tone: "warning" }] }], "研究看板")`);

    for (const text of [
      "重仓股",
      "标的",
      "收盘价",
      "贵州茅台",
      "1,682.00",
      "宁德时代",
      "241.36",
      "分区域收入",
      "区域",
      "季度",
      "华东",
      "120",
      "华南",
      "八月日程",
      "中报",
      "分红除权",
      "全年事件",
      "年报发布",
      "增发获批",
      "研究看板",
      "待办",
      "尽调纪要",
      "张伟",
      "估值模型",
    ]) {
      expect(screen.getByText(text), `missing: ${text}`).toBeTruthy();
    }
  });

  it("puts every block on the DOM under its own data-slot", () => {
    const { container } = renderLang(`root = Stack([dg, pv, cal, es, kb])
dg = DataGrid([{ label: "标的" }], [["贵州茅台"]])
pv = PivotTable("区域", "季度", ["Q1"], [{ label: "华东", values: [120] }])
cal = Calendar("2026-08", [{ date: "2026-08-14", label: "中报" }])
es = EventStrip("2026-01-01", "2026-12-31", [{ at: "2026-03-14", label: "年报" }])
kb = Kanban([{ label: "待办", items: [{ title: "尽调纪要" }] }])`);

    for (const slot of [
      "vgb-data-grid",
      "vgb-pivot-table",
      "vgb-calendar",
      "vgb-calendar-day",
      "vgb-event-strip",
      "vgb-event-strip-item",
      "vgb-kanban",
      "vgb-kanban-column",
      "vgb-kanban-card",
    ]) {
      expect(
        container.querySelector(`[data-slot="${slot}"]`),
        `missing: ${slot}`,
      ).not.toBeNull();
    }
  });
});

/**
 * Content fitting. Model output is unpredictable in ways designed content is
 * not, and each of these is a payload that has really arrived: an optional
 * field left out, one that came back as an empty string, a label of one
 * character, and a list of nothing at all. None may throw, and none may leave
 * an empty frame behind — a frame with no rows reads as data that failed to
 * load rather than as an answer with nothing to say.
 */
const TABULAR_CASES = [
  {
    empty: `root = DataGrid([{ label: "标的" }], [])`,
    long: `root = DataGrid([{ label: "${LONG_CJK}" }, { label: "Trailing twelve-month free cash flow yield", unit: "%" }], [["${LONG_CJK}", "4.2"]])`,
    name: "DataGrid",
    short: `root = DataGrid([{ label: "率", unit: "" }], [["一"]])`,
    slot: "vgb-data-grid",
  },
  {
    empty: `root = PivotTable("区域", "季度", ["Q1"], [])`,
    long: `root = PivotTable("${LONG_CJK}", "季度", ["Q1"], [{ label: "${LONG_CJK}", values: [120] }])`,
    name: "PivotTable",
    short: `root = PivotTable("区", "季", ["1"], [{ label: "东", values: [1] }])`,
    slot: "vgb-pivot-table",
  },
  {
    empty: `root = Calendar("2026-08", [])`,
    long: `root = Calendar("2026-08", [{ date: "2026-08-14", label: "${LONG_CJK}" }])`,
    name: "Calendar",
    short: `root = Calendar("2026-08", [{ date: "1", label: "开" }])`,
    slot: "vgb-calendar",
  },
  {
    empty: `root = EventStrip("2026-01-01", "2026-12-31", [])`,
    long: `root = EventStrip("2026-01-01", "2026-12-31", [{ at: "2026-03-14", label: "${LONG_CJK}" }])`,
    name: "EventStrip",
    short: `root = EventStrip("1", "9", [{ at: "5", label: "中", tone: "info" }])`,
    slot: "vgb-event-strip",
  },
  {
    empty: `root = Kanban([])`,
    long: `root = Kanban([{ label: "${LONG_CJK}", items: [{ title: "${LONG_CJK}" }] }])`,
    name: "Kanban",
    short: `root = Kanban([{ label: "待", items: [{ title: "尽", meta: "" }] }])`,
    slot: "vgb-kanban",
  },
] as const;

describe("tabular blocks survive the content they are given", () => {
  it.each(TABULAR_CASES)(
    "$name renders a long CJK label with every optional field missing",
    ({ long, slot }) => {
      const { container } = renderLang(long);
      expect(container.querySelector(`[data-slot="${slot}"]`)).not.toBeNull();
      expect(screen.getAllByText(LONG_CJK).length).toBeGreaterThan(0);
    },
  );

  it.each(TABULAR_CASES)(
    "$name renders a one-character label and empty-string optionals",
    ({ short, slot }) => {
      const { container } = renderLang(short);
      const root = container.querySelector(`[data-slot="${slot}"]`);
      expect(root).not.toBeNull();
      // An empty string is not a value: it must leave no element behind, or the
      // block grows a blank line that reads as missing data.
      expect(root?.textContent?.trim().length).toBeGreaterThan(0);
    },
  );

  it.each(TABULAR_CASES)(
    "$name renders nothing at all when it has no entries",
    ({ empty, slot }) => {
      const { container } = renderLang(empty);
      expect(container.querySelector(`[data-slot="${slot}"]`)).toBeNull();
    },
  );

  it("renders one row and fifty rows the same way", () => {
    const rows = Array.from(
      { length: 50 },
      (_, index) => `["标的 ${index}", "${index}.00"]`,
    ).join(", ");
    const { container } = renderLang(
      `root = DataGrid([{ label: "标的" }, { label: "价", align: "right" }], [${rows}])`,
    );
    expect(container.querySelectorAll(".vgb-datagrid-row")).toHaveLength(50);

    const single = renderLang(
      `root = DataGrid([{ label: "标的" }, { label: "价" }], [["标的 0", "0.00"]])`,
    );
    expect(single.container.querySelectorAll(".vgb-datagrid-row")).toHaveLength(1);
  });

  it("renders fifty events and fifty cards without dropping any", () => {
    const events = Array.from(
      { length: 50 },
      (_, index) => `{ at: "${index + 1}", label: "事件 ${index}" }`,
    ).join(", ");
    const strip = renderLang(`root = EventStrip("1", "50", [${events}])`);
    expect(
      strip.container.querySelectorAll('[data-slot="vgb-event-strip-item"]'),
    ).toHaveLength(50);

    const cards = Array.from({ length: 50 }, (_, index) => `{ title: "卡 ${index}" }`).join(
      ", ",
    );
    const board = renderLang(`root = Kanban([{ label: "待办", items: [${cards}] }])`);
    expect(board.container.querySelectorAll('[data-slot="vgb-kanban-card"]')).toHaveLength(
      50,
    );
  });

  it("keeps every figure unbreakable while its label wraps", () => {
    // The host stylesheet sets `overflow-wrap: anywhere` on every span in
    // scope, which is right for prose and wrong for a value: "1,68 / 2.00"
    // reads as a different number, not a squeezed one. The classes below are
    // the ones that carry a figure, and the stylesheet pins each of them.
    const { container } = renderLang(`root = Stack([dg, pv, cal, es, kb])
dg = DataGrid([{ label: "${LONG_CJK}" }, { label: "收盘价" }], [["${LONG_CJK}", "1,682.00"]])
pv = PivotTable("区域", "季度", ["Q1"], [{ label: "${LONG_CJK}", values: ["3,830.84"] }])
cal = Calendar("2026-08", [{ date: "14", label: "${LONG_CJK}" }])
es = EventStrip("2026-01-01", "2026-12-31", [{ at: "2026-03-14", label: "${LONG_CJK}" }])
kb = Kanban([{ label: "${LONG_CJK}", items: [{ title: "${LONG_CJK}" }], limit: 2 }])`);

    for (const selector of [
      ".vgb-datagrid-cell",
      ".vgb-pivot-cell",
      ".vgb-calendar-date",
      ".vgb-strip-at",
      ".vgb-kanban-count",
    ]) {
      expect(container.querySelector(selector), `missing: ${selector}`).not.toBeNull();
    }
    expect(screen.getByText("1,682.00")).toBeTruthy();
    expect(screen.getByText("3,830.84")).toBeTruthy();
  });

  it("fits a container far narrower than comfortable without widening the page", () => {
    // Every wide block has to scroll inside its own box. Nothing about jsdom
    // measures layout, so what is asserted is the containment itself: the wide
    // shapes sit inside `.vgb-scroll-x`, and the board wraps rather than
    // scrolling at all.
    const { container } = renderLang(`root = Stack([dg, pv, cal, kb])
dg = DataGrid([{ label: "标的" }, { label: "价" }], [["贵州茅台", "1,682.00"]])
pv = PivotTable("区域", "季度", ["Q1"], [{ label: "华东", values: [120] }])
cal = Calendar("2026-08", [{ date: "14", label: "中报" }])
kb = Kanban([{ label: "待办", items: [{ title: "尽调" }] }])`);

    for (const slot of ["vgb-data-grid", "vgb-pivot-table", "vgb-calendar"]) {
      const block = container.querySelector(`[data-slot="${slot}"]`);
      expect(block?.querySelector(".vgb-scroll-x"), `not contained: ${slot}`).not.toBeNull();
    }
    expect(
      container.querySelector('[data-slot="vgb-kanban"] .vgb-kanban-columns'),
    ).not.toBeNull();
  });
});

describe("tabular blocks stay honest and inert", () => {
  it("renders no control a reader could try to operate", () => {
    // The governing rule of this family. Each block is the still picture of
    // something normally interactive, and there is no handler behind any of it:
    // a sort arrow, a month chevron, a drag handle or a drop zone would each be
    // a promise nothing can keep.
    const { container } = renderLang(`root = Stack([dg, pv, cal, es, kb])
dg = DataGrid([{ label: "标的" }, { label: "价" }], [["贵州茅台", "1,682.00"]], "重仓", "价, descending", "A 股")
pv = PivotTable("区域", "季度", ["Q1", "Q2"], [{ label: "华东", values: [120, 140] }], [260], [120], 260)
cal = Calendar("2026-08", [{ date: "14", label: "中报" }], "mon", "2026-08-14")
es = EventStrip("2026-01-01", "2026-12-31", [{ at: "2026-03-14", label: "年报" }])
kb = Kanban([{ label: "待办", items: [{ title: "尽调" }], limit: 1 }, { label: "进行中", items: [] }])`);

    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("input")).toBeNull();
    expect(container.querySelector("select")).toBeNull();
    expect(container.querySelector("[tabindex]")).toBeNull();
    expect(container.querySelector("[onclick]")).toBeNull();
    expect(container.querySelector("[draggable]")).toBeNull();
    for (const role of [
      "button",
      "link",
      "checkbox",
      "columnheader",
      "gridcell",
      "slider",
      "spinbutton",
      "combobox",
      "menuitem",
    ]) {
      expect(container.querySelector(`[role="${role}"]`), role).toBeNull();
    }
    for (const attribute of ["aria-sort", "aria-expanded", "aria-selected", "aria-haspopup"]) {
      expect(container.querySelector(`[${attribute}]`), attribute).toBeNull();
    }
  });

  it("states the sort and the filter as facts rather than offering them", () => {
    const { container } = renderLang(
      `root = DataGrid([{ label: "标的" }, { label: "收入" }], [["A", "12"], ["B", "9"]], "重仓", "revenue, descending", "US listings only")`,
    );
    const facts = container.querySelector('[data-slot="vgb-data-grid-facts"]')?.textContent;
    expect(facts).toContain("Sorted by revenue, descending");
    expect(facts).toContain("Filtered to US listings only");
    // The block never acts on either: the rows leave in the order they arrived.
    const names = [...container.querySelectorAll(".vgb-datagrid-name")].map(
      (cell) => cell.textContent,
    );
    expect(names).toEqual(["A", "B"]);
  });

  it("does not stutter when the model writes the verb into the fact", () => {
    const { container } = renderLang(
      `root = DataGrid([{ label: "标的" }], [["A"]], "重仓", "sorted by revenue", "filtered to US")`,
    );
    const facts = container.querySelector('[data-slot="vgb-data-grid-facts"]')?.textContent;
    expect(facts).toContain("Sorted by revenue");
    expect(facts).not.toContain("Sorted by sorted by");
    expect(facts).toContain("Filtered to US");
  });

  it("states the row count when it stops short of every row", () => {
    const rows = Array.from({ length: 140 }, (_, index) => `["行 ${index}", "${index}"]`).join(
      ", ",
    );
    const { container } = renderLang(
      `root = DataGrid([{ label: "标的" }, { label: "值" }], [${rows}])`,
    );
    expect(container.querySelectorAll(".vgb-datagrid-row")).toHaveLength(100);
    expect(
      container.querySelector('[data-slot="vgb-data-grid-facts"]')?.textContent,
    ).toContain("Showing 100 of 140 rows");
  });

  it("flags a total that disagrees with the cells and prints it anyway", () => {
    // 120 + 140 is 260, not 300. The reported figure is what appears — hiding
    // it would hide the disagreement — and the block says so instead of
    // quietly substituting its own arithmetic.
    const { container } = renderLang(
      `root = PivotTable("区域", "季度", ["Q1", "Q2"], [{ label: "华东", values: [120, 140] }, { label: "华南", values: [80, 90] }], [300, 170], [200, 230], 470)`,
    );
    const flagged = [...container.querySelectorAll('[data-mismatch="true"]')].map(
      (cell) => cell.textContent,
    );
    expect(flagged).toContain("300");
    // The row that does add up is not flagged.
    expect(flagged).not.toContain("170");
    expect(
      container.querySelector('[data-slot="vgb-pivot-note"]')?.textContent,
    ).toContain("Does not reconcile");
  });

  it("leaves a total alone when the cells cannot be added at all", () => {
    // A dash is not a zero. With a non-numeric cell in the row there is no sum
    // to compare against, so the total is left plain rather than accused.
    const { container } = renderLang(
      `root = PivotTable("区域", "季度", ["Q1", "Q2"], [{ label: "华东", values: ["120", "—"] }], [999])`,
    );
    expect(container.querySelector('[data-mismatch="true"]')).toBeNull();
    expect(screen.getByText("999")).toBeTruthy();
  });

  it("keeps a calendar to its own month and lists what falls outside it", () => {
    const { container } = renderLang(
      `root = Calendar("2026-08", [{ date: "2026-08-14", label: "中报" }, { date: "2026-09-01", label: "分红" }])`,
    );
    expect(screen.getByText("中报")).toBeTruthy();
    // The September event cannot be placed on an August grid, so it is stated
    // rather than dropped — and it is not drawn as a neighbouring day.
    const outside = container.querySelector('[data-slot="vgb-calendar-outside"]')?.textContent;
    expect(outside).toContain("2026-09-01");
    expect(outside).toContain("分红");
    // August 2026 begins on a Saturday, so a Monday-start grid opens with five
    // blank cells rather than five dates from July.
    const blanks = container.querySelectorAll('[data-outside="true"]');
    expect(blanks.length).toBeGreaterThan(0);
    for (const cell of blanks) expect(cell.textContent).toBe("");
  });

  it("marks no day as today unless it is told which day that is", () => {
    const without = renderLang(
      `root = Calendar("2026-08", [{ date: "14", label: "中报" }])`,
    );
    expect(without.container.querySelector('[data-today="true"]')).toBeNull();

    const with_ = renderLang(
      `root = Calendar("2026-08", [{ date: "14", label: "中报" }], "mon", "2026-08-14")`,
    );
    const today = with_.container.querySelector('[data-today="true"]');
    expect(today?.querySelector(".vgb-calendar-date")?.textContent).toBe("14");
  });

  it("clamps an event outside the range to the edge and counts it", () => {
    const { container } = renderLang(
      `root = EventStrip("2026-01-01", "2026-06-30", [{ at: "2026-03-14", label: "年报" }, { at: "2026-11-02", label: "增发" }])`,
    );
    // Both events are still listed — nothing is dropped for being out of range.
    expect(container.querySelectorAll('[data-slot="vgb-event-strip-item"]')).toHaveLength(2);
    const marks = container.querySelectorAll(".vgb-strip-mark");
    expect(marks).toHaveLength(2);
    expect(marks[1]?.getAttribute("data-outside")).toBe("true");
    expect(
      container.querySelector('[data-slot="vgb-event-strip-note"]')?.textContent,
    ).toContain("drawn at the edge");
  });

  it("says when a column is over its limit and hides nothing", () => {
    const { container } = renderLang(
      `root = Kanban([{ label: "进行中", items: [{ title: "A" }, { title: "B" }, { title: "C" }], limit: 2 }, { label: "完成", items: [] }])`,
    );
    const columns = container.querySelectorAll('[data-slot="vgb-kanban-column"]');
    // The empty column still renders: an empty stage is a fact about the board.
    expect(columns).toHaveLength(2);
    expect(columns[0]?.querySelector(".vgb-kanban-count")?.textContent).toBe("3 / 2");
    expect(columns[0]?.querySelector('[data-slot="vgb-kanban-over"]')?.textContent).toBe(
      "1 over the limit of 2",
    );
    // Over the limit, every card is still shown.
    expect(columns[0]?.querySelectorAll('[data-slot="vgb-kanban-card"]')).toHaveLength(3);
    expect(columns[1]?.querySelector('[data-slot="vgb-kanban-over"]')).toBeNull();
  });

  it("declares every required prop before the first optional one", () => {
    // OpenUI Lang binds arguments positionally in zod key order, so a required
    // prop declared after an optional one cannot be reached by the shortest
    // call that supplies it — the argument silently lands on the optional prop
    // instead. Nothing reports this: not the parser, not TypeScript. The block
    // just renders empty.
    type ZodField = { safeParse: (value: unknown) => { success: boolean } };
    const offenders: string[] = [];
    for (const block of tabularBlocks) {
      const shape = (block.props as unknown as { shape?: Record<string, ZodField> }).shape;
      if (!shape) continue;
      let seenOptional: string | null = null;
      for (const [key, field] of Object.entries(shape)) {
        if (field.safeParse(undefined).success) {
          seenOptional ??= key;
        } else if (seenOptional) {
          offenders.push(
            `${block.name}: required "${key}" declared after optional "${seenOptional}"`,
          );
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("gives no two blocks the same schema object", () => {
    // The library keys registration off the schema, so two `defineComponent`
    // calls handed the same object silently replace one another: one name
    // renders the other's component, with both names still in the library and
    // nothing reported anywhere.
    const schemas = new Set(tabularBlocks.map((block) => block.props));
    expect(schemas.size).toBe(tabularBlocks.length);
  });

  it("gives the model a description worth reading", () => {
    // `description` is prompt text, not documentation: a thin one is a block
    // the model reaches for at the wrong moment.
    const thin = tabularBlocks
      .filter((block) => (block.description ?? "").trim().length < 40)
      .map((block) => block.name);
    expect(thin).toEqual([]);
  });

  it("tells the model in prose that nothing here can be operated", () => {
    // The rule has to survive into the prompt, or the model narrates a control
    // the block does not render: "click a column to re-sort", "use the arrows
    // to change month". Each description says so in its own words.
    for (const block of tabularBlocks) {
      const description = (block.description ?? "").toLowerCase();
      const disclaims =
        description.includes("nothing here") ||
        description.includes("nothing in the block") ||
        description.includes("nothing can be") ||
        description.includes("cannot be") ||
        description.includes("there is no way to") ||
        description.includes("is a picture");
      expect(disclaims, `${block.name} never says it is inert`).toBe(true);
    }
  });
});
