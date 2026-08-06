"use client";

import { defineComponent } from "@openuidev/react-lang";

import { asPct, formatValue, readItems, readLabel, sizePct, spanOf } from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import { toneText } from "../lib/tone";
import { HistogramSchema } from "./schema";

export { HistogramBinSchema, HistogramSchema } from "./schema";

export const Histogram = defineComponent({
  name: "Histogram",
  props: HistogramSchema,
  description:
    "A distribution as columns of counts — how many observations fell in each bin, in bin order. " +
    "bins is {label, count} where label names the interval (\"0–10\", \"10–20\") and count is how many landed in it; keep the bins contiguous and in ascending order, because the shape is the answer. " +
    "unit names what is being counted (\"companies\", \"trading days\"); title should state what was measured and over what period. " +
    "Use it for a spread — returns, latencies, ages, scores. For counts of unrelated named categories use GroupedBar instead: a histogram's bins are a scale, not a list.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const bins = readItems(raw.bins ?? raw.items ?? raw.data ?? raw.buckets)
      .map((record) => ({
        label: readLabel(record) || readTextFromKeys(record, ["range", "bucket", "bin"]),
        count: readLooseNumber(record.count ?? record.value ?? record.n ?? record.frequency),
      }))
      .filter((bin): bin is { label: string; count: number } => bin.count !== undefined);
    if (bins.length === 0) return null;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);
    const tone = props.tone ?? "brand";
    // A negative count is not a height. Clamp for geometry only — the printed
    // figure stays whatever the model said, so the error is visible.
    const heights = bins.map((bin) => Math.max(0, bin.count));
    const span = spanOf(heights);
    const total = bins.reduce((sum, bin) => sum + bin.count, 0);
    const tallest = bins.reduce((best, bin) => (bin.count > best.count ? bin : best), bins[0]!);

    const summary =
      `Histogram${title ? ` of ${title}` : ""}: ${bins.length} bins` +
      `${unit ? ` counting ${unit}` : ""}, ${formatValue(total)} in total, ` +
      `tallest bin ${tallest.label || "unlabelled"} at ${formatValue(tallest.count)}.`;

    return (
      <ChartFrame slot="histogram" summary={summary} title={title} unit={unit}>
        {/*
         * The plot scrolls inside its own box rather than squeezing the bins.
         * With 50 bins in a chat column there is no width at which every label
         * still fits, and the two alternatives both lose: rotated labels are
         * unreadable at this size, truncated ones need a hover to recover and
         * this block is static by design. So each bin keeps a floor, labels
         * wrap inside it, and the reader scrolls.
         */}
        <div className="vgb-scroll-x">
          <div className="vgb-histogram" data-a2ui-histogram>
            {bins.map((bin, index) => (
              <div className="vgb-histogram-bin" key={`${bin.label}-${index}`}>
                <span className="vgb-histogram-count">{formatValue(bin.count)}</span>
                <span aria-hidden="true" className="vgb-histogram-column">
                  {heights[index]! > 0 ? (
                    <span
                      className="vgb-histogram-fill"
                      style={{
                        backgroundColor: toneText(tone),
                        height: asPct(sizePct(heights[index]!, span)),
                      }}
                    />
                  ) : null}
                </span>
                <span className="vgb-histogram-label">{bin.label}</span>
              </div>
            ))}
          </div>
        </div>
      </ChartFrame>
    );
  },
});
