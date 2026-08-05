import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { Renderer, createLibrary } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BlockComponent } from "./blocks";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { Progress } from "./Progress";
import { Result } from "./Result";
import { Skeleton } from "./Skeleton";

/**
 * The feedback-state family through the real parser.
 *
 * The library is composed here rather than through `createValuzLibrary()`
 * because registration in `blocks.ts` is assembled centrally; swap this for
 * `createValuzLibrary()` once these five names are listed there. What the
 * detour cannot skip is the point of the file: every call below is positional,
 * which is the only way to catch a schema whose key order does not match the
 * order the model would write the arguments in — that failure is silent, with
 * no parse error and no type error, just an empty block.
 */
const stateBlocks: BlockComponent[] = [EmptyState, ErrorState, Result, Progress, Skeleton];

/** The slice of a zod field this file needs; avoids depending on zod internals. */
type ZodField = { safeParse: (value: unknown) => { success: boolean } };

const srcDir = dirname(fileURLToPath(import.meta.url));
// Comments are stripped up front: this file's own prose mentions the values it
// asserts on, and a leading comment otherwise lands inside a captured selector.
const statesCss = readFileSync(join(srcDir, "styles/states.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

function renderLang(source: string) {
  const library = createLibrary({
    root: openuiLibrary.root ?? "Stack",
    components: [...(Object.values(openuiLibrary.components) as BlockComponent[]), ...stateBlocks],
  });
  return render(<Renderer library={library} response={source} />);
}

// Long enough to wrap several times, and CJK — which carries no spaces, so a
// stylesheet that relies on word boundaries to break simply does not break it.
const LONG_CJK_TITLE =
  "本季度财报数据管道在最后一个抽取阶段发生中断因此无法为该组合中的任何一只标的生成完整的持仓归因结果";

describe("state family renders through the OpenUI Lang parser", () => {
  it("binds every block's shortest positional call to the props it reads", () => {
    renderLang(`root = Stack([empty, err, ok, bar, bones])
empty = EmptyState("No filings in this period", "Widen the date range to see earlier reports.", "inbox")
err = ErrorState("Could not load the filing", "The provider timed out after 30s.", "TimeoutError: upstream did not respond")
ok = Result("success", "Filing submitted", "The regulator acknowledged receipt.")
bar = Progress(72, "Indexing filings", "1,204 of 1,670 documents")
bones = Skeleton(3, "text")`);

    for (const text of [
      "No filings in this period",
      "Widen the date range to see earlier reports.",
      "Could not load the filing",
      "The provider timed out after 30s.",
      "TimeoutError: upstream did not respond",
      "Filing submitted",
      "The regulator acknowledged receipt.",
      "Indexing filings",
      "1,204 of 1,670 documents",
      "72%",
    ]) {
      expect(screen.getByText(text), `missing: ${text}`).toBeTruthy();
    }
  });

  it("renders each block's root, and Skeleton the number of shapes it was asked for", () => {
    const { container } = renderLang(`root = Stack([empty, err, ok, bar, bones])
empty = EmptyState("Nothing here")
err = ErrorState("Failed")
ok = Result("error", "Rejected")
bar = Progress(0)
bones = Skeleton(4, "circle")`);

    for (const slot of [
      "vgb-empty-state",
      "vgb-error-state",
      "vgb-result",
      "vgb-progress",
      "vgb-skeleton",
    ]) {
      expect(container.querySelector(`[data-slot="${slot}"]`), `missing: ${slot}`).not.toBeNull();
    }
    const skeleton = container.querySelector('[data-slot="vgb-skeleton"]');
    expect(skeleton?.getAttribute("data-variant")).toBe("circle");
    expect(skeleton?.querySelectorAll(".vgb-skeleton-shape").length).toBe(4);
    // Nothing is actually loading behind a block that renders a finished model
    // response, so the placeholder is hidden rather than announced as busy.
    expect(skeleton?.getAttribute("aria-hidden")).toBe("true");
    expect(skeleton?.getAttribute("role")).toBeNull();
  });
});

describe("Progress announces the value it draws", () => {
  it("carries role=progressbar and the aria value attributes", () => {
    // A bare <div> announces nothing at all: no role, no value, no name. The
    // bar is then invisible to a screen reader however carefully it is painted.
    const { container } = renderLang(`root = Progress(72, "Indexing filings")`);
    const bar = container.querySelector('[role="progressbar"]');
    expect(bar, "no element carries role=progressbar").not.toBeNull();
    expect(bar?.getAttribute("aria-valuenow")).toBe("72");
    expect(bar?.getAttribute("aria-valuemin")).toBe("0");
    expect(bar?.getAttribute("aria-valuemax")).toBe("100");
    expect(bar?.getAttribute("aria-valuetext")).toBe("72%");
    expect(bar?.getAttribute("aria-label")).toBe("Indexing filings");
  });

  it("clamps the drawn width and the announced value to the same number", () => {
    // A bar painted full while announcing 240 is worse than either mistake
    // alone: a sighted reader and a screen-reader reader then get different
    // data from one element. Whatever the model sends, one number does both.
    // A bare `Progress()` is not in this list: the parser enforces required
    // fields and drops the whole component before it renders, so the block
    // never sees a missing `percent` under OpenUI Lang. It does under A2UI,
    // which builds its registry from `blockCatalog` and passes props through —
    // hence the last two rows, which are the shapes that actually arrive.
    const cases: [source: string, expected: string][] = [
      [`root = Progress(240, "Over")`, "100"],
      [`root = Progress(-30, "Under")`, "0"],
      [`root = Progress(99.6, "Fractional")`, "100"],
      [`root = Progress("72%", "String")`, "72"],
      [`root = Progress("not a number", "Junk")`, "0"],
      [`root = Progress("", "Empty")`, "0"],
      [`root = Progress({label: "No percent at all"})`, "0"],
    ];

    for (const [source, expected] of cases) {
      const { container, unmount } = renderLang(source);
      const bar = container.querySelector('[role="progressbar"]');
      const fill = container.querySelector(".vgb-progress-fill");
      expect(bar?.getAttribute("aria-valuenow"), source).toBe(expected);
      expect(bar?.getAttribute("aria-valuetext"), source).toBe(`${expected}%`);
      expect((fill as HTMLElement | null)?.style.width, source).toBe(`${expected}%`);
      unmount();
    }
  });
});

describe("state blocks fit whatever the model sends", () => {
  it("renders a long CJK title in every block that takes one", () => {
    const { container } = renderLang(`root = Stack([empty, err, ok, bar])
empty = EmptyState("${LONG_CJK_TITLE}")
err = ErrorState("${LONG_CJK_TITLE}")
ok = Result("warning", "${LONG_CJK_TITLE}")
bar = Progress(50, "${LONG_CJK_TITLE}")`);

    expect(screen.getAllByText(LONG_CJK_TITLE).length).toBe(4);
    // Nothing may be pinned to a width the column cannot honour. A bare floor
    // does not shrink its container, it overflows it and paints over whatever
    // sits beside it — so every floor in this family concedes with `min(…)`.
    for (const [, value] of statesCss.matchAll(/min-width:\s*([^;]+);/g)) {
      const width = value.trim();
      expect(/^(0|min\()/.test(width), `bare min-width floor: ${width}`).toBe(true);
    }
    expect(container.querySelector('[data-slot="vgb-progress"]')).not.toBeNull();
  });

  it("drops every optional row rather than leaving a gap", () => {
    // The renderer does not validate props against the schema, so an omitted
    // optional field arrives as `undefined` and a model that wrote one arrives
    // as `null` or "". All three have to render as an absent row, not a blank
    // line the flex gap still spaces around.
    const { container } = renderLang(`root = Stack([empty, err, ok, bar])
empty = EmptyState("Nothing to show")
err = ErrorState("Failed", "", "")
ok = Result("info", "Done")
bar = Progress(10)`);

    const empty = container.querySelector('[data-slot="vgb-empty-state"]');
    expect(empty?.querySelector(".vgb-state-text")).toBeNull();
    // No icon prop and no icon element: BlockIcon renders nothing for a name it
    // was never given, so the gap above the title collapses with it.
    expect(empty?.querySelector("svg")).toBeNull();
    expect(empty?.textContent).toBe("Nothing to show");

    const err = container.querySelector('[data-slot="vgb-error-state"]');
    expect(err?.querySelector(".vgb-state-text")).toBeNull();
    expect(err?.querySelector('[data-slot="vgb-error-state-detail"]')).toBeNull();

    expect(container.querySelector('[data-slot="vgb-result"] .vgb-state-text')).toBeNull();
    expect(container.querySelector(".vgb-progress-detail")).toBeNull();
    // The figure is the block, so it is never the row that goes missing.
    expect(container.querySelector(".vgb-progress-percent")?.textContent).toBe("10%");
  });

  it("keeps a long stack-like detail inside its own box, selectable", () => {
    // One unbroken token thousands of characters long is the realistic shape of
    // `detail`, and it is exactly what widens a chat column if nothing says
    // otherwise. The rules are asserted rather than the geometry: jsdom has no
    // layout, so the stylesheet is the only place this can be checked.
    const trace = `TimeoutError: upstream did not respond\n    at ${"veryLongFrameName".repeat(20)} (/a/b/c.ts:42:17)`;
    const { container } = renderLang(`root = ErrorState("Failed", "Retry later.", ${JSON.stringify(trace)})`);

    const detail = container.querySelector('[data-slot="vgb-error-state-detail"]');
    expect(detail?.tagName).toBe("PRE");
    expect(detail?.querySelector("code")?.textContent).toBe(trace);

    const rule = statesCss.match(/\.vgb-state-detail\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(rule, "detail must be code type").toContain("var(--openui-font-code)");
    expect(rule, "detail must wrap, not widen the column").toContain("overflow-wrap: anywhere");
    expect(rule, "detail must scroll inside its own box").toContain("overflow: auto");
    // The only reason to show a stack trace is so a human can copy it out.
    expect(rule, "detail must stay selectable").toContain("user-select: text");
  });

  it("caps an absurd Skeleton line count and defaults a junk variant", () => {
    const { container } = renderLang(`root = Stack([huge, junk, negative])
huge = Skeleton(400)
junk = Skeleton(2, "spinner")
negative = Skeleton(-5, "block")`);

    const skeletons = container.querySelectorAll('[data-slot="vgb-skeleton"]');
    expect(skeletons.length).toBe(3);
    expect(skeletons[0]?.querySelectorAll(".vgb-skeleton-shape").length).toBe(12);
    // An unknown variant falls back to text rather than emitting a data-variant
    // no rule matches, which would draw zero-height shapes.
    expect(skeletons[1]?.getAttribute("data-variant")).toBe("text");
    expect(skeletons[2]?.querySelectorAll(".vgb-skeleton-shape").length).toBe(1);
  });

  it("falls back to info for a status outside the enum", () => {
    const { container } = renderLang(`root = Result("catastrophe", "Unknown outcome")`);
    // Not `error`: an unrecognised status is a prompt miss, and colouring one
    // red invents bad news the model never reported.
    expect(container.querySelector('[data-slot="vgb-result"]')?.getAttribute("data-status")).toBe(
      "info",
    );
  });
});

describe("state blocks render output, they never act", () => {
  it("draws no button, link, or other affordance", () => {
    const { container } = renderLang(`root = Stack([empty, err, ok, bar, bones])
empty = EmptyState("No filings", "Nothing matched.", "inbox")
err = ErrorState("Could not load", "The provider timed out.", "TimeoutError: …", "circle-alert")
ok = Result("error", "Rejected", "Two rows failed validation.")
bar = Progress(72, "Indexing", "1,204 of 1,670")
bones = Skeleton(3, "text")`);

    // There is no handler behind any of these blocks — they render a finished
    // model response — so anything that reads as a control is a promise the
    // package cannot keep. `role="progressbar"` is the one role here, and it
    // describes a value, not an action.
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("input")).toBeNull();
    expect(container.querySelector("[tabindex]")).toBeNull();
    expect(container.querySelector("[onclick]")).toBeNull();
    for (const role of ["button", "link", "checkbox", "menuitem", "tab"]) {
      expect(container.querySelector(`[role="${role}"]`), `role=${role}`).toBeNull();
    }
  });

  it("never tells the reader to press something that is not there", () => {
    // `description` is prompt text fed verbatim to the model. If it narrates a
    // retry button, the model narrates one too — in a block that has none.
    const forbidden = /\b(retry|try again|click|press|tap|refresh button)\b/i;
    for (const block of stateBlocks) {
      const description = block.description ?? "";
      expect(description.trim().length, `${block.name}: thin description`).toBeGreaterThan(40);
      for (const sentence of description.split(/(?<=\.)\s+/)) {
        if (!forbidden.test(sentence)) continue;
        // Mentioning retry is fine — required, even — as long as the sentence
        // is denying one exists.
        expect(
          /\b(no|not|never|cannot|nothing|without)\b/i.test(sentence),
          `${block.name}: description implies an action — "${sentence.trim()}"`,
        ).toBe(true);
      }
    }
  });
});

describe("state family stylesheet", () => {
  it("is imported from the package style entry point", () => {
    const entry = readFileSync(join(srcDir, "styles.css"), "utf8");
    expect(entry).toContain('@import "./styles/states.css";');
  });

  it("stops every animation under prefers-reduced-motion", () => {
    // An element animating indefinitely is an accessibility failure, not a
    // preference: for a reader with a vestibular disorder it decides whether
    // the page is usable. So every rule that starts motion must have a
    // counterpart that stops it — checked by finding the movers rather than by
    // naming them, so a new one cannot be added without being disabled.
    const reduced = statesCss.match(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([\s\S]*?)\n\}/,
    )?.[1];
    expect(reduced, "no prefers-reduced-motion block").toBeTruthy();

    const outside = statesCss.replace(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\n\}/,
      "",
    );
    const movers = [...outside.matchAll(/([^{}@]+)\{([^{}]*)\}/g)]
      .filter(([, , body]) => /^\s*(animation|transition):/m.test(body ?? ""))
      .flatMap(([, selector]) => (selector ?? "").split(",").map((s) => s.trim()));
    expect(movers.length, "nothing animates — this test would pass vacuously").toBeGreaterThan(0);

    for (const selector of movers) {
      expect(reduced, `still animates under reduce: ${selector}`).toContain(selector);
    }
    expect(reduced).toContain("animation: none");
    expect(reduced).toContain("transition: none");
  });
});

describe("state family schemas", () => {
  it("declares every required prop before the first optional one", () => {
    // OpenUI Lang binds arguments positionally in zod key order, so a required
    // prop declared after an optional one cannot be reached by the shortest
    // call that supplies it — the argument silently lands on the optional prop
    // instead. Nothing reports this: not the parser, not TypeScript.
    const offenders: string[] = [];
    for (const block of stateBlocks) {
      const shape = (block.props as unknown as { shape?: Record<string, ZodField> }).shape;
      if (!shape) continue;
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

  it("gives every block a name no OpenUI component already uses", () => {
    // Merging puts blocks last, so a block sharing a name with an OpenUI
    // component would silently replace it for every document.
    const openuiNames = new Set(Object.keys(openuiLibrary.components));
    const shadowed = stateBlocks.map((c) => c.name).filter((n) => openuiNames.has(n));
    expect(shadowed).toEqual([]);
  });
});
