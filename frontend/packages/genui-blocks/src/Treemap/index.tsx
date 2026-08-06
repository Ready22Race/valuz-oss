"use client";

import { defineComponent } from "@openuidev/react-lang";

import { formatShare, formatValue, readItems, readLabel, toneTint } from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import type { Tone } from "../lib/schema";
import { ToneSchema } from "../lib/schema";
import { TreemapSchema } from "./schema";

export { TreemapItemSchema, TreemapSchema } from "./schema";

/**
 * Slice cap.
 *
 * A treemap of sixty rectangles is not a chart, it is a texture: past a dozen
 * the tiles are smaller than their own labels and the areas stop being
 * comparable by eye. Everything below the top 11 is merged into one honest
 * "other" slice, which keeps the areas summing to the whole.
 */
const MAX_SLICES = 12;

/** Below this share a tile has no room for text, so it carries none. */
const LABEL_THRESHOLD = 0.06;

interface Slice {
  label: string;
  value: number;
  tone: Tone;
}

/**
 * Rows of slices, laid out largest first.
 *
 * Not a squarified treemap — a real one is a lot of code for a picture this
 * small. Slices are packed into `rowCount` bands whose heights are their own
 * share of the total, and within a band each tile grows in proportion to its
 * value. Every area is therefore exactly right; only the aspect ratios are
 * less even than a squarified layout would make them.
 */
function packRows(slices: Slice[], rowCount: number): Slice[][] {
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  const rows: Slice[][] = [];
  let row: Slice[] = [];
  let cumulative = 0;
  for (const slice of slices) {
    row.push(slice);
    cumulative += slice.value;
    const boundary = (total * (rows.length + 1)) / rowCount;
    if (rows.length < rowCount - 1 && cumulative >= boundary) {
      rows.push(row);
      row = [];
    }
  }
  if (row.length > 0) rows.push(row);
  return rows;
}

export const Treemap = defineComponent({
  name: "Treemap",
  props: TreemapSchema,
  description:
    "Part-to-whole as area: one flat level of rectangles, each sized by its share of the total. " +
    "items is {label, value, tone} — value must be a positive magnitude in one unit (a weight, a revenue, a headcount), never a percentage of something else and never a negative. " +
    "At most 12 slices are drawn; the remainder is merged into a single \"other\" tile, so the areas always sum to the whole. " +
    "unit names what the values measure (\"USD m\", \"employees\"). Use it when the composition is the point and there are more categories than a PieChart can carry; use MiniCardBlock instead when the reader needs the exact figures.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const parsed = readItems(raw.items ?? raw.data ?? raw.slices).map((record) => ({
      label: readLabel(record),
      value: readLooseNumber(record.value ?? record.amount ?? record.size ?? record.weight),
      tone: ToneSchema.safeParse(record.tone).data,
    }));
    // Area cannot represent a negative or a zero. Dropping them is the only
    // honest option, and the footnote says how many went.
    const sorted: Slice[] = parsed
      .flatMap((item) =>
        item.value !== undefined && item.value > 0
          ? [{ label: item.label, value: item.value, tone: item.tone ?? ("brand" as Tone) }]
          : [],
      )
      .sort((a, b) => b.value - a.value);
    if (sorted.length === 0) return null;
    const dropped = parsed.length - sorted.length;
    const slices: Slice[] =
      sorted.length > MAX_SLICES
        ? [
            ...sorted.slice(0, MAX_SLICES - 1),
            {
              label: "other",
              value: sorted
                .slice(MAX_SLICES - 1)
                .reduce((sum, slice) => sum + slice.value, 0),
              tone: "neutral" as Tone,
            },
          ]
        : sorted;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);
    const total = slices.reduce((sum, slice) => sum + slice.value, 0);
    const largest = slices[0]?.value ?? 0;
    const rows = packRows(slices, Math.max(1, Math.round(Math.sqrt(slices.length))));

    // Every slice is named here, with its share. That is what lets the tiles
    // ellipsis their labels without losing anything: the full list is one
    // sentence away and the cap keeps it to twelve entries.
    const summary =
      `Treemap${title ? ` of ${title}` : ""}: ${slices.length} slices totalling ` +
      `${formatValue(total)}${unit ? ` ${unit}` : ""} — ` +
      `${slices
        .map((slice) => `${slice.label || "unlabelled"} ${formatShare(slice.value / total)}`)
        .join(", ")}.`;

    const footnote =
      sorted.length > MAX_SLICES || dropped > 0 ? (
        <span>
          {[
            sorted.length > MAX_SLICES
              ? `${sorted.length - MAX_SLICES + 1} smaller slices merged into "other".`
              : "",
            dropped > 0 ? `${dropped} items without a positive value were not drawn.` : "",
          ]
            .filter(Boolean)
            .join(" ")}
        </span>
      ) : null;

    return (
      <ChartFrame footnote={footnote} slot="treemap" summary={summary} title={title} unit={unit}>
        <div className="vgb-treemap" data-a2ui-treemap>
          {rows.map((row, rowIndex) => {
            const rowValue = row.reduce((sum, slice) => sum + slice.value, 0);
            return (
              <div
                className="vgb-treemap-row"
                key={rowIndex}
                style={{ flexGrow: rowValue / total }}
              >
                {row.map((slice, index) => {
                  const share = slice.value / total;
                  return (
                    <div
                      className="vgb-treemap-tile"
                      key={`${slice.label}-${index}`}
                      style={{
                        // One hue, stepped light to dark by value: the area is
                        // the measure, and a per-slice rainbow would claim the
                        // colours meant something they do not.
                        backgroundColor: toneTint(
                          slice.tone,
                          15 + (largest > 0 ? slice.value / largest : 0) * 40,
                        ),
                        flexGrow: slice.value,
                      }}
                    >
                      {share >= LABEL_THRESHOLD ? (
                        <>
                          <span className="vgb-treemap-label">{slice.label}</span>
                          <span className="vgb-treemap-value">{formatShare(share)}</span>
                        </>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </ChartFrame>
    );
  },
});
