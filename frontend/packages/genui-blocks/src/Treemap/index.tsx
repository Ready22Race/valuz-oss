"use client";

import { defineComponent } from "@openuidev/react-lang";
import {
  ResponsiveContainer,
  Treemap as RechartsTreemap,
  Tooltip,
} from "recharts";
import type { TreemapNode } from "recharts";

import {
  formatShare,
  formatValue,
  readItems,
  readLabel,
  toneTint,
} from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import type { Tone } from "../lib/schema";
import { ToneSchema } from "../lib/schema";
import {
  CHART_INITIAL_DIMENSION,
  TOOLTIP_CONTENT_STYLE,
  TOOLTIP_CURSOR,
  TOOLTIP_ITEM_STYLE,
  TOOLTIP_LABEL_STYLE,
} from "../lib/recharts-chrome";
import { toneText } from "../lib/tone";
import { TreemapSchema } from "./schema";

export { TreemapItemSchema, TreemapSchema } from "./schema";

/**
 * Slice cap.
 *
 * A treemap of sixty rectangles is not a chart, it is a texture: past a dozen
 * the tiles are smaller than their own labels and the areas stop being
 * comparable by eye. Everything below the top 11 is merged into one honest
 * "other" slice, which keeps the areas summing to the whole.
 */
const MAX_SLICES = 12;

/** Below this share a tile has no room for text, so it carries none. */
const LABEL_THRESHOLD = 0.06;

interface Slice {
  label: string;
  value: number;
  tone: Tone;
}

export const Treemap = defineComponent({
  name: "Treemap",
  props: TreemapSchema,
  description:
    "Part-to-whole as area: one flat level of rectangles, each sized by its share of the total. " +
    "items is {label, value, tone} — value must be a positive magnitude in one unit (a weight, a revenue, a headcount), never a percentage of something else and never a negative. " +
    'At most 12 slices are drawn; the remainder is merged into a single "other" tile, so the areas always sum to the whole. ' +
    'unit names what the values measure ("USD m", "employees"). Use it when the composition is the point and there are more categories than a PieChart can carry; use MiniCardBlock instead when the reader needs the exact figures.',
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const parsed = readItems(raw.items ?? raw.data ?? raw.slices).map(
      (record) => ({
        label: readLabel(record),
        value: readLooseNumber(
          record.value ?? record.amount ?? record.size ?? record.weight,
        ),
        tone: ToneSchema.safeParse(record.tone).data,
      }),
    );
    // Area cannot represent a negative or a zero. Dropping them is the only
    // honest option, and the footnote says how many went.
    const sorted: Slice[] = parsed
      .flatMap((item) =>
        item.value !== undefined && item.value > 0
          ? [
              {
                label: item.label,
                value: item.value,
                tone: item.tone ?? ("brand" as Tone),
              },
            ]
          : [],
      )
      .sort((a, b) => b.value - a.value);
    if (sorted.length === 0) return null;
    const dropped = parsed.length - sorted.length;
    const slices: Slice[] =
      sorted.length > MAX_SLICES
        ? [
            ...sorted.slice(0, MAX_SLICES - 1),
            {
              label: "other",
              value: sorted
                .slice(MAX_SLICES - 1)
                .reduce((sum, slice) => sum + slice.value, 0),
              tone: "neutral" as Tone,
            },
          ]
        : sorted;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);
    const total = slices.reduce((sum, slice) => sum + slice.value, 0);
    const largest = slices[0]?.value ?? 0;

    // Every slice is named here, with its share. That is what lets the tiles
    // ellipsis their labels without losing anything: the full list is one
    // sentence away and the cap keeps it to twelve entries.
    const summary =
      `Treemap${title ? ` of ${title}` : ""}: ${slices.length} slices totalling ` +
      `${formatValue(total)}${unit ? ` ${unit}` : ""} — ` +
      `${slices
        .map(
          (slice) =>
            `${slice.label || "unlabelled"} ${formatShare(slice.value / total)}`,
        )
        .join(", ")}.`;

    const footnote =
      sorted.length > MAX_SLICES || dropped > 0 ? (
        <span>
          {[
            sorted.length > MAX_SLICES
              ? `${sorted.length - MAX_SLICES + 1} smaller slices merged into "other".`
              : "",
            dropped > 0
              ? `${dropped} items without a positive value were not drawn.`
              : "",
          ]
            .filter(Boolean)
            .join(" ")}
        </span>
      ) : null;

    /*
     * recharts calls `content` once for the synthetic root (depth 0, the
     * whole plot) before it calls it for every leaf — only the depth-1 leaves
     * are real slices, and a leaf's `index` is exactly its position in
     * `slices`, the same order the data was handed to `<Treemap>` in.
     */
    function renderTile(node: TreemapNode) {
      if (node.depth === 0) return <g />;
      const slice = slices[node.index];
      if (!slice) return <g />;
      const x = Number(node.x);
      const y = Number(node.y);
      const width = Number(node.width);
      const height = Number(node.height);
      const share = total > 0 ? slice.value / total : 0;
      return (
        <g>
          <rect
            className="vgb-treemap-tile"
            // One hue, stepped light to dark by value: the area is the
            // measure, and a per-slice rainbow would claim the colours meant
            // something they do not.
            fill={toneTint(
              slice.tone,
              15 + (largest > 0 ? slice.value / largest : 0) * 40,
            )}
            height={height}
            width={width}
            x={x}
            y={y}
          />
          {share >= LABEL_THRESHOLD ? (
            <>
              <text
                className="vgb-treemap-label"
                fill={toneText("neutral")}
                fontSize={12}
                x={x + 6}
                y={y + 16}
              >
                {slice.label}
              </text>
              <text
                className="vgb-treemap-value"
                fill={toneText("neutral")}
                fontSize={11}
                opacity={0.75}
                x={x + 6}
                y={y + 32}
              >
                {formatShare(share)}
              </text>
            </>
          ) : null}
        </g>
      );
    }

    return (
      <ChartFrame
        footnote={footnote}
        slot="treemap"
        summary={summary}
        title={title}
        unit={unit}
      >
        <div className="vgb-recharts" data-a2ui-treemap>
          <ResponsiveContainer
            height="100%"
            initialDimension={CHART_INITIAL_DIMENSION}
            minHeight={0}
            minWidth={0}
            width="100%"
          >
            <RechartsTreemap
              content={renderTile}
              // `Slice` carries no index signature — recharts' `TreemapDataType`
              // wants one so nested `children` stays structurally open, which
              // this flat, one-level treemap never uses.
              data={slices as unknown as ReadonlyArray<Record<string, unknown>>}
              dataKey="value"
              isAnimationActive={false}
              nameKey="label"
            >
              <Tooltip
                contentStyle={TOOLTIP_CONTENT_STYLE}
                cursor={TOOLTIP_CURSOR}
                formatter={(value) => formatValue(Number(value))}
                isAnimationActive={false}
                itemStyle={TOOLTIP_ITEM_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
              />
            </RechartsTreemap>
          </ResponsiveContainer>
        </div>
      </ChartFrame>
    );
  },
});
