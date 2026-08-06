"use client";

import { defineComponent } from "@openuidev/react-lang";

import {
  extentOf,
  formatValue,
  offsetPct,
  readItems,
  readLabel,
  toneTint,
} from "../lib/chart";
import type { Span } from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import type { Tone } from "../lib/schema";
import { ToneSchema } from "../lib/schema";
import { toneText } from "../lib/tone";
import { BubbleChartSchema } from "./schema";

export { BubbleChartSchema, BubblePointSchema } from "./schema";

/*
 * Plot box, in user units.
 *
 * `preserveAspectRatio` is left at its default (`xMidYMid meet`) and the CSS
 * box is given the matching `aspect-ratio`, because a bubble has to stay round:
 * the `preserveAspectRatio="none"` trick the line charts in this family use
 * would squash every circle into an ellipse whose *area* — the one thing this
 * chart encodes — depends on the width of the column it landed in.
 */
const VIEW_W = 160;
const VIEW_H = 100;

/**
 * Radius range, in the same user units.
 *
 * `R_MAX` is what the largest value gets; everything else is scaled down from
 * it by the square root of its share. `PAD` is `R_MAX` plus a little, so the
 * biggest bubble sitting on the domain's edge is still drawn inside the box.
 */
const R_MAX = 13;
const R_MIN = 2.5;
const PAD = R_MAX + 3;

/** Beyond this the picture is a texture, not a chart. */
const MAX_POINTS = 60;

/** Above this the per-point key is longer than the plot; the summary carries it. */
const MAX_KEY = 16;

interface Point {
  x: number;
  y: number;
  size: number;
  label: string;
  tone: Tone;
}

/**
 * Radius from value, **through area**.
 *
 * This is the whole reason the block exists rather than being a ScatterChart
 * with a `size` channel bolted on. A reader compares two bubbles by the ink in
 * them, so the encoded quantity has to be πr² — mapping the value to `r`
 * directly overstates the large end by the square, which is the single most
 * common way a bubble chart lies (a 4× value drawn 4× wide looks 16× bigger).
 *
 * `R_MIN` is a floor, not part of the encoding: below it a bubble is smaller
 * than the stroke around it and stops being visible at all. It only ever
 * applies to values so far below the maximum that no comparison was legible
 * anyway, and the figure is printed in the key regardless.
 */
function radiusOf(size: number, maxSize: number): number {
  if (!(size > 0) || !(maxSize > 0)) return R_MIN;
  const exact = R_MAX * Math.sqrt(size / maxSize);
  return Math.round(Math.max(R_MIN, exact) * 100) / 100;
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * Where a value sits in its domain, as a percentage.
 *
 * `offsetPct` alone would pin an all-equal axis — one point, or five sharing an
 * x — to the far left, which reads as "lowest possible" rather than as "there
 * is no spread here". A domain with no width puts its marks down the middle,
 * the same answer `Sparkline` gives a flat series.
 */
function positionPct(value: number, span: Span): number {
  return span.max === span.min ? 50 : offsetPct(value, span);
}

export const BubbleChart = defineComponent({
  name: "BubbleChart",
  props: BubbleChartSchema,
  description:
    "A scatter whose third dimension is bubble size: each point carries an x, a y, and a magnitude drawn as area. " +
    "points is {x, y, size, label, tone} — size must be a positive magnitude in one unit (a revenue, a headcount, a market cap), never a percentage change or anything that can go negative. " +
    "Bubble area is proportional to size, so a bubble twice as wide is four times the value; name what size means in sizeLabel or the reader will guess. " +
    "xLabel and yLabel name the two axes with their units (\"Revenue growth %\"), and every point must be the same kind of thing measured the same way. " +
    "Use it for three-variable comparisons across a dozen or so subjects; use OpenUI's ScatterChart when there is no third variable, and MiniCardBlock when the reader needs the exact figures.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const parsed = readItems(raw.points ?? raw.items ?? raw.data ?? raw.bubbles).map(
      (record) => ({
        x: readLooseNumber(record.x ?? record.xValue),
        y: readLooseNumber(record.y ?? record.yValue),
        size: readLooseNumber(record.size ?? record.value ?? record.weight ?? record.r),
        label: readLabel(record),
        tone: ToneSchema.safeParse(record.tone).data,
      }),
    );

    // A point with no position cannot be placed at all. A point with no size
    // still has a position, so it is kept and drawn at the floor — the footnote
    // says how many, so an outline is never mistaken for a small value.
    const usable: Point[] = parsed.flatMap((item) =>
      item.x !== undefined && item.y !== undefined
        ? [
            {
              x: item.x,
              y: item.y,
              size: item.size ?? 0,
              label: item.label,
              tone: item.tone ?? ("brand" as Tone),
            },
          ]
        : [],
    );
    // Zero points is not a chart. It renders nothing at all rather than an
    // empty box holding its height.
    if (usable.length === 0) return null;

    const points = usable.slice(0, MAX_POINTS);
    const truncated = usable.length - points.length;
    const sizeless = points.filter((point) => !(point.size > 0)).length;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const xName = readTextFromKeys(raw, ["xLabel", "xAxis", "x_label"]) || "x";
    const yName = readTextFromKeys(raw, ["yLabel", "yAxis", "y_label"]) || "y";
    const sizeName =
      readTextFromKeys(raw, ["sizeLabel", "sizeAxis", "size_label", "unit"]) || "size";

    // Position is the data here, not length, so the domain covers the values and
    // nothing more: forcing zero in would push a cluster of 98–100 into a single
    // sliver at one edge. (`spanOf` is the opposite choice, for bars.)
    const xSpan = extentOf(points.map((point) => point.x));
    const ySpan = extentOf(points.map((point) => point.y));
    const sizes = points.map((point) => point.size);
    const maxSize = Math.max(0, ...sizes);
    const largest = points.reduce(
      (best, point) => (point.size > best.size ? point : best),
      points[0] as Point,
    );

    const summary =
      `Bubble chart${title ? ` of ${title}` : ""}: ${points.length} points. ` +
      `${xName} runs ${formatValue(xSpan.min)} to ${formatValue(xSpan.max)} left to right, ` +
      `${yName} runs ${formatValue(ySpan.min)} to ${formatValue(ySpan.max)} bottom to top, ` +
      `and bubble area is proportional to ${sizeName}, ` +
      `${formatValue(Math.min(...sizes))} to ${formatValue(maxSize)}. ` +
      `Largest: ${largest.label || "unlabelled"} at ${formatValue(largest.x)}, ` +
      `${formatValue(largest.y)}.`;

    const footnote = (
      <span>
        {[
          // Always stated, not only when something is odd: the reader cannot
          // tell area encoding from radius encoding by looking, and the two
          // differ by a square.
          `Bubble area — not radius — is proportional to ${sizeName}, ` +
            "so a bubble twice as wide is four times the value.",
          sizeless > 0
            ? `${sizeless} point${sizeless === 1 ? " has" : "s have"} no positive ` +
              `${sizeName} and ${sizeless === 1 ? "is" : "are"} drawn as an outline ` +
              "at the smallest size, since zero has no area."
            : "",
          points.length > MAX_KEY
            ? `Individual labels are listed up to ${MAX_KEY} points; above that the chart ` +
              "shows the distribution rather than naming every subject."
            : "",
          truncated > 0
            ? `${truncated} further points were not drawn — past ${MAX_POINTS} bubbles ` +
              "the plot is a texture rather than a comparison."
            : "",
        ]
          .filter(Boolean)
          .join(" ")}
      </span>
    );

    return (
      <ChartFrame footnote={footnote} slot="bubble-chart" summary={summary} title={title}>
        <div className="vgb-bubble">
          {/* The y axis reads bottom-to-top as a column of three lines rather
              than as rotated text: rotated labels are unreadable at this size,
              and a CJK axis name rotated 90° is worse still. */}
          <span className="vgb-bubble-axis vgb-bubble-axis-y">
            <span className="vgb-chart-sub">{formatValue(ySpan.max)}</span>
            <span className="vgb-bubble-axis-name">{yName}</span>
            <span className="vgb-chart-sub">{formatValue(ySpan.min)}</span>
          </span>
          <svg aria-hidden="true" className="vgb-bubble-plot" viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}>
            {points.map((point, index) => {
              const cx = round(
                PAD + (positionPct(point.x, xSpan) / 100) * (VIEW_W - PAD * 2),
              );
              const cy = round(
                VIEW_H - PAD - (positionPct(point.y, ySpan) / 100) * (VIEW_H - PAD * 2),
              );
              const positive = point.size > 0;
              return (
                <circle
                  className="vgb-bubble-dot"
                  cx={cx}
                  cy={cy}
                  data-bubble-size={point.size}
                  fill={positive ? toneTint(point.tone, 38) : "none"}
                  key={`${point.label}-${index}`}
                  r={radiusOf(point.size, maxSize)}
                  stroke={toneText(point.tone)}
                  strokeDasharray={positive ? undefined : "2 2"}
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              );
            })}
          </svg>
          <span className="vgb-bubble-axis vgb-bubble-axis-x">
            <span className="vgb-chart-sub">{formatValue(xSpan.min)}</span>
            <span className="vgb-bubble-axis-name">{xName}</span>
            <span className="vgb-chart-sub">{formatValue(xSpan.max)}</span>
          </span>
        </div>
        {/*
         * The size key: the smallest bubble and the largest, drawn at the size
         * they are drawn at, with what they mean and the values they stand for.
         *
         * An area encoding is undecodable without one. The reader can see that
         * one bubble is bigger, but "bigger by how much" needs a reference, and
         * naming the measure in prose is not the same as showing the two ends
         * of it side by side at their true relative size.
         */}
        <span className="vgb-bubble-size-key">
          <svg aria-hidden="true" className="vgb-bubble-size-svg" viewBox="0 0 62 30">
            <circle
              className="vgb-bubble-legend-dot"
              cx={16}
              cy={15}
              fill={toneTint("brand", 38)}
              r={radiusOf(Math.min(...sizes), maxSize)}
              stroke={toneText("brand")}
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
            <circle
              className="vgb-bubble-legend-dot"
              cx={45}
              cy={15}
              fill={toneTint("brand", 38)}
              r={radiusOf(maxSize, maxSize)}
              stroke={toneText("brand")}
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          </svg>
          <span className="vgb-bubble-axis-name">{sizeName}</span>
          <span className="vgb-chart-sub">
            {`${formatValue(Math.min(...sizes))} – ${formatValue(maxSize)}`}
          </span>
        </span>
        {/* Labels go under the plot, never beside the bubbles: a name set next
            to a mark overlaps its neighbours the moment two points are close,
            and there is no hover here to recover it with. */}
        {points.length <= MAX_KEY ? (
          <ul className="vgb-bubble-key">
            {points.map((point, index) =>
              point.label ? (
                <li className="vgb-bubble-key-item" key={`${point.label}-${index}`}>
                  <span
                    aria-hidden="true"
                    className="vgb-chart-swatch"
                    style={{ backgroundColor: toneText(point.tone) }}
                  />
                  <span className="vgb-bubble-key-name">{point.label}</span>
                  <span className="vgb-chart-sub">
                    {`${formatValue(point.x)}, ${formatValue(point.y)} · ` +
                      `${sizeName} ${formatValue(point.size)}`}
                  </span>
                </li>
              ) : null,
            )}
          </ul>
        ) : null}
      </ChartFrame>
    );
  },
});
