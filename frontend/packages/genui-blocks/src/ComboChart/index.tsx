"use client";

import { defineComponent } from "@openuidev/react-lang";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatValue, readCells, seriesColor, spanOf } from "../lib/chart";
import { ChartFrame, ChartLegend } from "../lib/chart-parts";
import { readRecord, readText, readTextFromKeys, toArray } from "../lib/props";
import type { Span } from "../lib/chart";
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
import { ComboChartSchema } from "./schema";

export { ComboChartSchema, ComboSeriesSchema } from "./schema";

/**
 * Category cap.
 *
 * Deliberately a cap rather than a sideways scroll, which is what the wide
 * charts in this family do. A combo chart's claim is about how the line moves
 * *against* the bars, and half of that comparison scrolled out of view is the
 * comparison not being made. Sixteen columns still fit a chat column; past that
 * the answer wants two charts.
 */
const MAX_CATEGORIES = 16;

/** Bars take the first palette slot, the line the second — same as the legend. */
const BAR_COLOR = seriesColor(0);
const LINE_COLOR = seriesColor(1);

interface Series {
  name: string;
  values: (number | undefined)[];
}

function readSeries(value: unknown, fallback: string): Series {
  // A model that has just written GroupedBar reaches for an array here out of
  // habit. One series wrapped in a list is still one series; anything past the
  // first is not drawable by this shape and is left behind.
  const record = readRecord(Array.isArray(value) ? value[0] : value);
  return {
    name:
      readTextFromKeys(record, ["name", "label", "title", "series"]) ||
      fallback,
    values: readCells(record.values ?? record.data ?? record.points),
  };
}

/**
 * One category's row for recharts.
 *
 * An undefined reading becomes `null` rather than being omitted: `Bar` and
 * `Line` are declared once with a fixed `dataKey`, so a hole here just skips
 * that one bar and (with `connectNulls={false}`) breaks the line rather than
 * bridging it — the same "a hole is a hole" contract the hand-drawn version
 * enforced with `segmentsOf`.
 */
interface ComboRow {
  category: string;
  bar: number | null;
  line: number | null;
}

export const ComboChart = defineComponent({
  name: "ComboChart",
  props: ComboChartSchema,
  description:
    "Bars and a line over the same categories — a level and the rate, a volume and its price, an amount and its share. " +
    "categories names the columns; bars and line are each {name, values} where the nth value belongs to the nth category. " +
    "Both series are drawn against ONE shared scale by default, which is almost always what you want. A second axis is only used when sameScale is explicitly false AND barUnit and lineUnit name genuinely different units — an unlabelled second axis can be positioned to show any correlation you like, so it is refused rather than guessed at. " +
    'barUnit and lineUnit name what each series measures ("USD m", "%"). At most 16 categories are drawn. Use GroupedBar when both series are the same measure, and two separate charts when they are unrelated.',
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const bars = readSeries(raw.bars ?? raw.bar ?? raw.columns, "Bars");
    const line = readSeries(raw.line ?? raw.trend ?? raw.overlay, "Line");
    const labels = toArray(raw.categories ?? raw.labels).map((value) =>
      readText(value),
    );

    const count = Math.min(
      MAX_CATEGORIES,
      Math.max(labels.length, bars.values.length, line.values.length),
    );
    const truncated =
      Math.max(labels.length, bars.values.length, line.values.length) - count;

    const barNumbers = bars.values
      .slice(0, count)
      .filter((value): value is number => value !== undefined);
    const lineNumbers = line.values
      .slice(0, count)
      .filter((value): value is number => value !== undefined);
    // Neither series carries a number: there is nothing to draw, so nothing is
    // drawn. An empty plot holding its height is the defect.
    if (count === 0 || (barNumbers.length === 0 && lineNumbers.length === 0))
      return null;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const barUnit = readTextFromKeys(raw, ["barUnit", "bar_unit"]);
    const lineUnit = readTextFromKeys(raw, ["lineUnit", "line_unit"]);
    const sameScale = raw.sameScale ?? raw.same_scale;

    /*
     * **One scale unless a second is both asked for and justified.**
     *
     * Two axes is the way this chart lies: slide one scale against the other and
     * any two series can be made to look correlated, or made to look unrelated.
     * So a split needs the author to say `sameScale: false` *and* two different
     * units to say it about. `sameScale: false` with one unit — or with none
     * named — keeps the shared scale and says why in the note, because the same
     * measure on two different scales is never right.
     */
    const asked = sameScale === false;
    const differ =
      Boolean(barUnit) && Boolean(lineUnit) && barUnit !== lineUnit;
    const split = asked && differ;
    const refused = asked && !differ;

    const barSpan = spanOf(
      split ? barNumbers : [...barNumbers, ...lineNumbers],
    );
    const lineSpan = split ? spanOf(lineNumbers) : barSpan;
    const sharedUnit = barUnit || lineUnit;

    const range = (numbers: number[], span: Span) =>
      numbers.length > 0
        ? `${formatValue(Math.min(...numbers))} to ${formatValue(Math.max(...numbers))}`
        : `${formatValue(span.min)} to ${formatValue(span.max)}`;

    const summary =
      `Combination chart${title ? ` of ${title}` : ""}: ${count} categories. ` +
      `Bars, ${bars.name}, ${range(barNumbers, barSpan)}${barUnit ? ` ${barUnit}` : ""}; ` +
      `line, ${line.name}, ${range(lineNumbers, lineSpan)}${lineUnit ? ` ${lineUnit}` : ""}. ` +
      (split
        ? "The two series use different scales and are not comparable against each other."
        : "Both series are drawn against one shared scale.");

    const notes = [
      split
        ? `Two scales: the bars are read against the left axis (${barUnit}, ` +
          `${formatValue(barSpan.min)} to ${formatValue(barSpan.max)}) and the line ` +
          `against the right axis (${lineUnit}, ${formatValue(lineSpan.min)} to ` +
          `${formatValue(lineSpan.max)}). The two are not comparable — where the line ` +
          "crosses a bar means nothing, since either scale can be shifted to put it " +
          "anywhere."
        : "",
      refused
        ? "A separate scale was requested but both series carry the same unit, or none " +
          "was named, so one shared scale is used: the same measure drawn against two " +
          "different scales is never right."
        : "",
      truncated > 0
        ? `${truncated} further categories were not drawn — a combination chart only ` +
          "makes its point while the whole of both series is on screen at once."
        : "",
    ].filter(Boolean);

    const data: ComboRow[] = Array.from({ length: count }, (_, index) => ({
      category: labels[index] ?? "",
      bar: bars.values[index] ?? null,
      line: line.values[index] ?? null,
    }));

    return (
      <ChartFrame
        footnote={
          notes.length > 0 ? (
            <span data-combo-split={split ? "true" : undefined}>
              {notes.join(" ")}
            </span>
          ) : null
        }
        slot="combo-chart"
        summary={summary}
        title={title}
        unit={split ? undefined : sharedUnit}
      >
        <ChartLegend names={[bars.name, line.name]} />
        <div
          className="vgb-recharts"
          data-a2ui-combo
          data-combo-scales={split ? "split" : "shared"}
        >
          <ResponsiveContainer
            height="100%"
            initialDimension={CHART_INITIAL_DIMENSION}
            minHeight={0}
            minWidth={0}
            width="100%"
          >
            <ComposedChart data={data} margin={CHART_MARGIN}>
              <CartesianGrid
                stroke={GRID_STROKE}
                strokeOpacity={0.6}
                vertical={false}
              />
              {/* interval 0: a category never silently disappears from the
                  axis. recharts drops colliding ticks by default, which reads
                  as a missing category; overlap at extreme label lengths is
                  the lesser harm, and the tooltip disambiguates it. */}
              <XAxis
                axisLine={false}
                dataKey="category"
                interval={0}
                tick={AXIS_TICK}
                tickLine={false}
              />
              <YAxis
                axisLine={false}
                domain={[barSpan.min, barSpan.max]}
                tick={AXIS_TICK}
                tickFormatter={formatValue}
                tickLine={false}
                width={40}
              />
              {split ? (
                <YAxis
                  axisLine={false}
                  domain={[lineSpan.min, lineSpan.max]}
                  orientation="right"
                  tick={AXIS_TICK}
                  tickFormatter={formatValue}
                  tickLine={false}
                  width={40}
                  yAxisId="line"
                />
              ) : null}
              <Tooltip
                contentStyle={TOOLTIP_CONTENT_STYLE}
                cursor={TOOLTIP_CURSOR}
                formatter={(value) => formatValue(Number(value))}
                isAnimationActive={false}
                itemStyle={TOOLTIP_ITEM_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
              />
              <Bar
                dataKey="bar"
                fill={BAR_COLOR}
                isAnimationActive={false}
                maxBarSize={MAX_BAR_SIZE}
                name={bars.name}
                radius={2}
              />
              <Line
                connectNulls={false}
                dataKey="line"
                dot={false}
                isAnimationActive={false}
                name={line.name}
                stroke={LINE_COLOR}
                strokeWidth={2}
                type="linear"
                yAxisId={split ? "line" : undefined}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </ChartFrame>
    );
  },
});
