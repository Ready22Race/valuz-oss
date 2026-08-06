"use client";

import { defineComponent } from "@openuidev/react-lang";

import { ChartFrame, ChartRow } from "../lib/chart-parts";
import {
  asPct,
  formatSigned,
  formatValue,
  offsetPct,
  readItems,
  readLabel,
  sizePct,
  spanOf,
  toneTint,
} from "../lib/chart";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import type { Tone } from "../lib/schema";
import { toneText, trendTone } from "../lib/tone";
import { BridgeChartSchema, WaterfallSchema } from "./schema";

export {
  BridgeChartSchema,
  WaterfallItemSchema,
  WaterfallKindSchema,
  WaterfallSchema,
} from "./schema";

type Kind = "start" | "delta" | "end";

interface Bar {
  label: string;
  kind: Kind;
  /** The figure printed at the row's end — for the closing bar, what was reported. */
  figure: number;
  lo: number;
  hi: number;
  tone: Tone;
  mismatch: boolean;
}

/**
 * Float tolerance for the reconciliation.
 *
 * `100 + 0.1 + 0.2` is `100.30000000000001`, so an exact comparison would flag
 * a bridge that reconciles perfectly. Anything a model actually got wrong is
 * wrong by orders of magnitude more than this.
 */
const EPSILON = 1e-6;

interface Bridge {
  bars: Bar[];
  computed: number;
  reported?: number;
  mismatch: boolean;
  start: number;
  deltas: number;
}

/**
 * The bridge, reconciled.
 *
 * The invariant this block exists for: **the running total must actually add
 * up.** The closing figure is therefore *computed* from the start plus every
 * delta, never taken on trust. When the model also supplies an `end` item, its
 * number is what the row prints — dropping it would hide the disagreement — but
 * the bar is drawn at the computed total and the row is flagged, so a bridge
 * that does not reconcile looks wrong instead of looking authoritative.
 */
function buildBridge(raw: Record<string, unknown>): Bridge | null {
  const parsed = readItems(raw.items ?? raw.steps ?? raw.data ?? raw.bars)
    .map((record) => ({
      label: readLabel(record),
      value: readLooseNumber(
        record.value ?? record.amount ?? record.delta ?? record.change,
      ),
      kind: readTextFromKeys(record, ["kind", "type", "role"]).toLowerCase(),
    }))
    .filter((item): item is { label: string; value: number; kind: string } =>
      item.value !== undefined,
    );
  if (parsed.length === 0) return null;

  // An explicit start wins wherever it sits. Only when nothing claims to be the
  // opening balance does the first item become it — otherwise a list of pure
  // contributions would lose its first contribution to the baseline.
  const declared = parsed.map((item) =>
    item.kind === "start" || item.kind === "delta" || item.kind === "end"
      ? (item.kind as Kind)
      : undefined,
  );
  const hasStart = declared.includes("start");
  const kinds: Kind[] = declared.map((kind, index) =>
    kind ?? (!hasStart && index === 0 ? "start" : "delta"),
  );

  const bars: Bar[] = [];
  let running = 0;
  let start = 0;
  let deltas = 0;
  let reported: number | undefined;
  let endLabel = "Total";

  parsed.forEach((item, index) => {
    const kind = kinds[index] ?? "delta";
    if (kind === "end") {
      // Held back: the closing bar is appended once every delta has been added.
      reported = item.value;
      endLabel = item.label || endLabel;
      return;
    }
    if (kind === "start") {
      running = item.value;
      start = item.value;
      bars.push({
        label: item.label,
        kind,
        figure: item.value,
        lo: Math.min(0, item.value),
        hi: Math.max(0, item.value),
        tone: "neutral",
        mismatch: false,
      });
      return;
    }
    const from = running;
    const to = running + item.value;
    running = to;
    deltas += 1;
    bars.push({
      label: item.label,
      kind,
      figure: item.value,
      lo: Math.min(from, to),
      hi: Math.max(from, to),
      // The house convention, decided once in lib/tone: up is red, down is
      // green. A bridge disagreeing with the metric tile above it reads as a
      // data error rather than a styling one.
      tone: trendTone(item.value >= 0 ? "up" : "down"),
      mismatch: false,
    });
  });

  const computed = running;
  const mismatch = reported !== undefined && Math.abs(reported - computed) > EPSILON;
  bars.push({
    label: endLabel,
    kind: "end",
    figure: reported ?? computed,
    lo: Math.min(0, computed),
    hi: Math.max(0, computed),
    tone: "neutral",
    mismatch,
  });

  return { bars, computed, reported, mismatch, start, deltas };
}

function WaterfallChart({ raw, slot }: { raw: Record<string, unknown>; slot: string }) {
  const bridge = buildBridge(raw);
  if (!bridge) return null;

  const title = readTextFromKeys(raw, ["title", "label"]);
  const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);
  const span = spanOf(bridge.bars.flatMap((bar) => [bar.lo, bar.hi]));
  const zero = offsetPct(0, span);

  const summary =
    `Waterfall bridge${title ? ` of ${title}` : ""}${unit ? ` in ${unit}` : ""}: ` +
    `starts at ${formatValue(bridge.start)}, ${bridge.deltas} contributions, ` +
    `ends at ${formatValue(bridge.computed)}` +
    (bridge.mismatch
      ? `, which does not match the reported ${formatValue(bridge.reported ?? 0)}.`
      : ".");

  const footnote = bridge.mismatch ? (
    <span data-chart-mismatch="true">
      {`Does not reconcile: reported ${formatValue(bridge.reported ?? 0)}, computed ` +
        `${formatValue(bridge.computed)} (difference ` +
        `${formatSigned((bridge.reported ?? 0) - bridge.computed)}).`}
    </span>
  ) : null;

  return (
    <ChartFrame footnote={footnote} slot={slot} summary={summary} title={title} unit={unit}>
      <div className="vgb-chart-rows">
        {bridge.bars.map((bar, index) => {
          const width = sizePct(bar.hi - bar.lo, span);
          return (
            <ChartRow
              figure={bar.kind === "delta" ? formatSigned(bar.figure) : formatValue(bar.figure)}
              key={`${bar.label}-${index}`}
              label={bar.label}
              mismatch={bar.mismatch}
            >
              {span.min < 0 ? (
                <span
                  aria-hidden="true"
                  className="vgb-chart-zero"
                  style={{ left: asPct(zero) }}
                />
              ) : null}
              {/* A zero contribution draws no bar at all: a 2px stub reads as a
                  small value rather than as no movement. */}
              {width > 0 ? (
                <span
                  aria-hidden="true"
                  className="vgb-chart-bar"
                  data-chart-kind={bar.kind}
                  style={{
                    backgroundColor:
                      bar.kind === "delta" ? toneText(bar.tone) : toneTint(bar.tone, 45),
                    left: asPct(offsetPct(bar.lo, span)),
                    width: asPct(width),
                  }}
                />
              ) : null}
            </ChartRow>
          );
        })}
      </div>
    </ChartFrame>
  );
}

const DESCRIPTION =
  "A bridge from an opening value to a closing one: the start, each signed contribution, and the total. " +
  "items is the walk in order — {label, value, kind} where kind is \"start\" for the opening balance, \"delta\" for a contribution (value signed: -12 is a decrease), and \"end\" for a stated closing figure. " +
  "The closing bar is always computed from start + deltas; supply an \"end\" item only to have it checked, because a reported total that disagrees is printed and flagged, not silently accepted. " +
  "unit names the basis of every value (\"USD m\", \"% of revenue\") — the numbers carry no unit of their own. Use it for revenue and margin bridges, budget variance, and headcount movement.";

export const Waterfall = defineComponent({
  name: "Waterfall",
  props: WaterfallSchema,
  description: DESCRIPTION,
  component: ({ props }) => (
    <WaterfallChart raw={props as unknown as Record<string, unknown>} slot="waterfall" />
  ),
});

export const BridgeChart = defineComponent({
  name: "BridgeChart",
  // Its own schema object, never Waterfall's — see `waterfallProps()`.
  props: BridgeChartSchema,
  description:
    `${DESCRIPTION} Identical to Waterfall — the two names exist because "bridge" and "waterfall" ` +
    "are the same chart in different vocabularies; pick whichever the answer uses.",
  component: ({ props }) => (
    <WaterfallChart raw={props as unknown as Record<string, unknown>} slot="bridge-chart" />
  ),
});
