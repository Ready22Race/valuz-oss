"use client";

import { defineComponent } from "@openuidev/react-lang";

import { extentOf, formatValue, readItems, readLabel, readNumbers } from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import { readTextFromKeys } from "../lib/props";
import { toneText } from "../lib/tone";
import { SmallMultiplesSchema } from "./schema";

export { SmallMultipleSchema, SmallMultiplesSchema } from "./schema";

/*
 * One panel's plot box, in user units. `preserveAspectRatio="none"` lets the
 * line fill whatever width the grid cell ends up with, and
 * `vector-effect: non-scaling-stroke` keeps the stroke from stretching with it
 * — without that pair the line is a wedge, thick at one end.
 */
const PANEL_W = 100;
const PANEL_H = 32;
/** Half a stroke of headroom, so a series touching the domain edge never clips. */
const PANEL_PAD = 3;

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
    const parsed = readItems(raw.items ?? raw.series ?? raw.data ?? raw.panels).map(
      (record) => ({
        label: readLabel(record),
        values: readNumbers(record.values ?? record.data ?? record.points ?? record.series),
      }),
    );
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

    const yOf = (value: number) => {
      const ratio = flat ? 0.5 : (value - span.min) / span.size;
      return Math.round((PANEL_H - PANEL_PAD - ratio * (PANEL_H - PANEL_PAD * 2)) * 100) / 100;
    };
    const zeroY = span.min < 0 && span.max > 0 ? yOf(0) : undefined;

    const scaleText =
      `${formatValue(span.min)} to ${formatValue(span.max)}${unit ? ` ${unit}` : ""}`;
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
      <ChartFrame footnote={footnote} slot="small-multiples" summary={summary} title={title} unit={unit}>
        <div
          className="vgb-multiples"
          data-a2ui-small-multiples
          data-scale-max={String(span.max)}
          data-scale-min={String(span.min)}
        >
          {panels.map((panel, index) => {
            const last = panel.values[panel.values.length - 1] ?? 0;
            const step = panel.values.length > 1 ? PANEL_W / (panel.values.length - 1) : 0;
            const points =
              panel.values.length > 1
                ? panel.values
                    .map((value, i) => `${Math.round(i * step * 100) / 100},${yOf(value)}`)
                    .join(" ")
                : // One reading is a level, not a trend: a short tick marks where
                  // it sits on the shared scale instead of drawing a line that
                  // would imply movement.
                  `45,${yOf(panel.values[0] ?? 0)} 55,${yOf(panel.values[0] ?? 0)}`;

            return (
              <div className="vgb-multiple" key={`${panel.label}-${index}`}>
                <span className="vgb-multiple-label">{panel.label}</span>
                <svg
                  aria-hidden="true"
                  className="vgb-multiple-svg"
                  preserveAspectRatio="none"
                  viewBox={`0 0 ${PANEL_W} ${PANEL_H}`}
                >
                  {zeroY === undefined ? null : (
                    <line
                      className="vgb-multiple-zero"
                      x1={0}
                      x2={PANEL_W}
                      y1={zeroY}
                      y2={zeroY}
                    />
                  )}
                  <polyline
                    className="vgb-multiple-line"
                    fill="none"
                    points={points}
                    stroke={toneText(PANEL_TONE)}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={1.5}
                    vectorEffect="non-scaling-stroke"
                  />
                </svg>
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
