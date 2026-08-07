"use client";

import { defineComponent } from "@openuidev/react-lang";
import {
  Line,
  LineChart,
  ReferenceLine,
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
} from "../lib/chart";
import type { Span } from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import { readTextFromKeys } from "../lib/props";
import {
  CHART_INITIAL_DIMENSION,
  CHART_MARGIN,
  GRID_STROKE,
  TOOLTIP_CONTENT_STYLE,
  TOOLTIP_CURSOR,
  TOOLTIP_ITEM_STYLE,
} from "../lib/recharts-chrome";
import { toneText } from "../lib/tone";
import { SmallMultiplesSchema } from "./schema";

export { SmallMultipleSchema, SmallMultiplesSchema } from "./schema";

/**
 * Panel cap.
 *
 * Past this the panels are smaller than their own labels and the grid stops
 * being readable at a glance, which is the only thing it is for.
 */
const MAX_PANELS = 16;

/** Every panel wears one colour: they are the same measure, not six series. */
const PANEL_TONE = "brand" as const;

interface Panel {
  label: string;
  values: number[];
}

/**
 * A usable, shared Y domain from `extentOf`.
 *
 * All-equal and all-zero data has no range to scale against — a linear scale
 * divides by that width — so instead of collapsing every panel onto one edge,
 * the domain is padded a unit either side. Every value then lands exactly in
 * the middle of every panel, which is the honest picture: nothing moved.
 */
function sharedDomain(span: Span): [number, number] {
  return span.min === span.max
    ? [span.min - 1, span.max + 1]
    : [span.min, span.max];
}

export const SmallMultiples = defineComponent({
  name: "SmallMultiples",
  props: SmallMultiplesSchema,
  description:
    "The same tiny line chart repeated once per category, every panel drawn against one shared scale so the panels are comparable by eye. " +
    "items is {label, values} where values is that category's series in order, oldest first; every series must be the same measure in the same unit, named by unit. " +
    "One domain is computed across every panel and stated under the grid — that shared scale is the entire point, so never use this for series of different magnitudes or different measures (a per-panel scale would make it a decorative grid of unrelated lines). " +
    "Use it to compare the shape of a dozen categories at once; use GroupedBar when the reader has to compare values rather than shapes, and Sparkline for a single series beside a figure.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const parsed = readItems(
      raw.items ?? raw.series ?? raw.data ?? raw.panels,
    ).map((record) => ({
      label: readLabel(record),
      values: readNumbers(
        record.values ?? record.data ?? record.points ?? record.series,
      ),
    }));
    const usable: Panel[] = parsed.filter((panel) => panel.values.length > 0);
    // Zero panels is not a chart. It renders nothing at all rather than an empty
    // grid holding its height.
    if (usable.length === 0) return null;

    const panels = usable.slice(0, MAX_PANELS);
    const truncated = usable.length - panels.length;
    const empty = parsed.length - usable.length;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);

    /*
     * **One domain across every panel.** Computed from the flattened values of
     * all of them, never per panel: a panel scaled to its own range draws a
     * series moving 0.1 with exactly the shape of one moving 1,000, so the grid
     * would invite precisely the comparison it makes invalid.
     *
     * `extentOf` rather than `spanOf` because a line's *position* is the data,
     * not its length — anchoring at zero would flatten every panel of a series
     * that lives between 98 and 100.
     */
    const span = extentOf(panels.flatMap((panel) => panel.values));
    // All-equal and all-zero data has no range to scale against. Every panel is
    // then drawn down the middle, which is the honest picture: nothing moved.
    const flat = span.max === span.min;
    const yDomain = sharedDomain(span);
    // A shared domain that straddles zero gets a reference line in every panel,
    // so a positive run and a negative one are told apart by more than colour.
    const hasZeroLine = span.min < 0 && span.max > 0;

    const scaleText = `${formatValue(span.min)} to ${formatValue(span.max)}${unit ? ` ${unit}` : ""}`;
    const summary =
      `Small multiples${title ? ` of ${title}` : ""}: ${panels.length} panels ` +
      `(${panels.map((panel) => panel.label || "unlabelled").join(", ")}), ` +
      `all on one shared scale of ${scaleText}. ` +
      panels
        .map(
          (panel) =>
            `${panel.label || "unlabelled"} ends at ` +
            `${formatValue(panel.values[panel.values.length - 1] ?? 0)}`,
        )
        .join("; ") +
      ".";

    const footnote = (
      <span>
        {[
          // Always stated. A grid of lines gives the reader no way to tell a
          // shared scale from a per-panel one, and the two say opposite things.
          flat
            ? `Every value is ${formatValue(span.min)}${unit ? ` ${unit}` : ""}: there is ` +
              "no range to scale against, so every line is drawn down the middle."
            : `Every panel is drawn against one shared scale, ${scaleText}, so panel ` +
              "heights are comparable with each other.",
          empty > 0
            ? `${empty} series had no values and ${empty === 1 ? "was" : "were"} not drawn.`
            : "",
          truncated > 0
            ? `${truncated} further panels were not drawn — past ${MAX_PANELS} each panel ` +
              "is smaller than its own label."
            : "",
        ]
          .filter(Boolean)
          .join(" ")}
      </span>
    );

    return (
      <ChartFrame
        footnote={footnote}
        slot="small-multiples"
        summary={summary}
        title={title}
        unit={unit}
      >
        <div
          className="vgb-multiples"
          data-a2ui-small-multiples
          data-scale-max={String(span.max)}
          data-scale-min={String(span.min)}
        >
          {panels.map((panel, index) => {
            const last = panel.values[panel.values.length - 1] ?? 0;
            // One reading is a level, not a trend: a single point with a visible
            // dot marks where it sits on the shared scale, rather than a line
            // that would imply movement recharts has no second point to draw.
            const single = panel.values.length === 1;
            const data = single
              ? [{ index: 0, value: panel.values[0] ?? 0 }]
              : panel.values.map((value, i) => ({ index: i, value }));

            return (
              <div className="vgb-multiple" key={`${panel.label}-${index}`}>
                <span className="vgb-multiple-label">{panel.label}</span>
                <div className="vgb-recharts vgb-recharts-small">
                  <ResponsiveContainer
                    width="100%"
                    height="100%"
                    minWidth={0}
                    minHeight={0}
                    initialDimension={CHART_INITIAL_DIMENSION}
                  >
                    <LineChart data={data} margin={CHART_MARGIN}>
                      <XAxis
                        dataKey="index"
                        domain={[0, Math.max(1, panel.values.length - 1)]}
                        hide
                        type="number"
                      />
                      <YAxis domain={yDomain} hide type="number" />
                      {hasZeroLine ? (
                        <ReferenceLine
                          className="vgb-multiple-zero"
                          stroke={GRID_STROKE}
                          strokeOpacity={0.6}
                          y={0}
                        />
                      ) : null}
                      <Tooltip
                        content={({ active, payload }) => {
                          if (!active || !payload?.length) return null;
                          const point = payload[0]?.payload as
                            { value: number } | undefined;
                          if (!point) return null;
                          return (
                            <div style={TOOLTIP_CONTENT_STYLE}>
                              <div style={TOOLTIP_ITEM_STYLE}>
                                {`${formatValue(point.value)}${unit ? ` ${unit}` : ""}`}
                              </div>
                            </div>
                          );
                        }}
                        cursor={TOOLTIP_CURSOR}
                        isAnimationActive={false}
                      />
                      <Line
                        className="vgb-multiple-line"
                        dataKey="value"
                        dot={
                          single
                            ? {
                                r: 3,
                                fill: toneText(PANEL_TONE),
                                stroke: "none",
                              }
                            : false
                        }
                        isAnimationActive={false}
                        stroke={toneText(PANEL_TONE)}
                        strokeWidth={1.5}
                        type="linear"
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <span className="vgb-multiple-figure">
                  <span className="vgb-chart-value">{formatValue(last)}</span>
                  <span className="vgb-chart-sub">
                    {/* Spaced en dash, not a bare one: "-2–3" reads as one
                        mangled number rather than as a range from -2 to 3. */}
                    {`${formatValue(Math.min(...panel.values))} – ` +
                      `${formatValue(Math.max(...panel.values))}`}
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      </ChartFrame>
    );
  },
});
