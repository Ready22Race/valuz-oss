"use client";

import { defineComponent } from "@openuidev/react-lang";
import { Fragment } from "react";

import {
  MAX_SERIES,
  asPct,
  formatValue,
  offsetPct,
  readCells,
  readItems,
  seriesTone,
  sizePct,
  spanOf,
} from "../lib/chart";
import { ChartFrame, ChartLegend, ChartRow } from "../lib/chart-parts";
import { readText, readTextFromKeys, toArray } from "../lib/props";
import { toneText } from "../lib/tone";
import { GroupedBarSchema, StackedBarSchema } from "./schema";

export { ChartSeriesSchema, GroupedBarSchema, StackedBarSchema } from "./schema";

interface CategoryData {
  categories: string[];
  series: { name: string; values: (number | undefined)[] }[];
  title: string;
  unit: string;
  /** Series beyond the palette, dropped rather than given a repeated colour. */
  dropped: number;
}

function readCategoryData(raw: Record<string, unknown>): CategoryData | null {
  const series = readItems(raw.series ?? raw.data ?? raw.groups)
    .map((record, index) => ({
      // An unnamed series is numbered, not dropped: the legend and the stacked
      // breakdown both need something to call it, and "" would render a swatch
      // labelled with nothing.
      name:
        readTextFromKeys(record, ["name", "label", "title", "series"]) ||
        `Series ${index + 1}`,
      values: readCells(record.values ?? record.data ?? record.points),
    }))
    .filter((entry) => entry.values.length > 0);
  if (series.length === 0) return null;

  const labels = toArray(raw.categories ?? raw.labels ?? raw.columns).map((value) =>
    readText(value),
  );
  const count = Math.max(labels.length, ...series.map((entry) => entry.values.length));
  if (count === 0) return null;

  return {
    categories: Array.from({ length: count }, (_, index) => labels[index] ?? ""),
    series: series.slice(0, MAX_SERIES),
    title: readTextFromKeys(raw, ["title", "label"]),
    unit: readTextFromKeys(raw, ["unit", "units", "basis"]),
    dropped: Math.max(0, series.length - MAX_SERIES),
  };
}

/** "beyond the palette" note, shared by both blocks. */
function droppedNote(dropped: number) {
  return dropped > 0 ? (
    <span>
      {`${dropped} further series were not drawn — ${MAX_SERIES} is the number of ` +
        "distinct colours available. Split the comparison across two charts."}
    </span>
  ) : null;
}

export const GroupedBar = defineComponent({
  name: "GroupedBar",
  props: GroupedBarSchema,
  description:
    "Categories compared across several series, each series its own bar, all on one shared scale. " +
    "categories names what is being compared; series is {name, values} where the nth value belongs to the nth category, so every values array must be as long as categories. " +
    "Every series must be the same measure in the same unit — two measures of different scale is two charts, never one with two axes. unit names that measure (\"USD m\", \"%\"). " +
    "Negative values are supported and grow left from a zero line. At most six series are drawn. Use StackedBar instead when the series are parts of one total.",
  component: ({ props }) => {
    const data = readCategoryData(props as unknown as Record<string, unknown>);
    if (!data) return null;

    const numbers = data.series.flatMap((entry) =>
      entry.values.filter((value): value is number => value !== undefined),
    );
    if (numbers.length === 0) return null;

    // Zero is always in the domain: a bar's length is its magnitude, and a
    // domain of 98–100 would draw a 99 as half a bar.
    const span = spanOf(numbers);
    const zero = offsetPct(0, span);
    const summary =
      `Grouped bar chart${data.title ? ` of ${data.title}` : ""}: ` +
      `${data.categories.length} categories across ${data.series.length} series ` +
      `(${data.series.map((entry) => entry.name || "unnamed").join(", ")})` +
      `${data.unit ? ` in ${data.unit}` : ""}, values from ${formatValue(Math.min(...numbers))} ` +
      `to ${formatValue(Math.max(...numbers))}.`;

    return (
      <ChartFrame
        footnote={droppedNote(data.dropped)}
        slot="grouped-bar"
        summary={summary}
        title={data.title}
        unit={data.unit}
      >
        <ChartLegend names={data.series.map((entry) => entry.name)} />
        <div className="vgb-chart-rows">
          {data.categories.map((category, categoryIndex) => (
            <div className="vgb-chart-row vgb-chart-row-group" key={`${category}-${categoryIndex}`}>
              <span className="vgb-chart-label">
                <span className="vgb-chart-label-text">{category}</span>
              </span>
              <div className="vgb-group" data-a2ui-chart-group>
                {data.series.map((entry, seriesIndex) => {
                  const value = entry.values[categoryIndex];
                  const lo = value === undefined ? 0 : Math.min(0, value);
                  const hi = value === undefined ? 0 : Math.max(0, value);
                  return (
                    <Fragment key={`${entry.name}-${seriesIndex}`}>
                      <div className="vgb-chart-track">
                        {span.min < 0 ? (
                          <span
                            aria-hidden="true"
                            className="vgb-chart-zero"
                            style={{ left: asPct(zero) }}
                          />
                        ) : null}
                        {value !== undefined && hi - lo > 0 ? (
                          <span
                            aria-hidden="true"
                            className="vgb-chart-bar"
                            style={{
                              backgroundColor: toneText(seriesTone(seriesIndex)),
                              left: asPct(offsetPct(lo, span)),
                              width: asPct(sizePct(hi - lo, span)),
                            }}
                          />
                        ) : null}
                      </div>
                      <span className="vgb-chart-value">
                        {value === undefined ? "—" : formatValue(value)}
                      </span>
                    </Fragment>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </ChartFrame>
    );
  },
});

export const StackedBar = defineComponent({
  name: "StackedBar",
  // Its own schema object, never GroupedBar's — see `categoryBarProps()`.
  props: StackedBarSchema,
  description:
    "Categories as one bar each, with the series stacked inside it and the total printed at the end. " +
    "categories names the bars; series is {name, values} where the nth value belongs to the nth category — the series must be parts of that category's whole, in one unit named by unit. " +
    "Every part is printed under its bar and the total at the end of it, so the reader can add the parts up and check them. " +
    "Only positive contributions can be stacked; a negative one is dropped and said so. At most six series are drawn. Use GroupedBar when the series are alternatives rather than parts.",
  component: ({ props }) => {
    const data = readCategoryData(props as unknown as Record<string, unknown>);
    if (!data) return null;

    let negatives = 0;
    const stacks = data.categories.map((category, categoryIndex) => {
      const parts = data.series.map((entry) => {
        const value = entry.values[categoryIndex];
        if (value !== undefined && value < 0) negatives += 1;
        // A stack of signed values has no honest geometry — a negative segment
        // would either shorten the bar (hiding it) or extend it (double-counting
        // it). It is dropped from both the bar and the total, and the footnote
        // says how many went, so the total the reader checks is the total drawn.
        return value === undefined || value < 0 ? 0 : value;
      });
      return { category, parts, total: parts.reduce((sum, part) => sum + part, 0) };
    });
    if (stacks.length === 0) return null;

    const span = spanOf(stacks.map((stack) => stack.total));
    const totals = stacks.map((stack) => stack.total);
    const summary =
      `Stacked bar chart${data.title ? ` of ${data.title}` : ""}: ` +
      `${stacks.length} categories, each split into ${data.series.length} series ` +
      `(${data.series.map((entry) => entry.name || "unnamed").join(", ")})` +
      `${data.unit ? ` in ${data.unit}` : ""}, totals from ${formatValue(Math.min(...totals))} ` +
      `to ${formatValue(Math.max(...totals))}.`;

    const footnote =
      negatives > 0 ? (
        <span>
          {`${negatives} negative value${negatives === 1 ? " was" : "s were"} dropped: ` +
            "a stack can only be built from positive parts, so they are excluded from " +
            "both the bars and the totals."}
        </span>
      ) : (
        droppedNote(data.dropped)
      );

    return (
      <ChartFrame
        footnote={footnote}
        slot="stacked-bar"
        summary={summary}
        title={data.title}
        unit={data.unit}
      >
        <ChartLegend names={data.series.map((entry) => entry.name)} />
        <div className="vgb-chart-rows">
          {stacks.map((stack, index) => (
            <ChartRow
              detail={
                /*
                 * The parts, as plain text under the bar.
                 *
                 * Not inside the segments: a value set on a saturated fill needs
                 * its colour picked from the fill's luminance, which a token
                 * cannot be asked for, and the narrow segments have no room for
                 * it anyway. A line of ordinary secondary text always fits, wraps
                 * where it must, and needs no hover — which this block does not
                 * have, by design.
                 */
                data.series.map((entry, seriesIndex) => (
                  <span className="vgb-stack-part" key={`${entry.name}-${seriesIndex}`}>
                    <span
                      aria-hidden="true"
                      className="vgb-chart-swatch"
                      style={{ backgroundColor: toneText(seriesTone(seriesIndex)) }}
                    />
                    {`${entry.name || "series"} ${formatValue(stack.parts[seriesIndex] ?? 0)}`}
                  </span>
                ))
              }
              figure={formatValue(stack.total)}
              key={`${stack.category}-${index}`}
              label={stack.category}
            >
              <span className="vgb-stack" data-a2ui-chart-stack>
                {stack.parts.map((part, seriesIndex) => {
                  const width = sizePct(part, span);
                  if (width <= 0) return null;
                  return (
                    <span
                      aria-hidden="true"
                      className="vgb-stack-segment"
                      key={`${data.series[seriesIndex]?.name ?? seriesIndex}`}
                      style={{
                        backgroundColor: toneText(seriesTone(seriesIndex)),
                        width: asPct(width),
                      }}
                    />
                  );
                })}
              </span>
            </ChartRow>
          ))}
        </div>
      </ChartFrame>
    );
  },
});
