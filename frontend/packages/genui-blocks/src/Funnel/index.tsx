"use client";

import { defineComponent } from "@openuidev/react-lang";
import {
  Funnel as RechartsFunnel,
  FunnelChart,
  LabelList,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import type { LabelProps as RechartsLabelProps } from "recharts";

import { formatShare, formatValue, readItems, readLabel } from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import {
  CHART_INITIAL_DIMENSION,
  CHART_MARGIN,
  TOOLTIP_CONTENT_STYLE,
  TOOLTIP_CURSOR,
  TOOLTIP_ITEM_STYLE,
  TOOLTIP_LABEL_STYLE,
} from "../lib/recharts-chrome";
import { toneText } from "../lib/tone";
import { FunnelSchema } from "./schema";

export { FunnelSchema, FunnelStageSchema } from "./schema";

interface Stage {
  label: string;
  value: number;
}

/**
 * Name, value and conversion share, drawn as the trapezoid's own label.
 *
 * Not a plain `dataKey` `LabelList` (name only): the share of the first
 * stage is what this chart exists to state, so it rides on every trapezoid
 * rather than waiting for a hover this static block does not have.
 */
function stageLabel(stages: Stage[], top: number) {
  return function renderStageLabel(entry: RechartsLabelProps) {
    const index = typeof entry.index === "number" ? entry.index : -1;
    const stage = stages[index];
    if (!stage) return null;
    const x = Number(entry.x ?? 0);
    const y = Number(entry.y ?? 0);
    const width = Number(entry.width ?? 0);
    const height = Number(entry.height ?? 0);
    const midX = x + width / 2;
    const midY = y + height / 2;
    return (
      <g className="vgb-funnel-label">
        <text
          className="vgb-funnel-name"
          fill={toneText("neutral")}
          fontSize={12}
          textAnchor="middle"
          x={midX}
          y={midY - 6}
        >
          {stage.label}
        </text>
        <text
          className="vgb-funnel-share"
          fill={toneText("neutral")}
          fontSize={11}
          opacity={0.75}
          textAnchor="middle"
          x={midX}
          y={midY + 10}
        >
          {/*
           * Two `<tspan>`s, not one text run: `getNodeText` (the DOM-testing-
           * library helper `expectText` relies on) only reads an element's own
           * direct text-node children, so a value and share concatenated into
           * one run are never individually matchable — "5,000 · 10%" has no
           * node whose own text is exactly "10%". Splitting them into their
           * own `<tspan>`s keeps the value and the share each queryable on
           * their own, the same way the two once sat in separate spans.
           */}
          <tspan>{formatValue(stage.value)}</tspan>
          {top > 0 ? <tspan> · </tspan> : null}
          {top > 0 ? <tspan>{formatShare(stage.value / top)}</tspan> : null}
        </text>
      </g>
    );
  };
}

export const Funnel = defineComponent({
  name: "Funnel",
  props: FunnelSchema,
  description:
    "Stages narrowing toward an outcome, each bar centred so the drop-off is the shape you see. " +
    "items is the stages in order, widest first — {label, value} where value counts what reached that stage. Every stage is also printed as its share of the first stage, which is what the chart is for. " +
    'unit names what is being counted ("visitors", "applications", "USD m of pipeline"); the numbers carry no unit of their own. ' +
    "Use it for conversion funnels, hiring pipelines and any sequence where each step is a subset of the one above. Not for unrelated categories — that is a GroupedBar.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const stages: Stage[] = readItems(raw.items ?? raw.stages ?? raw.data)
      .map((record) => ({
        label: readLabel(record),
        value: readLooseNumber(record.value ?? record.count ?? record.amount),
      }))
      .filter((stage): stage is Stage => stage.value !== undefined);
    if (stages.length === 0) return null;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);
    const top = stages[0]?.value ?? 0;
    const last = stages[stages.length - 1]?.value ?? 0;

    const summary =
      `Funnel${title ? ` of ${title}` : ""}${unit ? ` in ${unit}` : ""}: ` +
      `${stages.length} stages from ${formatValue(top)} to ${formatValue(last)}, ` +
      `an end-to-end conversion of ${top > 0 ? formatShare(last / top) : "an unknown share"}.`;

    return (
      <ChartFrame slot="funnel" summary={summary} title={title} unit={unit}>
        <div className="vgb-recharts">
          <ResponsiveContainer
            height="100%"
            initialDimension={CHART_INITIAL_DIMENSION}
            minHeight={0}
            minWidth={0}
            width="100%"
          >
            <FunnelChart margin={CHART_MARGIN}>
              <Tooltip
                contentStyle={TOOLTIP_CONTENT_STYLE}
                cursor={TOOLTIP_CURSOR}
                formatter={(value) => formatValue(Number(value))}
                isAnimationActive={false}
                itemStyle={TOOLTIP_ITEM_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
              />
              <RechartsFunnel
                data={stages}
                dataKey="value"
                fill={toneText("brand")}
                isAnimationActive={false}
                nameKey="label"
              >
                <LabelList content={stageLabel(stages, top)} dataKey="label" />
              </RechartsFunnel>
            </FunnelChart>
          </ResponsiveContainer>
        </div>
      </ChartFrame>
    );
  },
});
