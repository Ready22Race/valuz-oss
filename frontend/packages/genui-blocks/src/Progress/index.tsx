"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readLooseNumber, readText } from "../lib/props";
import { ProgressSchema } from "./schema";

export { ProgressSchema } from "./schema";

/**
 * A whole percent in 0–100, from whatever the model sent.
 *
 * Models write `"72%"`, `72.4`, `120` and `-3`. Every one of those has to land
 * on a single number, because that number is drawn *and* announced: a bar
 * painted at 100% while `aria-valuenow` says 120 is worse than either mistake
 * alone, since a sighted and a screen-reader user then read different data.
 */
function readPercent(value: unknown): number {
  const parsed = readLooseNumber(typeof value === "string" ? value.replace(/%/g, "") : value);
  if (parsed === undefined || !Number.isFinite(parsed)) return 0;
  return Math.round(Math.min(100, Math.max(0, parsed)));
}

export const Progress = defineComponent({
  name: "Progress",
  props: ProgressSchema,
  description:
    "A determinate progress bar: how far through something already is, as a filled track with the figure beside it. " +
    "percent is a number from 0 to 100 (not a fraction — write 72, not 0.72); label names what is being measured (\"Indexing filings\"), and detail is an optional line of counts under the bar (\"1,204 of 1,670 documents\"). " +
    "Use it to report a stated completion figure. It is a picture of a number, not a live indicator: nothing is in flight, nothing polls, and there is no indeterminate or spinner mode — if you have no figure, do not use this block.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const percent = readPercent(raw.percent);
    const label = readText(raw.label).trim();
    const detail = readText(raw.detail).trim();
    const text = `${percent}%`;

    return (
      <div className="vgb-progress" data-slot="vgb-progress">
        <div className="vgb-progress-heading">
          {label ? <span className="vgb-progress-label">{label}</span> : null}
          {/* Always rendered, even unlabelled: the figure is the block. */}
          <span className="vgb-progress-percent">{text}</span>
        </div>
        {/*
         * A bare `<div>` announces nothing at all — no role, no value, no name
         * — so the bar is invisible to a screen reader however it is painted.
         * `role="progressbar"` plus the three value attributes is the whole
         * contract, and `aria-valuenow` is the same clamped number the fill is
         * drawn from so the two can never disagree.
         */}
        <div
          className="vgb-progress-track"
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuetext={text}
          // Only set when there is one: an empty string is a worse accessible
          // name than none, because it suppresses the fallback.
          aria-label={label || undefined}
        >
          <span className="vgb-progress-fill" style={{ width: `${percent}%` }} />
        </div>
        {detail ? <p className="vgb-progress-detail">{detail}</p> : null}
      </div>
    );
  },
});
