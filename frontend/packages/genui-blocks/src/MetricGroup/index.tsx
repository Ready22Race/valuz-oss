"use client";

import { defineComponent } from "@openuidev/react-lang";

import { inferTrend, readRecord, readTextFromKeys, toArray } from "../lib/props";
import type { Trend } from "../lib/schema";
import { toneText, trendGlyph, trendTone } from "../lib/tone";
import { MetricGroupSchema } from "./schema";

export { MetricGroupItemSchema, MetricGroupSchema } from "./schema";

const TREND_NAMES: readonly string[] = ["up", "down", "flat"];

/**
 * Direction of a change, from the stated trend or, failing that, the sign of
 * the change figure itself — the model writes `"-1.8pp"` and rarely says which
 * way that points. Returns `undefined` when there is no change to point at, so
 * a metric without a delta draws no arrow rather than a flat one.
 */
function readTrend(record: Record<string, unknown>, delta: string): Trend | undefined {
  const stated = readTextFromKeys(record, ["trend", "direction"]).trim().toLowerCase();
  if (TREND_NAMES.includes(stated)) return stated as Trend;
  return delta ? inferTrend(delta) : undefined;
}

interface Item {
  delta: string;
  label: string;
  trend: Trend | undefined;
  value: string;
}

function readItems(value: unknown): Item[] {
  return toArray(value)
    .map((entry): Item => {
      const record = readRecord(entry);
      const delta = readTextFromKeys(record, ["delta", "change", "changePct", "change_pct"]);
      return {
        delta,
        label: readTextFromKeys(record, ["label", "title", "name"]),
        trend: readTrend(record, delta),
        value: readTextFromKeys(record, ["value", "amount", "text"]),
      };
    })
    .filter((item) => item.label || item.value);
}

export const MetricGroup = defineComponent({
  name: "MetricGroup",
  props: MetricGroupSchema,
  description:
    "Several related figures under one heading, sharing one stated basis. Reach for it when the figures only mean something together — the segments of a revenue split, the ratios of one quarter, the same measure across peers. " +
    "items is the data (label, value already formatted with its unit, plus optional delta and trend up|down|flat); title heads the group. " +
    "basis is the line that makes the set comparable — the as-of date, period or currency the figures share (\"FY2024, unaudited\", \"as of 31 Mar, RMB mn\") — and you should always write it: a column of figures with no shared basis invites the reader to compare things that are not comparable. " +
    "Use MiniCardBlock instead when the figures are unrelated KPIs, and StatsCard when one figure is the point.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const items = readItems(raw.items ?? raw.metrics ?? raw.data);
    const title = readTextFromKeys(raw, ["title", "label", "heading"]);
    const basis = readTextFromKeys(raw, ["basis", "asOf", "as_of", "period"]);

    /*
     * No figures, no group — not a heading and a basis line framing an empty
     * space. A group whose items failed to arrive should read as absent, which
     * is the truth, rather than as a set of figures that happens to be empty.
     */
    if (items.length === 0) return null;

    return (
      <section className="vgb-metric-group" data-slot="vgb-metric-group">
        {title ? <h4 className="vgb-metric-group-title">{title}</h4> : null}
        <div className="vgb-metric-group-grid">
          {items.map((item, index) => (
            <div className="vgb-metric-group-item" key={`${item.label}-${index}`}>
              {item.label ? (
                <span className="vgb-metric-group-label">{item.label}</span>
              ) : null}
              {item.value ? (
                <span className="vgb-metric-group-value">{item.value}</span>
              ) : null}
              {item.delta ? (
                <span
                  className="vgb-metric-group-delta"
                  style={{ color: toneText(trendTone(item.trend)) }}
                >
                  {item.trend ? <span aria-hidden="true">{trendGlyph(item.trend)}</span> : null}
                  {item.delta}
                </span>
              ) : null}
            </div>
          ))}
        </div>
        {basis ? <p className="vgb-metric-group-basis">{basis}</p> : null}
      </section>
    );
  },
});
