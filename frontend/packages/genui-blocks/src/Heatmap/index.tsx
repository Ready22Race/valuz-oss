"use client";

import { defineComponent } from "@openuidev/react-lang";

import { formatValue, readCells, readItems, readLabel, toneTint } from "../lib/chart";
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

export const Heatmap = defineComponent({
  name: "Heatmap",
  props: HeatmapSchema,
  description:
    "A grid of intensities: rows against columns, each cell shaded by its value and printed with it. " +
    "rows is {label, values} with values in column order; columns names the columns and must line up with them. " +
    "Shading is one tone from light to dark — light is the low end of the range, dark the high end — so it reads as magnitude, never as category. " +
    "unit names what every cell measures (\"%\", \"USD m\", \"incidents\") since one scale covers the whole grid; say so in title when the values cross zero, because a single-hue ramp shows size, not sign. " +
    "Use it for a matrix small enough to read — correlations, month-by-region tables, cohort retention. A wide grid scrolls sideways inside its own box.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readItems(raw.rows ?? raw.items ?? raw.data)
      .map((record) => ({
        label: readLabel(record),
        cells: readCells(record.values ?? record.data ?? record.cells ?? record.row),
      }))
      .filter((row) => row.cells.length > 0);
    const numbers = rows.flatMap((row) =>
      row.cells.filter((cell): cell is number => cell !== undefined),
    );
    if (numbers.length === 0) return null;

    const columns = toArray(raw.columns ?? raw.cols ?? raw.headers).map((value) =>
      readText(value),
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
              backgroundColor: toneTint(tone, TINT_FLOOR + step * (TINT_CEILING - TINT_FLOOR)),
            }}
          />
        ))}
        <span className="vgb-chart-sub">{formatValue(max)}</span>
      </span>
    );

    return (
      <ChartFrame footnote={scale} slot="heatmap" summary={summary} title={title} unit={unit}>
        {/*
         * A real table, not a grid of divs: the cells are tabular data, and the
         * row/column headers then carry the association for free. It is also
         * the table view this chart would otherwise need a second time.
         */}
        <div className="vgb-scroll-x">
          <table className="vgb-heatmap" data-a2ui-heatmap>
            <thead>
              <tr>
                <th className="vgb-heatmap-corner" scope="col" />
                {Array.from({ length: columnCount }, (_, index) => (
                  <th className="vgb-heatmap-head" key={index} scope="col">
                    {columns[index] ?? ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`${row.label}-${rowIndex}`}>
                  <th className="vgb-heatmap-row-head" scope="row">
                    {row.label}
                  </th>
                  {Array.from({ length: columnCount }, (_, index) => {
                    const cell = row.cells[index];
                    return (
                      <td
                        className="vgb-heatmap-cell"
                        key={index}
                        style={
                          cell === undefined ? undefined : { backgroundColor: tintFor(cell) }
                        }
                      >
                        {cell === undefined ? "" : formatValue(cell)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </ChartFrame>
    );
  },
});
