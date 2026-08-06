"use client";

import { defineComponent } from "@openuidev/react-lang";

import {
  asPct,
  formatShare,
  formatValue,
  readItems,
  readLabel,
  sizePct,
  spanOf,
} from "../lib/chart";
import { ChartFrame, ChartRow } from "../lib/chart-parts";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import { toneText } from "../lib/tone";
import { FunnelSchema } from "./schema";

export { FunnelSchema, FunnelStageSchema } from "./schema";

export const Funnel = defineComponent({
  name: "Funnel",
  props: FunnelSchema,
  description:
    "Stages narrowing toward an outcome, each bar centred so the drop-off is the shape you see. " +
    "items is the stages in order, widest first — {label, value} where value counts what reached that stage. Every stage is also printed as its share of the first stage, which is what the chart is for. " +
    "unit names what is being counted (\"visitors\", \"applications\", \"USD m of pipeline\"); the numbers carry no unit of their own. " +
    "Use it for conversion funnels, hiring pipelines and any sequence where each step is a subset of the one above. Not for unrelated categories — that is a GroupedBar.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const stages = readItems(raw.items ?? raw.stages ?? raw.data)
      .map((record) => ({
        label: readLabel(record),
        value: readLooseNumber(record.value ?? record.count ?? record.amount),
      }))
      .filter((stage): stage is { label: string; value: number } => stage.value !== undefined);
    if (stages.length === 0) return null;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);
    const top = stages[0]?.value ?? 0;
    // Scale against the widest stage rather than the first: a model that lists
    // the stages out of order would otherwise draw bars wider than the track.
    const span = spanOf(stages.map((stage) => stage.value));
    const last = stages[stages.length - 1]?.value ?? 0;

    const summary =
      `Funnel${title ? ` of ${title}` : ""}${unit ? ` in ${unit}` : ""}: ` +
      `${stages.length} stages from ${formatValue(top)} to ${formatValue(last)}, ` +
      `an end-to-end conversion of ${top > 0 ? formatShare(last / top) : "an unknown share"}.`;

    return (
      <ChartFrame slot="funnel" summary={summary} title={title} unit={unit}>
        <div className="vgb-chart-rows">
          {stages.map((stage, index) => {
            const width = sizePct(stage.value, span);
            return (
              <ChartRow
                figure={formatValue(stage.value)}
                key={`${stage.label}-${index}`}
                label={stage.label}
                // Share of the *first* stage, which is the funnel's basis. A
                // zero or missing top stage has no share to state, so it says
                // so rather than printing an infinity or a stray 0%.
                sub={top > 0 ? formatShare(stage.value / top) : "—"}
              >
                {width > 0 ? (
                  <span
                    aria-hidden="true"
                    className="vgb-chart-bar vgb-chart-bar-centred"
                    style={{ backgroundColor: toneText("brand"), width: asPct(width) }}
                  />
                ) : null}
              </ChartRow>
            );
          })}
        </div>
      </ChartFrame>
    );
  },
});
