"use client";

import { defineComponent } from "@openuidev/react-lang";

import {
  formatValue,
  readCells,
  readItems,
  readLabel,
  toneTint,
} from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import { readText, readTextFromKeys, toArray } from "../lib/props";
import { HeatmapSchema } from "./schema";

export { HeatmapRowSchema, HeatmapSchema } from "./schema";

/**
 * Tint range, in percent of the tone.
 *
 * The floor is not zero: a cell at the bottom of the scale still has to look
 * like a cell that was measured rather than a cell with no data. The ceiling is
 * not 100 either — the value is printed on top of the fill, and dark ink on a
 * full-strength tint fails contrast in light mode while pale ink fails in dark.
 */
const TINT_FLOOR = 8;
const TINT_CEILING = 70;

/** Height of one data row, in the CSS grid that lines up the row headers with
 *  the svg matrix beside them. */
const ROW_HEIGHT = "2.25rem";

/** Percent of a cell's own box the fill inset leaves as gutter to its
 *  neighbours — the svg equivalent of the old table's `border-spacing`. */
const CELL_GUTTER_PCT = 3;

export const Heatmap = defineComponent({
  name: "Heatmap",
  props: HeatmapSchema,
  description:
    "A grid of intensities: rows against columns, each cell shaded by its value and printed with it. " +
    "rows is {label, values} with values in column order; columns names the columns and must line up with them. " +
    "Shading is one tone from light to dark — light is the low end of the range, dark the high end — so it reads as magnitude, never as category. " +
    'unit names what every cell measures ("%", "USD m", "incidents") since one scale covers the whole grid; say so in title when the values cross zero, because a single-hue ramp shows size, not sign. ' +
    "Use it for a matrix small enough to read — correlations, month-by-region tables, cohort retention. A wide grid scrolls sideways inside its own box.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readItems(raw.rows ?? raw.items ?? raw.data)
      .map((record) => ({
        label: readLabel(record),
        cells: readCells(
          record.values ?? record.data ?? record.cells ?? record.row,
        ),
      }))
      .filter((row) => row.cells.length > 0);
    const numbers = rows.flatMap((row) =>
      row.cells.filter((cell): cell is number => cell !== undefined),
    );
    if (numbers.length === 0) return null;

    const columns = toArray(raw.columns ?? raw.cols ?? raw.headers).map(
      (value) => readText(value),
    );
    const columnCount = Math.max(
      columns.length,
      ...rows.map((row) => row.cells.length),
    );
    const title = readTextFromKeys(raw, ["title", "label"]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);
    const tone = props.tone ?? "info";
    const min = Math.min(...numbers);
    const max = Math.max(...numbers);
    const range = max - min;

    const tintFor = (value: number) => {
      // An all-equal grid has no range to shade against. Every cell then takes
      // the same mid tint, which is the honest picture — shading them by their
      // absolute value would invent a gradient the data does not have.
      const ratio = range > 0 ? (value - min) / range : 0.5;
      return toneTint(tone, TINT_FLOOR + ratio * (TINT_CEILING - TINT_FLOOR));
    };

    const summary =
      `Heatmap${title ? ` of ${title}` : ""}: ${rows.length} rows by ${columnCount} columns` +
      `${unit ? ` in ${unit}` : ""}, shaded from ${formatValue(min)} (lightest) to ` +
      `${formatValue(max)} (darkest).`;

    const scale = (
      <span className="vgb-heatmap-scale">
        <span className="vgb-chart-sub">{formatValue(min)}</span>
        {[0, 0.25, 0.5, 0.75, 1].map((step) => (
          <span
            aria-hidden="true"
            className="vgb-heatmap-swatch"
            key={step}
            style={{
              backgroundColor: toneTint(
                tone,
                TINT_FLOOR + step * (TINT_CEILING - TINT_FLOOR),
              ),
            }}
          />
        ))}
        <span className="vgb-chart-sub">{formatValue(max)}</span>
      </span>
    );

    return (
      <ChartFrame
        footnote={scale}
        slot="heatmap"
        summary={summary}
        title={title}
        unit={unit}
      >
        {/*
         * recharts has no matrix primitive — a rows×columns grid of shaded cells
         * is not a chart type it draws, so there is nothing to hand it here. The
         * frame (ChartFrame, the row/column headers, the scale footnote) is
         * ours; only the colour tokens (`toneTint`) come from the shared theme.
         * The matrix itself is one plain `<svg>`, percentage-positioned rather
         * than `viewBox`-scaled so the printed values keep an undistorted,
         * uniform pixel font size regardless of the grid's aspect ratio. Row and
         * column headers stay real HTML text beside it, in the same layout the
         * table version used — a wide grid still scrolls sideways as one box.
         */}
        <div className="vgb-scroll-x">
          <div
            data-a2ui-heatmap
            style={{
              display: "grid",
              gridTemplateColumns: `minmax(6rem, max-content) repeat(${columnCount}, minmax(3rem, 1fr))`,
              gridTemplateRows: `auto repeat(${rows.length}, ${ROW_HEIGHT})`,
              minWidth: "100%",
            }}
          >
            <span
              className="vgb-heatmap-corner"
              style={{ gridColumn: 1, gridRow: 1 }}
            />
            {Array.from({ length: columnCount }, (_, index) => (
              <span
                className="vgb-heatmap-head"
                key={index}
                style={{ gridColumn: index + 2, gridRow: 1 }}
              >
                {columns[index] ?? ""}
              </span>
            ))}
            {rows.map((row, rowIndex) => (
              <span
                className="vgb-heatmap-row-head"
                key={`${row.label}-${rowIndex}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gridColumn: 1,
                  gridRow: rowIndex + 2,
                }}
              >
                {row.label}
              </span>
            ))}
            <svg
              aria-hidden="true"
              className="vgb-heatmap-cells"
              style={{
                gridColumn: `2 / -1`,
                gridRow: `2 / -1`,
                width: "100%",
                height: "100%",
              }}
            >
              {rows.map((row, rowIndex) =>
                Array.from({ length: columnCount }, (_, colIndex) => {
                  const cell = row.cells[colIndex];
                  if (cell === undefined) return null;
                  const xPct = (colIndex / columnCount) * 100;
                  const yPct = (rowIndex / rows.length) * 100;
                  const wPct = 100 / columnCount;
                  const hPct = 100 / rows.length;
                  return (
                    <g data-a2ui-heatmap-cell key={`${rowIndex}-${colIndex}`}>
                      <rect
                        className="vgb-heatmap-cell"
                        fill={tintFor(cell)}
                        height={`${hPct - CELL_GUTTER_PCT}%`}
                        width={`${wPct - CELL_GUTTER_PCT}%`}
                        x={`${xPct + CELL_GUTTER_PCT / 2}%`}
                        y={`${yPct + CELL_GUTTER_PCT / 2}%`}
                      />
                      <text
                        dominantBaseline="central"
                        style={{
                          fill: "var(--openui-text-neutral-primary)",
                          fontFamily: "var(--openui-font-numbers)",
                          fontSize: "var(--openui-font-size-2xs)",
                          fontVariantNumeric: "tabular-nums",
                        }}
                        textAnchor="middle"
                        x={`${xPct + wPct / 2}%`}
                        y={`${yPct + hPct / 2}%`}
                      >
                        {formatValue(cell)}
                      </text>
                    </g>
                  );
                }),
              )}
            </svg>
          </div>
        </div>
      </ChartFrame>
    );
  },
});
