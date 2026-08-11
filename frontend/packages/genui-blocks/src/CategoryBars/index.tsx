"use client";

import { defineComponent } from "@openuidev/react-lang";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  MAX_SERIES,
  formatValue,
  readCells,
  readItems,
  seriesColor,
  spanOf,
} from "../lib/chart";
import { ChartFrame, ChartLegend } from "../lib/chart-parts";
import { readText, readTextFromKeys, toArray } from "../lib/props";
import {
  AXIS_TICK,
  CHART_INITIAL_DIMENSION,
  CHART_MARGIN,
  GRID_STROKE,
  MAX_BAR_SIZE,
  TOOLTIP_CONTENT_STYLE,
  TOOLTIP_CURSOR,
  TOOLTIP_ITEM_STYLE,
  TOOLTIP_LABEL_STYLE,
} from "../lib/recharts-chrome";
import { GroupedBarSchema, StackedBarSchema } from "./schema";

export {
  ChartSeriesSchema,
  GroupedBarSchema,
  StackedBarSchema,
} from "./schema";

/*
 * `@openuidev/react-ui`'s `BarChart` was tried first, per the migration brief.
 * It derives its series keys from `Object.keys(data[0])` alone (see
 * `getDataKeys` in its `dataUtils`) rather than from a declared prop — so a
 * category-0 hole in one series (routine: `values` arrays only have to be as
 * long as `categories`, not every series aligned) silently drops that series
 * from *every* category, not just the one with the hole. That is a data-loss
 * bug for this block's contract, so both charts here render straight recharts
 * `BarChart`s instead, with one `<Bar>` per series declared explicitly — the
 * same "own the dataKey" approach `ComboChart` uses — and the same
 * `../lib/recharts-chrome` tokens.
 */

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

  const labels = toArray(raw.categories ?? raw.labels ?? raw.columns).map(
    (value) => readText(value),
  );
  const count = Math.max(
    labels.length,
    ...series.map((entry) => entry.values.length),
  );
  if (count === 0) return null;

  return {
    categories: Array.from(
      { length: count },
      (_, index) => labels[index] ?? "",
    ),
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

/** A series's synthetic recharts key. Never the series's own name: two series
 *  can share a display name (an unnamed pair both falling back to the same
 *  "Series N" text is the routine case), and a name collision would merge
 *  their values under one data key. The legend and tooltip still show the
 *  real name via `name=`. */
function seriesKey(index: number): string {
  return `s${index}`;
}

export const GroupedBar = defineComponent({
  name: "GroupedBar",
  props: GroupedBarSchema,
  description:
    "Categories compared across several series, each series its own bar, all on one shared scale. " +
    "categories names what is being compared; series is {name, values} where the nth value belongs to the nth category, so every values array must be as long as categories. " +
    'Every series must be the same measure in the same unit — two measures of different scale is two charts, never one with two axes. unit names that measure ("USD m", "%"). ' +
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
    const summary =
      `Grouped bar chart${data.title ? ` of ${data.title}` : ""}: ` +
      `${data.categories.length} categories across ${data.series.length} series ` +
      `(${data.series.map((entry) => entry.name || "unnamed").join(", ")})` +
      `${data.unit ? ` in ${data.unit}` : ""}, values from ${formatValue(Math.min(...numbers))} ` +
      `to ${formatValue(Math.max(...numbers))}.`;

    const rows = data.categories.map((category, categoryIndex) => {
      const row: Record<string, string | number | null> = { category };
      data.series.forEach((entry, seriesIndex) => {
        // A hole in one category is a hole, not a zero — leave it out so the
        // bar for that series in that category is skipped rather than drawn
        // at a value the data never gave.
        row[seriesKey(seriesIndex)] = entry.values[categoryIndex] ?? null;
      });
      return row;
    });

    return (
      <ChartFrame
        footnote={droppedNote(data.dropped)}
        slot="grouped-bar"
        summary={summary}
        title={data.title}
        unit={data.unit}
      >
        <ChartLegend names={data.series.map((entry) => entry.name)} />
        <div className="vgb-recharts">
          <ResponsiveContainer
            height="100%"
            initialDimension={CHART_INITIAL_DIMENSION}
            minHeight={0}
            minWidth={0}
            width="100%"
          >
            <BarChart data={rows} margin={CHART_MARGIN}>
              <CartesianGrid
                stroke={GRID_STROKE}
                strokeOpacity={0.6}
                vertical={false}
              />
              <XAxis
                axisLine={false}
                dataKey="category"
                tick={AXIS_TICK}
                tickLine={false}
              />
              <YAxis
                axisLine={false}
                domain={[span.min, span.max]}
                tick={AXIS_TICK}
                tickFormatter={formatValue}
                tickLine={false}
                width={40}
              />
              <Tooltip
                contentStyle={TOOLTIP_CONTENT_STYLE}
                cursor={TOOLTIP_CURSOR}
                formatter={(value) => formatValue(Number(value))}
                isAnimationActive={false}
                itemStyle={TOOLTIP_ITEM_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
              />
              {data.series.map((entry, seriesIndex) => (
                <Bar
                  dataKey={seriesKey(seriesIndex)}
                  fill={seriesColor(seriesIndex)}
                  isAnimationActive={false}
                  key={`${entry.name}-${seriesIndex}`}
                  maxBarSize={MAX_BAR_SIZE}
                  name={entry.name}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
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
      return {
        category,
        parts,
        total: parts.reduce((sum, part) => sum + part, 0),
      };
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

    const rows = stacks.map((stack) => {
      const row: Record<string, string | number> = { category: stack.category };
      stack.parts.forEach((part, seriesIndex) => {
        row[seriesKey(seriesIndex)] = part;
      });
      return row;
    });

    return (
      <ChartFrame
        footnote={footnote}
        slot="stacked-bar"
        summary={summary}
        title={data.title}
        unit={data.unit}
      >
        <ChartLegend names={data.series.map((entry) => entry.name)} />
        <div className="vgb-recharts">
          <ResponsiveContainer
            height="100%"
            initialDimension={CHART_INITIAL_DIMENSION}
            minHeight={0}
            minWidth={0}
            width="100%"
          >
            <BarChart data={rows} margin={CHART_MARGIN}>
              <CartesianGrid
                stroke={GRID_STROKE}
                strokeOpacity={0.6}
                vertical={false}
              />
              <XAxis
                axisLine={false}
                dataKey="category"
                tick={AXIS_TICK}
                tickLine={false}
              />
              <YAxis
                axisLine={false}
                domain={[span.min, span.max]}
                tick={AXIS_TICK}
                tickFormatter={formatValue}
                tickLine={false}
                width={40}
              />
              <Tooltip
                contentStyle={TOOLTIP_CONTENT_STYLE}
                cursor={TOOLTIP_CURSOR}
                formatter={(value) => formatValue(Number(value))}
                isAnimationActive={false}
                itemStyle={TOOLTIP_ITEM_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
              />
              {data.series.map((entry, seriesIndex) => (
                <Bar
                  dataKey={seriesKey(seriesIndex)}
                  fill={seriesColor(seriesIndex)}
                  isAnimationActive={false}
                  key={`${entry.name}-${seriesIndex}`}
                  maxBarSize={MAX_BAR_SIZE}
                  name={entry.name}
                  stackId="stack"
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </ChartFrame>
    );
  },
});
