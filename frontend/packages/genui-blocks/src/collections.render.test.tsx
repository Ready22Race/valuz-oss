import { Renderer, createLibrary } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BlockComponent } from "./blocks";
import { ComparisonTable, DiffView } from "./ComparisonTable";
import { Feed } from "./Feed";
import { Breadcrumb, DescriptionList, Tree } from "./Outline";
import { ProgressList, StatusItem, StatusList } from "./StatusList";
import { ActivityFeed, ActivityItem, Timeline, TimelineItem } from "./Timeline";

/**
 * The data-collection families through the real parser.
 *
 * The library is composed here rather than through `createValuzLibrary()`
 * because registration in `blocks.ts` is assembled centrally; swap this for
 * `createValuzLibrary()` once these thirteen names are listed there.
 *
 * What the detour cannot skip is the point of the file. Every call below is
 * positional, which is the only way to catch a schema whose key order does not
 * match the order the model would write the arguments in — that failure is
 * completely silent, and renders an empty block with no error anywhere.
 */
const collectionBlocks: BlockComponent[] = [
  Timeline,
  TimelineItem,
  ActivityFeed,
  ActivityItem,
  Feed,
  StatusList,
  StatusItem,
  ProgressList,
  ComparisonTable,
  DiffView,
  Tree,
  Breadcrumb,
  DescriptionList,
];

function renderLang(source: string) {
  const library = createLibrary({
    root: openuiLibrary.root ?? "Stack",
    components: [
      ...(Object.values(openuiLibrary.components) as BlockComponent[]),
      ...collectionBlocks,
    ],
  });
  return render(<Renderer library={library} response={source} />);
}

/**
 * A label three times longer than any design assumed, in a script that wraps
 * between any two glyphs. Blocks render model output; this is what arrives.
 */
const LONG_CJK =
  "这是一个非常非常长的中文标签用来验证换行以及容器宽度处理不会导致溢出或者塌陷的情况";

describe("collection blocks bind their positional calls", () => {
  it("binds every block's shortest positional call to the props it reads", () => {
    renderLang(`root = Stack([tl, item, af, ai, fd, sl, si, pl, ct, dv, tr, bc, dl])
tl = Timeline([{ time: "09:30", title: "开盘" }, { time: "15:00", title: "收盘", description: "沪指收涨 0.56%" }])
item = TimelineItem("11:30", "午间休市")
af = ActivityFeed([{ actor: "张伟", action: "审核通过", target: "Q3 预算", time: "09:12" }])
ai = ActivityItem("Ada Lovelace", "published", "the March review", "17:04")
fd = Feed([{ title: "央行宣布降准", body: "释放长期资金约 5000 亿元", time: "今天 08:30", imageUrl: "https://example.com/pboc.png", source: "新华社" }])
sl = StatusList([{ label: "数据抓取", status: "success" }, { label: "建模", status: "running", detail: "第 2 轮" }])
si = StatusItem("交割校验", "blocked", "等待对手方确认")
pl = ProgressList([{ label: "清洗", percent: 62, detail: "12 of 20 done" }])
ct = ComparisonTable(["方案 A", "方案 B"], [{ label: "毛利率", values: ["38%", "41%"], unit: "%", better: "high" }])
dv = DiffView([{ label: "标题", before: "旧标题", after: "新标题" }])
tr = Tree([{ label: "研究目录", detail: "12 篇", children: [{ label: "设备材料" }] }])
bc = Breadcrumb([{ label: "研究" }, { label: "行业" }, { label: "半导体", current: true }])
dl = DescriptionList([{ term: "口径", description: "以收盘价为准" }])`);

    for (const text of [
      "开盘",
      "09:30",
      "收盘",
      "沪指收涨 0.56%",
      "午间休市",
      "11:30",
      "张伟",
      "审核通过",
      "Q3 预算",
      "09:12",
      "Ada Lovelace",
      "published",
      "the March review",
      "央行宣布降准",
      "释放长期资金约 5000 亿元",
      "新华社",
      "数据抓取",
      "建模",
      "第 2 轮",
      "交割校验",
      "等待对手方确认",
      "清洗",
      "62%",
      "12 of 20 done",
      "方案 A",
      "毛利率",
      "38%",
      "41%",
      "标题",
      "旧标题",
      "新标题",
      "研究目录",
      "12 篇",
      "设备材料",
      "研究",
      "半导体",
      "口径",
      "以收盘价为准",
    ]) {
      expect(screen.getByText(text), `missing: ${text}`).toBeTruthy();
    }
  });

  it("puts every block on the DOM under its own data-slot", () => {
    const { container } = renderLang(`root = Stack([tl, af, fd, sl, pl, ct, dv, tr, bc, dl])
tl = Timeline([{ time: "09:30", title: "开盘" }])
af = ActivityFeed([{ actor: "张伟", action: "审核通过" }])
fd = Feed([{ title: "央行宣布降准" }])
sl = StatusList([{ label: "数据抓取", status: "success" }])
pl = ProgressList([{ label: "清洗", percent: 62 }])
ct = ComparisonTable(["A", "B"], [{ label: "毛利率", values: ["38%", "41%"] }])
dv = DiffView([{ label: "标题", before: "旧", after: "新" }])
tr = Tree([{ label: "研究" }])
bc = Breadcrumb([{ label: "研究" }])
dl = DescriptionList([{ term: "口径", description: "收盘价" }])`);

    for (const slot of [
      "vgb-timeline",
      "vgb-timeline-item",
      "vgb-activity-feed",
      "vgb-activity-item",
      "vgb-feed",
      "vgb-feed-item",
      "vgb-status-list",
      "vgb-status-item",
      "vgb-progress-list",
      "vgb-progress-item",
      "vgb-comparison-table",
      "vgb-diff-view",
      "vgb-diff-item",
      "vgb-tree",
      "vgb-tree-item",
      "vgb-breadcrumb",
      "vgb-description-list",
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
 * character, and a list of nothing at all. None of them may throw, and none may
 * leave an empty frame behind — a frame with no rows reads as data that failed
 * to load rather than as an answer with nothing to say.
 */
const COLLECTION_CASES = [
  {
    empty: `root = Timeline([])`,
    long: `root = Timeline([{ time: "09:30", title: "${LONG_CJK}" }])`,
    name: "Timeline",
    short: `root = Timeline([{ time: "9", title: "开", description: "" }])`,
    slot: "vgb-timeline",
  },
  {
    empty: `root = ActivityFeed([])`,
    long: `root = ActivityFeed([{ actor: "张伟", action: "${LONG_CJK}" }])`,
    name: "ActivityFeed",
    short: `root = ActivityFeed([{ actor: "李", action: "改", target: "" }])`,
    slot: "vgb-activity-feed",
  },
  {
    empty: `root = Feed([])`,
    long: `root = Feed([{ title: "${LONG_CJK}" }])`,
    name: "Feed",
    short: `root = Feed([{ title: "新", body: "", imageUrl: "" }])`,
    slot: "vgb-feed",
  },
  {
    empty: `root = StatusList([])`,
    long: `root = StatusList([{ label: "${LONG_CJK}", status: "running" }])`,
    name: "StatusList",
    short: `root = StatusList([{ label: "建", status: "pending", detail: "" }])`,
    slot: "vgb-status-list",
  },
  {
    empty: `root = ProgressList([])`,
    long: `root = ProgressList([{ label: "${LONG_CJK}", percent: 62 }])`,
    name: "ProgressList",
    short: `root = ProgressList([{ label: "清", percent: 0, detail: "" }])`,
    slot: "vgb-progress-list",
  },
  {
    empty: `root = ComparisonTable([], [])`,
    long: `root = ComparisonTable(["甲"], [{ label: "${LONG_CJK}", values: ["38%"] }])`,
    name: "ComparisonTable",
    short: `root = ComparisonTable(["甲"], [{ label: "率", values: ["1"], unit: "" }])`,
    slot: "vgb-comparison-table",
  },
  {
    empty: `root = DiffView([])`,
    long: `root = DiffView([{ label: "${LONG_CJK}", before: "旧", after: "新" }])`,
    name: "DiffView",
    short: `root = DiffView([{ label: "名", before: "", after: "新" }])`,
    slot: "vgb-diff-view",
  },
  {
    empty: `root = Tree([])`,
    long: `root = Tree([{ label: "${LONG_CJK}" }])`,
    name: "Tree",
    short: `root = Tree([{ label: "根", detail: "" }])`,
    slot: "vgb-tree",
  },
  {
    empty: `root = Breadcrumb([])`,
    long: `root = Breadcrumb([{ label: "${LONG_CJK}" }])`,
    name: "Breadcrumb",
    short: `root = Breadcrumb([{ label: "根" }])`,
    slot: "vgb-breadcrumb",
  },
  {
    empty: `root = DescriptionList([])`,
    long: `root = DescriptionList([{ term: "${LONG_CJK}", description: "以收盘价为准" }])`,
    name: "DescriptionList",
    short: `root = DescriptionList([{ term: "率", description: "" }])`,
    slot: "vgb-description-list",
  },
] as const;

describe("collection blocks survive the content they are given", () => {
  it.each(COLLECTION_CASES)(
    "$name renders a long CJK label with every optional field missing",
    ({ long, slot }) => {
      const { container } = renderLang(long);
      expect(container.querySelector(`[data-slot="${slot}"]`)).not.toBeNull();
      expect(screen.getByText(LONG_CJK)).toBeTruthy();
    },
  );

  it.each(COLLECTION_CASES)(
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

  it.each(COLLECTION_CASES)(
    "$name renders nothing at all for an empty items array",
    ({ empty, slot }) => {
      const { container } = renderLang(empty);
      expect(container.querySelector(`[data-slot="${slot}"]`)).toBeNull();
    },
  );

  it("renders one row and fifty rows the same way", () => {
    const rows = Array.from(
      { length: 50 },
      (_, index) => `{ label: "任务 ${index}", percent: ${index * 2} }`,
    ).join(", ");
    const { container } = renderLang(`root = ProgressList([${rows}])`);
    expect(container.querySelectorAll('[data-slot="vgb-progress-item"]')).toHaveLength(
      50,
    );

    const single = renderLang(`root = ProgressList([{ label: "任务 0", percent: 0 }])`);
    expect(
      single.container.querySelectorAll('[data-slot="vgb-progress-item"]'),
    ).toHaveLength(1);
  });

  it("keeps every figure and timestamp unbreakable", () => {
    // The host stylesheet sets `overflow-wrap: anywhere` on every span in
    // scope, which is right for prose and wrong for a value: "26,58 / 4" reads
    // as a different number, not a squeezed one. The classes below are the ones
    // that carry a figure, and the stylesheet pins each of them.
    const { container } = renderLang(`root = Stack([tl, af, pl])
tl = Timeline([{ time: "2025-03-14 09:30", title: "开盘" }])
af = ActivityFeed([{ actor: "张伟", action: "审核", target: "预算", time: "09:12:45" }])
pl = ProgressList([{ label: "清洗", percent: 62.5 }])`);

    for (const selector of [
      ".vgb-timeline-time",
      ".vgb-activity-time",
      ".vgb-progress-percent",
    ]) {
      expect(container.querySelector(selector), `missing: ${selector}`).not.toBeNull();
    }
    expect(screen.getByText("62.5%")).toBeTruthy();
  });
});

describe("collection blocks stay honest and inert", () => {
  it("marks the winning cell only when the row is actually comparable", () => {
    const { container } = renderLang(`root = Stack([clean, mixed, tied, low])
clean = ComparisonTable(["甲", "乙"], [{ label: "毛利率", values: ["38%", "41%"], better: "high" }])
mixed = ComparisonTable(["甲", "乙"], [{ label: "规模", values: ["$4.2M", "3800000"], better: "high" }])
tied = ComparisonTable(["甲", "乙"], [{ label: "费率", values: ["1.2%", "1.2%"], better: "low" }])
low = ComparisonTable(["甲", "乙"], [{ label: "费率", values: ["1.2%", "0.8%"], better: "low" }])`);

    const tables = container.querySelectorAll('[data-slot="vgb-comparison-table"]');
    expect(tables).toHaveLength(4);

    // "41%" beats "38%" when high wins…
    expect(tables[0]?.querySelector('[data-best="true"]')?.textContent).toBe("41%");
    // …but "$4.2M" against "3800000" is not a comparison this block is entitled
    // to make, and a tie has no winner.
    expect(tables[1]?.querySelector('[data-best="true"]')).toBeNull();
    expect(tables[2]?.querySelector('[data-best="true"]')).toBeNull();
    // `better: "low"` picks the smaller value, not the first one.
    expect(tables[3]?.querySelector('[data-best="true"]')?.textContent).toBe("0.8%");
  });

  it("never re-orders or re-scales what it was given", () => {
    const { container } = renderLang(
      `root = ComparisonTable(["甲", "乙", "丙"], [{ label: "毛利率", values: ["41%", "38%", "0.4"], better: "high" }])`,
    );
    const cells = [...container.querySelectorAll(".vgb-comparison-cell")].map(
      (cell) => cell.textContent,
    );
    // Rows keep the order they arrived in — sorting by the winning column would
    // silently change which comparison the reader is making.
    expect(cells).toEqual(["41%", "38%", "0.4"]);
    // And a bare "0.4" beside two percentages is a different unit, so the row
    // gets no winner rather than a wrong one.
    expect(container.querySelector('[data-best="true"]')).toBeNull();
  });

  it("shows every reading even when there are more than there are columns", () => {
    const { container } = renderLang(
      `root = ComparisonTable(["甲"], [{ label: "毛利率", values: ["38%", "41%"] }])`,
    );
    const cells = [...container.querySelectorAll(".vgb-comparison-cell")].map(
      (cell) => cell.textContent,
    );
    // Dropping the surplus reading would be the one edit this block must never
    // make; it renders under a placeholder heading instead.
    expect(cells).toEqual(["38%", "41%"]);
  });

  it("classifies a diff from the values rather than from a colour word", () => {
    const { container } = renderLang(`root = Stack([add, drop, edit, same])
add = DiffView([{ label: "新增字段", before: "", after: "行业分类" }])
drop = DiffView([{ label: "删除字段", before: "旧口径", after: "" }])
edit = DiffView([{ label: "修改字段", before: "38%", after: "41%" }])
same = DiffView([{ label: "未变字段", before: "同值", after: "同值" }])`);
    const kinds = [...container.querySelectorAll('[data-slot="vgb-diff-item"]')].map(
      (row) => row.getAttribute("data-kind"),
    );
    expect(kinds).toEqual(["added", "removed", "changed", "same"]);
  });

  it("caps the tree at six levels and says so with an ellipsis", () => {
    const deepest = Array.from({ length: 9 }, (_, index) => index)
      .reverse()
      .reduce(
        (inner, index) =>
          inner
            ? `{ label: "L${index}", children: [${inner}] }`
            : `{ label: "L${index}" }`,
        "",
      );
    const { container } = renderLang(`root = Tree([${deepest}])`);
    const rows = [...container.querySelectorAll('[data-slot="vgb-tree-item"]')];
    // Six levels of labels, then one row that says the branch continues. A
    // seventh indent leaves no room for its own label in a chat column.
    expect(rows.map((row) => row.getAttribute("data-depth"))).toEqual([
      "0",
      "1",
      "2",
      "3",
      "4",
      "5",
      "6",
    ]);
    expect(rows[6]?.textContent).toBe("…");
    expect(screen.queryByText("L6")).toBeNull();
  });

  it("treats the last breadcrumb as current when nothing else claims it", () => {
    const { container } = renderLang(
      `root = Breadcrumb([{ label: "研究" }, { label: "行业" }, { label: "半导体" }])`,
    );
    const items = [...container.querySelectorAll(".vgb-breadcrumb-item")];
    expect(items.map((item) => item.getAttribute("data-current"))).toEqual([
      null,
      null,
      "true",
    ]);
  });

  it("reads a bare string as the label the block needs", () => {
    // The model writes `Breadcrumb(["研究", "半导体"])` often enough that
    // dropping the block on a string array would be the more common outcome
    // than rendering it.
    renderLang(`root = Breadcrumb(["研究", "半导体"])`);
    expect(screen.getByText("研究")).toBeTruthy();
    expect(screen.getByText("半导体")).toBeTruthy();
  });

  it("promises no interaction it cannot deliver", () => {
    // These blocks render LLM output and have no handler behind them, so
    // anything that looks clickable is a lie: no buttons, no links, no
    // focusable rows, and no ARIA that claims an expandable or selectable row.
    const { container } = renderLang(`root = Stack([tl, sl, tr, bc, fd])
tl = Timeline([{ time: "09:30", title: "开盘" }])
sl = StatusList([{ label: "数据抓取", status: "running" }])
tr = Tree([{ label: "研究", children: [{ label: "半导体" }] }])
bc = Breadcrumb([{ label: "研究" }, { label: "半导体" }])
fd = Feed([{ title: "央行宣布降准", imageUrl: "https://example.com/a.png" }])`);

    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("[tabindex]")).toBeNull();
    expect(container.querySelector("[onclick]")).toBeNull();
    for (const role of ["button", "link", "treeitem", "progressbar", "checkbox"]) {
      expect(container.querySelector(`[role="${role}"]`), role).toBeNull();
    }
    expect(container.querySelector("[aria-expanded]")).toBeNull();
    expect(container.querySelector("[aria-selected]")).toBeNull();
  });

  it("drops an image URL that is not a plain http(s) address", () => {
    const { container } = renderLang(
      `root = Feed([{ title: "央行宣布降准", imageUrl: "javascript:alert(1)" }])`,
    );
    // The entry still renders; only the URL is refused.
    expect(screen.getByText("央行宣布降准")).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });

  it("declares every required prop before the first optional one", () => {
    // OpenUI Lang binds arguments positionally in zod key order, so a required
    // prop declared after an optional one cannot be reached by the shortest
    // call that supplies it — the argument silently lands on the optional prop
    // instead. Nothing reports this: not the parser, not TypeScript. The block
    // just renders empty.
    type ZodField = { safeParse: (value: unknown) => { success: boolean } };
    const offenders: string[] = [];
    for (const block of collectionBlocks) {
      const shape = (block.props as unknown as { shape?: Record<string, ZodField> })
        .shape;
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

  it("gives the model a description worth reading", () => {
    // `description` is prompt text, not documentation: a thin one is a block the
    // model reaches for at the wrong moment.
    const thin = collectionBlocks
      .filter((block) => (block.description ?? "").trim().length < 40)
      .map((block) => block.name);
    expect(thin).toEqual([]);
  });
});
