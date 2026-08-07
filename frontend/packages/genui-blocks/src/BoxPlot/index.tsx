"use client";

import { defineComponent } from "@openuidev/react-lang";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  extentOf,
  formatValue,
  readItems,
  readLabel,
  readNumbers,
  toneTint,
} from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import {
  AXIS_TICK,
  CHART_INITIAL_DIMENSION,
  CHART_MARGIN,
  GRID_STROKE,
  TOOLTIP_CONTENT_STYLE,
  TOOLTIP_CURSOR,
  TOOLTIP_ITEM_STYLE,
  TOOLTIP_LABEL_STYLE,
} from "../lib/recharts-chrome";
import type { Tone } from "../lib/schema";
import { toneText } from "../lib/tone";
import { BoxPlotSchema } from "./schema";

export { BoxPlotItemSchema, BoxPlotSchema } from "./schema";

interface Box {
  label: string;
  /** The five numbers, sorted — see `readBox`. */
  five: [number, number, number, number, number];
  outliers: number[];
}

/**
 * One distribution, defensively ordered.
 *
 * The five numbers are sorted rather than trusted. A model that emits q1 above
 * q3 (or a max below the median) would otherwise produce a negative-width box,
 * which CSS clamps to nothing — the row would render with a whisker and no box
 * at all, and nothing would say why.
 */
function readBox(record: Record<string, unknown>): Box | null {
  const parts = [
    readLooseNumber(record.min ?? record.low ?? record.minimum),
    readLooseNumber(record.q1 ?? record.p25 ?? record.lowerQuartile),
    readLooseNumber(record.median ?? record.p50 ?? record.mid),
    readLooseNumber(record.q3 ?? record.p75 ?? record.upperQuartile),
    readLooseNumber(record.max ?? record.high ?? record.maximum),
  ];
  if (parts.some((part) => part === undefined)) return null;
  const five = (parts as number[]).slice().sort((a, b) => a - b) as Box["five"];
  return {
    label: readLabel(record),
    five,
    outliers: readNumbers(record.outliers ?? record.outlier ?? record.flyers),
  };
}

/** A box never balloons to fill a sparse category axis. */
const MAX_BOX_WIDTH = 40;

interface BoxDatum extends Box {
  /** min/max as a pair — a recharts `Bar` reads a two-element `dataKey` as a
   *  floating bar, so this one bar per category *is* the whisker-to-whisker
   *  span, and its own x/y/width/height (handed to `shape` below) are the
   *  pixel box every other mark is derived from. */
  range: [number, number];
}

/**
 * The whisker, box, median tick and outlier dots for one distribution.
 *
 * A plain `<Customized>` layer reading `useXAxisScale()`/`useYAxisScale()`
 * looked like the more direct translation of "recharts supplies the
 * coordinate math", but a `YAxis` with no graphical item referencing it never
 * gets ticks or a scale computed in recharts 3.8 — `useYAxisScale()` comes
 * back `undefined` and nothing draws. A `Bar` is what makes the numeric axis
 * real; `recharts`'s own source anticipates exactly this shape (`Bar.js`:
 * "Bars with a custom shape are not filtered out: the custom renderer may
 * still draw something visible at zero-dimension positions (e.g. horizontal
 * lines in a BoxPlot)"). So the "range" `Bar` below is the whisker-to-whisker
 * hit box and the thing that earns the axis its scale; this function replaces
 * its rectangle with the real five-number geometry, computed by linearly
 * interpolating q1/median/q3/outliers between the two pixel positions recharts
 * already resolved for min and max.
 */
function BoxShape(props: {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  payload?: BoxDatum;
  tone: Tone;
}) {
  const { x, y, width, height, payload, tone } = props;
  if (x == null || y == null || width == null || height == null || !payload)
    return null;

  const [low, q1, median, q3, high] = payload.five;
  const span = high - low;
  // `y` is the pixel position of `high`, `y + height` is the pixel position of
  // `low` — recharts computed both from the same linear y-scale, so any other
  // value's pixel position (even one outside [low, high], as an outlier can be)
  // is the same affine interpolation between them.
  const valueToY = (value: number) =>
    span > 0 ? y + ((high - value) / span) * height : y + height / 2;

  const boxWidth = Math.min(width, MAX_BOX_WIDTH);
  const center = x + width / 2;
  const left = center - boxWidth / 2;
  const yQ1 = valueToY(q1);
  const yQ3 = valueToY(q3);
  const yMedian = valueToY(median);
  const boxTop = Math.min(yQ1, yQ3);
  const boxHeight = Math.max(Math.abs(yQ1 - yQ3), 1);

  return (
    <g data-a2ui-box-plot-item>
      <line
        className="vgb-box-plot-whisker"
        stroke={toneText(tone)}
        strokeWidth={1}
        x1={center}
        x2={center}
        y1={y}
        y2={y + height}
      />
      <rect
        className="vgb-box-plot-box"
        fill={toneTint(tone, 30)}
        height={boxHeight}
        stroke={toneText(tone)}
        strokeWidth={1}
        width={boxWidth}
        x={left}
        y={boxTop}
      />
      <line
        className="vgb-box-plot-median"
        stroke={toneText(tone)}
        strokeWidth={2}
        x1={left}
        x2={left + boxWidth}
        y1={yMedian}
        y2={yMedian}
      />
      {payload.outliers.map((outlier, dot) => (
        <circle
          className="vgb-box-plot-outlier"
          cx={center}
          cy={valueToY(outlier)}
          fill={toneText(tone)}
          key={`${outlier}-${dot}`}
          r={2.5}
        />
      ))}
    </g>
  );
}

export const BoxPlot = defineComponent({
  name: "BoxPlot",
  props: BoxPlotSchema,
  description:
    "Five-number summaries side by side: whiskers from min to max, a box across the interquartile range, a tick at the median, and dots for outliers. " +
    "items is {label, min, q1, median, q3, max, outliers} — all five numbers are required and are quoted low to high; outliers is optional and drawn as separate dots. " +
    'Every distribution shares one scale, so all the values must be in the same unit; unit names it ("days to close", "%") and the numbers carry none of their own. ' +
    "Use it to compare spread and skew across groups. When you only have an average per group, that is a GroupedBar — do not invent quartiles to reach for this.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const boxes = readItems(raw.items ?? raw.data ?? raw.groups)
      .map(readBox)
      .filter((box): box is Box => box !== null);
    if (boxes.length === 0) return null;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);
    const tone = props.tone ?? "brand";
    // Positional data: the scale is the observed range, not zero to the max.
    const span = extentOf(
      boxes.flatMap((box) => [...box.five, ...box.outliers]),
    );
    const medians = boxes.map((box) => box.five[2]);
    const outlierCount = boxes.reduce(
      (sum, box) => sum + box.outliers.length,
      0,
    );

    const summary =
      `Box plot${title ? ` of ${title}` : ""}: ${boxes.length} distributions` +
      `${unit ? ` in ${unit}` : ""}, medians from ${formatValue(Math.min(...medians))} to ` +
      `${formatValue(Math.max(...medians))}, overall range ${formatValue(span.min)} to ` +
      `${formatValue(span.max)}` +
      (outlierCount > 0 ? `, ${outlierCount} outliers.` : ".");

    const data: BoxDatum[] = boxes.map((box) => ({
      ...box,
      range: [box.five[0], box.five[4]],
    }));

    return (
      <ChartFrame slot="box-plot" summary={summary} title={title} unit={unit}>
        <div className="vgb-recharts">
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
              <XAxis
                axisLine={false}
                dataKey="label"
                interval={0}
                tick={AXIS_TICK}
                tickLine={false}
                type="category"
              />
              <YAxis
                axisLine={false}
                domain={[span.min, span.min + span.size]}
                tick={AXIS_TICK}
                tickFormatter={formatValue}
                tickLine={false}
                type="number"
                width={44}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const box = payload[0]?.payload as BoxDatum | undefined;
                  if (!box) return null;
                  const [low, q1, median, q3, high] = box.five;
                  return (
                    <div style={TOOLTIP_CONTENT_STYLE}>
                      <div style={TOOLTIP_LABEL_STYLE}>{box.label}</div>
                      <div style={TOOLTIP_ITEM_STYLE}>
                        {formatValue(low)}–{formatValue(high)} (median{" "}
                        {formatValue(median)}, Q1 {formatValue(q1)}, Q3{" "}
                        {formatValue(q3)})
                      </div>
                    </div>
                  );
                }}
                cursor={TOOLTIP_CURSOR}
                isAnimationActive={false}
              />
              <Bar
                dataKey="range"
                isAnimationActive={false}
                maxBarSize={MAX_BOX_WIDTH}
                shape={(shapeProps: object) => (
                  <BoxShape {...shapeProps} tone={tone} />
                )}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </ChartFrame>
    );
  },
});
