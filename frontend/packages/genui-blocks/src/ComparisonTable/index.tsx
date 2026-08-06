"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readItems } from "../lib/collections";
import { readRecord, readText, readTextFromKeys, toArray } from "../lib/props";
import type { Tone } from "../lib/schema";
import { toneSurface, toneText } from "../lib/tone";
import type { Better } from "./schema";
import { ComparisonTableSchema, DiffViewSchema } from "./schema";

export {
  BetterSchema,
  ComparisonRowSchema,
  ComparisonTableSchema,
  DiffItemSchema,
  DiffViewSchema,
} from "./schema";

/*
 * ── 口径 ──────────────────────────────────────────────────────────
 *
 * This block is a measurement, and a measurement that has been quietly
 * adjusted is worse than no measurement. Three rules hold everywhere below:
 *
 *  1. Rows and columns render in the order they arrived. Nothing is sorted,
 *     not even by the winning column — a reader compares against the order
 *     the answer established, and re-ordering silently changes which
 *     comparison is being made.
 *  2. No value is rescaled, rounded, converted or reformatted. What the model
 *     wrote is what appears, unit and all.
 *  3. `better` only ever adds emphasis. It is computed from a parse of the
 *     text, and when that parse is not unambiguous — mixed units, a
 *     non-numeric cell, a tie — nothing is emphasised at all. A wrong winner
 *     is a wrong claim; no winner is merely a plain table.
 */

/** A cell split into the parts that decide whether two cells are comparable. */
interface Measured {
  prefix: string;
  suffix: string;
  value: number;
}

const NUMERIC_CELL = /^\s*([^\d.+-]*)\s*([+-]?\d[\d,]*(?:\.\d+)?)\s*(.*?)\s*$/;

function measure(text: string): Measured | undefined {
  const match = NUMERIC_CELL.exec(text);
  if (!match) return undefined;
  const value = Number((match[2] ?? "").replace(/,/g, ""));
  if (!Number.isFinite(value)) return undefined;
  return {
    prefix: (match[1] ?? "").trim(),
    suffix: (match[3] ?? "").trim(),
    value,
  };
}

/**
 * Index of the winning cell, or undefined when there is no honest winner.
 *
 * Every cell must parse, and every cell must carry the same prefix and suffix:
 * "$4.2M" against "3800000" is not a comparison this block is entitled to make,
 * and "38%" against "0.41" is the same mistake wearing a different hat. A tie
 * has no winner either — emphasising the first of two equal cells invents a
 * ranking the data does not contain.
 */
function findWinner(cells: string[], better: Better | undefined): number | undefined {
  if (!better || cells.length < 2) return undefined;
  const measured: Measured[] = [];
  for (const cell of cells) {
    const parsed = measure(cell);
    if (!parsed) return undefined;
    measured.push(parsed);
  }
  const head = measured[0];
  if (!head) return undefined;
  if (measured.some((m) => m.prefix !== head.prefix || m.suffix !== head.suffix)) {
    return undefined;
  }

  let best = 0;
  let tied = false;
  for (let index = 1; index < measured.length; index += 1) {
    const delta = measured[index].value - measured[best].value;
    if (delta === 0) {
      tied = true;
    } else if (better === "high" ? delta > 0 : delta < 0) {
      best = index;
      tied = false;
    }
  }
  return tied ? undefined : best;
}

interface ComparisonRow {
  better: Better | undefined;
  label: string;
  unit: string;
  values: string[];
}

function readComparisonRow(value: Record<string, unknown>): ComparisonRow {
  const better = readTextFromKeys(value, ["better", "direction", "prefer"])
    .trim()
    .toLowerCase();
  return {
    better: better === "high" || better === "low" ? better : undefined,
    label: readTextFromKeys(value, ["label", "title", "name", "metric"]),
    unit: readTextFromKeys(value, ["unit", "units"]),
    values: toArray(value.values ?? value.cells ?? value.data).map(readText),
  };
}

export const ComparisonTable = defineComponent({
  name: "ComparisonTable",
  props: ComparisonTableSchema,
  description:
    "The same measures taken across 2–4 subjects, one subject per column: two funds, three vendors, four scenarios. " +
    "columns names the subjects in the order they should read; rows is {label, values[], unit?, better?} where values lines up index-for-index with columns — pass an empty string for a cell you do not have rather than shifting the others along. " +
    "Write each value already formatted, with its unit, and keep one unit per row. better says which end of that row wins (\"high\" or \"low\") and only adds emphasis to the winning cell; it is skipped whenever the row mixes units, holds a non-numeric cell, or ties. " +
    "Nothing is sorted or rescaled — rows and columns appear exactly as given. Use DataList when there is only one subject.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const columns = toArray(raw.columns ?? raw.subjects ?? raw.headers).map(readText);
    const rows = readItems(raw.rows ?? raw.items ?? raw.metrics, "label")
      .map(readComparisonRow)
      .filter((row) => row.label || row.values.length);
    // Nothing to show means nothing rendered: an empty frame reads as data that
    // failed to load.
    if (!rows.length) return null;

    // A row carrying more readings than there are named subjects still has to
    // show them — dropping a value would be the one edit this block must never
    // make — so the surplus columns render under a placeholder heading.
    const columnCount = Math.max(
      columns.length,
      ...rows.map((row) => row.values.length),
    );
    const title = readTextFromKeys(raw, ["title", "label"]);
    const note = readTextFromKeys(raw, ["note", "footnote", "source", "caption"]);

    return (
      <section
        className="vgb-collection vgb-comparison"
        data-slot="vgb-comparison-table"
        data-a2ui-component="comparison-table"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        {/* Wide content scrolls inside its own box; the chat column must never
            scroll sideways. */}
        <div className="vgb-scroll-x">
          <table className="vgb-comparison-table">
            <thead>
              <tr>
                <th className="vgb-comparison-corner" scope="col" />
                {Array.from({ length: columnCount }, (_, index) => (
                  <th className="vgb-comparison-head" key={index} scope="col">
                    {columns[index] || "—"}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => {
                const cells = Array.from(
                  { length: columnCount },
                  (_, index) => row.values[index] ?? "",
                );
                const winner = findWinner(cells, row.better);
                return (
                  <tr className="vgb-comparison-row" key={`${row.label}-${rowIndex}`}>
                    <th className="vgb-comparison-label" scope="row">
                      {row.label}
                      {row.unit ? (
                        <span className="vgb-comparison-unit">{row.unit}</span>
                      ) : null}
                    </th>
                    {cells.map((cell, index) => (
                      <td
                        className="vgb-comparison-cell"
                        data-best={index === winner ? "true" : undefined}
                        key={index}
                      >
                        {cell || "—"}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {note ? <p className="vgb-collection-note">{note}</p> : null}
      </section>
    );
  },
});

type DiffKind = "added" | "removed" | "changed" | "same";

/*
 * Diff tones are semantic, not directional: an addition is `success` and a
 * removal is `danger` in every locale. This is deliberately *not* routed
 * through `trendTone()` — that function encodes the Greater China market
 * convention where a rise is red, and a text edit has no market direction to
 * apply it to.
 */
const DIFF_TONE: Record<DiffKind, Tone> = {
  added: "success",
  removed: "danger",
  changed: "info",
  same: "neutral",
};

/** Language-neutral marks, so the block reads the same in any locale. */
const DIFF_GLYPH: Record<DiffKind, string> = {
  added: "+",
  removed: "−",
  changed: "≠",
  same: "=",
};

interface DiffRow {
  after: string;
  before: string;
  kind: DiffKind;
  label: string;
}

function readDiffRow(value: unknown): DiffRow {
  const record = readRecord(value);
  const before = readTextFromKeys(record, ["before", "from", "old", "previous"]);
  const after = readTextFromKeys(record, ["after", "to", "new", "current"]);
  return {
    after,
    before,
    kind:
      !before && after
        ? "added"
        : before && !after
          ? "removed"
          : before === after
            ? "same"
            : "changed",
    label: readTextFromKeys(record, ["label", "title", "name", "field"]),
  };
}

export const DiffView = defineComponent({
  name: "DiffView",
  props: DiffViewSchema,
  description:
    "What changed between two versions of the same set of fields: a revised forecast, an edited paragraph, settings before and after. " +
    "items is {label, before, after} — label names the field, before and after are the two values written out in full. " +
    "Leave before empty for something newly added and after empty for something removed; the block marks each row added, removed, changed or unchanged from that, so never write \"(none)\" or \"N/A\" yourself. " +
    "title names what is being compared. Use ComparisonTable when there are more than two versions.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readItems(raw.items ?? raw.changes ?? raw.diffs, "label")
      .map(readDiffRow)
      .filter((row) => row.label || row.before || row.after);
    if (!rows.length) return null;
    const title = readTextFromKeys(raw, ["title", "label"]);

    return (
      <section
        className="vgb-collection vgb-diff"
        data-slot="vgb-diff-view"
        data-a2ui-component="diff-view"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        <div className="vgb-diff-rows" role="list">
          {rows.map((row, index) => {
            const tone = DIFF_TONE[row.kind];
            return (
              <div
                className="vgb-diff-row"
                data-kind={row.kind}
                data-slot="vgb-diff-item"
                key={`${row.label}-${index}`}
                role="listitem"
              >
                <span className="vgb-diff-label">{row.label}</span>
                <span className="vgb-diff-values">
                  <span className="vgb-diff-before">{row.before || "—"}</span>
                  <span aria-hidden="true" className="vgb-diff-arrow">
                    →
                  </span>
                  <span className="vgb-diff-after" style={{ color: toneText(tone) }}>
                    {row.after || "—"}
                  </span>
                </span>
                {/* The mark carries the kind on its own, so the row still reads
                    where colour does not survive — print, a monochrome theme, a
                    reader with low colour vision. */}
                <span
                  className="vgb-diff-mark"
                  style={{ backgroundColor: toneSurface(tone), color: toneText(tone) }}
                >
                  {DIFF_GLYPH[row.kind]}
                </span>
              </div>
            );
          })}
        </div>
      </section>
    );
  },
});
