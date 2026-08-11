"use client";

import { defineComponent } from "@openuidev/react-lang";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatValue, readItems, readLabel, spanOf } from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import {
  AXIS_TICK,
  BAR_RADIUS,
  CHART_INITIAL_DIMENSION,
  CHART_MARGIN,
  GRID_STROKE,
  MAX_BAR_SIZE,
  TOOLTIP_CONTENT_STYLE,
  TOOLTIP_CURSOR,
  TOOLTIP_ITEM_STYLE,
  TOOLTIP_LABEL_STYLE,
} from "../lib/recharts-chrome";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import { toneText } from "../lib/tone";
import { HistogramSchema } from "./schema";

export { HistogramBinSchema, HistogramSchema } from "./schema";

/**
 * Per-bin floor, in pixels.
 *
 * With 50 bins in a chat column there is no width at which every label still
 * fits, and the two alternatives both lose: rotated labels are unreadable at
 * this size, truncated ones need a hover to recover and this block is static
 * by design. So each bin keeps this floor and `.vgb-scroll-x` scrolls the box
 * sideways rather than squeezing the bars.
 */
const MIN_BIN_WIDTH = 44;

export const Histogram = defineComponent({
  name: "Histogram",
  props: HistogramSchema,
  description:
    "A distribution as columns of counts — how many observations fell in each bin, in bin order. " +
    'bins is {label, count} where label names the interval ("0–10", "10–20") and count is how many landed in it; keep the bins contiguous and in ascending order, because the shape is the answer. ' +
    'unit names what is being counted ("companies", "trading days"); title should state what was measured and over what period. ' +
    "Use it for a spread — returns, latencies, ages, scores. For counts of unrelated named categories use GroupedBar instead: a histogram's bins are a scale, not a list.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const bins = readItems(raw.bins ?? raw.items ?? raw.data ?? raw.buckets)
      .map((record) => ({
        label:
          readLabel(record) ||
          readTextFromKeys(record, ["range", "bucket", "bin"]),
        count: readLooseNumber(
          record.count ?? record.value ?? record.n ?? record.frequency,
        ),
      }))
      .filter(
        (bin): bin is { label: string; count: number } =>
          bin.count !== undefined,
      );
    if (bins.length === 0) return null;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);
    const tone = props.tone ?? "brand";
    // A negative count is not a height. Clamp for geometry only — the printed
    // figure stays whatever the model said, so the error is visible.
    const heights = bins.map((bin) => Math.max(0, bin.count));
    const span = spanOf(heights);
    const total = bins.reduce((sum, bin) => sum + bin.count, 0);
    const tallest = bins.reduce(
      (best, bin) => (bin.count > best.count ? bin : best),
      bins[0]!,
    );

    const summary =
      `Histogram${title ? ` of ${title}` : ""}: ${bins.length} bins` +
      `${unit ? ` counting ${unit}` : ""}, ${formatValue(total)} in total, ` +
      `tallest bin ${tallest.label || "unlabelled"} at ${formatValue(tallest.count)}.`;

    // recharts skips drawing (and labelling) a bar whose value is exactly 0,
    // which would silently drop the count label a zero or clamped-negative
    // bin still owes the reader. A sliver this thin is invisible at any real
    // chart height, so it costs nothing to keep the label alive.
    const labelFloor = span.size / 500;
    const data = bins.map((bin, index) => ({
      label: bin.label,
      count: bin.count,
      countLabel: formatValue(bin.count),
      height: Math.max(heights[index]!, labelFloor),
    }));

    return (
      <ChartFrame slot="histogram" summary={summary} title={title} unit={unit}>
        {/*
         * The plot scrolls inside its own box rather than squeezing the bins.
         * The box gets an explicit floor of `bins.length * MIN_BIN_WIDTH` —
         * `ResponsiveContainer` fills whatever width its parent offers, so a
         * wide floor is what makes 50 bins overflow into `.vgb-scroll-x`
         * instead of recharts happily rendering them as unreadable slivers.
         */}
        <div className="vgb-scroll-x">
          <div
            className="vgb-recharts"
            data-a2ui-histogram
            style={{ minWidth: `${bins.length * MIN_BIN_WIDTH}px` }}
          >
            <ResponsiveContainer
              width="100%"
              height="100%"
              minWidth={0}
              minHeight={0}
              initialDimension={CHART_INITIAL_DIMENSION}
            >
              <BarChart data={data} margin={CHART_MARGIN} barCategoryGap="2%">
                <CartesianGrid
                  stroke={GRID_STROKE}
                  strokeOpacity={0.6}
                  vertical={false}
                />
                <XAxis
                  axisLine={false}
                  dataKey="label"
                  interval={0}
                  tick={AXIS_TICK}
                  tickLine={false}
                />
                <YAxis
                  allowDecimals={false}
                  axisLine={false}
                  domain={[span.min, span.min + span.size]}
                  tick={AXIS_TICK}
                  tickFormatter={formatValue}
                  tickLine={false}
                />
                <Tooltip
                  content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const point = payload[0]?.payload as
                      { label: string; count: number } | undefined;
                    if (!point) return null;
                    return (
                      <div style={TOOLTIP_CONTENT_STYLE}>
                        <div style={TOOLTIP_LABEL_STYLE}>
                          {point.label || "—"}
                        </div>
                        <div style={TOOLTIP_ITEM_STYLE}>
                          {`${formatValue(point.count)}${unit ? ` ${unit}` : ""}`}
                        </div>
                      </div>
                    );
                  }}
                  cursor={TOOLTIP_CURSOR}
                  isAnimationActive={false}
                />
                <Bar
                  dataKey="height"
                  fill={toneText(tone)}
                  isAnimationActive={false}
                  maxBarSize={MAX_BAR_SIZE}
                  radius={BAR_RADIUS}
                >
                  <LabelList
                    dataKey="countLabel"
                    fontFamily={AXIS_TICK.fontFamily}
                    fontSize={AXIS_TICK.fontSize}
                    fill={AXIS_TICK.fill}
                    position="top"
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </ChartFrame>
    );
  },
});
