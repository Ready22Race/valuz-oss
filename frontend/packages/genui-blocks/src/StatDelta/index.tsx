"use client";

import { defineComponent } from "@openuidev/react-lang";

import { inferTrend, readTextFromKeys } from "../lib/props";
import type { Trend } from "../lib/schema";
import { toneSurface, toneText, trendGlyph, trendTone } from "../lib/tone";
import { StatDeltaSchema } from "./schema";

export { StatDeltaSchema } from "./schema";

const TREND_NAMES: readonly string[] = ["up", "down", "flat"];

export const StatDelta = defineComponent({
  name: "StatDelta",
  props: StatDeltaSchema,
  description:
    "A change on its own — no label, no base figure — drawn as a coloured pill with a direction arrow. Use it inline beside a heading or a name, or in a table cell, where the thing that moved is already named and only the movement needs saying. " +
    "value is the change written signed (\"+8.77%\", \"-1.2pp\", \"+3.4bn\"); trend (up|down|flat) states the direction and is inferred from the sign when omitted; basis is the period the change is measured over (\"vs Q3\", \"YoY\") and belongs here rather than folded into value. " +
    "tone overrides the colour and should be set only when direction and sentiment disagree — a fall in costs is a down that is good news. " +
    "When the figure itself also has to be shown, use MiniCard or StatsCard, which carry the delta already.",
  component: ({ props }) => {
    const record = props as unknown as Record<string, unknown>;
    const value = readTextFromKeys(record, ["value", "delta", "change", "text"]);
    const basis = readTextFromKeys(record, ["basis", "period", "comparedTo", "compared_to"]);

    if (!value) return null;

    /*
     * Direction, then colour. The model states `trend` maybe half the time and
     * writes a signed figure nearly always, so the sign is the fallback rather
     * than an assumed "flat" — a grey +8.77% reads as "no change", which is the
     * opposite of what it says.
     */
    const stated = readTextFromKeys(record, ["trend", "direction"]).trim().toLowerCase();
    const trend: Trend = TREND_NAMES.includes(stated) ? (stated as Trend) : inferTrend(value);

    /*
     * `trendTone` is the single place the up-is-red convention is decided for
     * every block in this package. Resolving the colour here from a glance at
     * the sign would put a second, silently diverging copy of that decision in
     * the repo.
     */
    const tone = props.tone ?? trendTone(trend);

    return (
      <span className="vgb-stat-delta" data-slot="vgb-stat-delta" data-trend={trend}>
        <span
          className="vgb-stat-delta-figure"
          style={{ color: toneText(tone), backgroundColor: toneSurface(tone) }}
        >
          {/* Decorative: the sign in `value` already carries the direction. */}
          <span aria-hidden="true">{trendGlyph(trend)}</span>
          {value}
        </span>
        {basis ? <span className="vgb-stat-delta-basis">{basis}</span> : null}
      </span>
    );
  },
});
