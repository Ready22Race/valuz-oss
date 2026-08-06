"use client";

import { defineComponent } from "@openuidev/react-lang";

import {
  asPct,
  extentOf,
  formatValue,
  offsetPct,
  readItems,
  readLabel,
  readNumbers,
  sizePct,
  toneTint,
} from "../lib/chart";
import { ChartFrame, ChartRow } from "../lib/chart-parts";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
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

export const BoxPlot = defineComponent({
  name: "BoxPlot",
  props: BoxPlotSchema,
  description:
    "Five-number summaries side by side: whiskers from min to max, a box across the interquartile range, a tick at the median, and dots for outliers. " +
    "items is {label, min, q1, median, q3, max, outliers} — all five numbers are required and are quoted low to high; outliers is optional and drawn as separate dots. " +
    "Every distribution shares one scale, so all the values must be in the same unit; unit names it (\"days to close\", \"%\") and the numbers carry none of their own. " +
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
    const span = extentOf(boxes.flatMap((box) => [...box.five, ...box.outliers]));
    const medians = boxes.map((box) => box.five[2]);
    const outlierCount = boxes.reduce((sum, box) => sum + box.outliers.length, 0);

    const summary =
      `Box plot${title ? ` of ${title}` : ""}: ${boxes.length} distributions` +
      `${unit ? ` in ${unit}` : ""}, medians from ${formatValue(Math.min(...medians))} to ` +
      `${formatValue(Math.max(...medians))}, overall range ${formatValue(span.min)} to ` +
      `${formatValue(span.max)}` +
      (outlierCount > 0 ? `, ${outlierCount} outliers.` : ".");

    return (
      <ChartFrame slot="box-plot" summary={summary} title={title} unit={unit}>
        <div className="vgb-chart-rows">
          {boxes.map((box, index) => {
            const [low, q1, median, q3, high] = box.five;
            return (
              <ChartRow
                figure={formatValue(median)}
                key={`${box.label}-${index}`}
                label={box.label}
                sub={`${formatValue(low)}–${formatValue(high)}`}
              >
                <span
                  aria-hidden="true"
                  className="vgb-box-whisker"
                  style={{
                    backgroundColor: toneText(tone),
                    left: asPct(offsetPct(low, span)),
                    width: asPct(sizePct(high - low, span)),
                  }}
                />
                <span
                  aria-hidden="true"
                  className="vgb-box"
                  style={{
                    backgroundColor: toneTint(tone, 30),
                    borderColor: toneText(tone),
                    left: asPct(offsetPct(q1, span)),
                    width: asPct(sizePct(q3 - q1, span)),
                  }}
                />
                <span
                  aria-hidden="true"
                  className="vgb-box-median"
                  style={{
                    backgroundColor: toneText(tone),
                    left: asPct(offsetPct(median, span)),
                  }}
                />
                {box.outliers.map((outlier, dot) => (
                  <span
                    aria-hidden="true"
                    className="vgb-box-outlier"
                    key={`${outlier}-${dot}`}
                    style={{
                      backgroundColor: toneText(tone),
                      left: asPct(offsetPct(outlier, span)),
                    }}
                  />
                ))}
              </ChartRow>
            );
          })}
        </div>
      </ChartFrame>
    );
  },
});
