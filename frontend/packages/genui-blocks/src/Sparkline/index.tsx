"use client";

import { defineComponent } from "@openuidev/react-lang";

import { formatValue, readNumbers, trendOf } from "../lib/chart";
import { toneText } from "../lib/tone";
import { SparklineSchema } from "./schema";

export { SparklineSchema } from "./schema";

/*
 * Plot box, in user units. `preserveAspectRatio="none"` lets the line stretch to
 * whatever width the cell gives it, and `vector-effect: non-scaling-stroke`
 * keeps the stroke from stretching with it — without that pair the line is a
 * wedge, thick at one end, in any cell that is not exactly 100 units wide.
 */
const WIDTH = 100;
const HEIGHT = 24;
/* Half a stroke of headroom top and bottom, so an extreme never clips. */
const PAD = 2;

function pointsOf(values: number[]): string {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;
  const step = WIDTH / (values.length - 1);
  return values
    .map((value, index) => {
      const x = index * step;
      // A flat series has no range to scale against; draw it down the middle
      // rather than dividing by zero and painting nothing.
      const ratio = span > 0 ? (value - min) / span : 0.5;
      const y = HEIGHT - PAD - ratio * (HEIGHT - PAD * 2);
      return `${Math.round(x * 100) / 100},${Math.round(y * 100) / 100}`;
    })
    .join(" ");
}

export const Sparkline = defineComponent({
  name: "Sparkline",
  props: SparklineSchema,
  description:
    "A bare trend line — no axes, no labels, no grid — sized to sit inside a table cell or beside a single metric. " +
    "values is the series in time order, oldest first, already in one unit (do not mix a level and a percentage); label names what is plotted and is read out to assistive tech, so write it with its unit (\"Weekly revenue, USD m\"). " +
    "Reach for it to show shape beside a number, never as the answer's main chart — use a MiniCard or Metric for the figure and OpenUI's LineChart when the reader has to read values off the line. Fewer than two points renders nothing.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const values = readNumbers(raw.values ?? raw.data ?? raw.points ?? raw.series);
    // One point is not a trend and zero points is not a chart. Both render
    // nothing at all — an empty plot holding its height is the defect.
    if (values.length < 2) return null;

    const tone = props.tone;
    const label = typeof raw.label === "string" ? raw.label : "";
    const first = values[0] ?? 0;
    const last = values[values.length - 1] ?? 0;
    const direction = trendOf(first, last);
    const summary =
      `Sparkline${label ? ` of ${label}` : ""}: ${values.length} points, ` +
      `${formatValue(first)} to ${formatValue(last)} (${direction}), ` +
      `low ${formatValue(Math.min(...values))}, high ${formatValue(Math.max(...values))}.`;

    return (
      <span
        className="vgb-sparkline"
        data-slot="vgb-sparkline"
        data-a2ui-component="sparkline"
        data-a2ui-trend={direction}
      >
        <span className="vgb-chart-sr">{summary}</span>
        <svg
          aria-hidden="true"
          className="vgb-sparkline-svg"
          preserveAspectRatio="none"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        >
          <polyline
            fill="none"
            points={pointsOf(values)}
            stroke={toneText(tone)}
            strokeLinecap="round"
            strokeLinejoin="round"
            /* The house line weight is 2px, which is for a full plot; at 20px
               tall that reads as a bar rather than a line. */
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </span>
    );
  },
});
