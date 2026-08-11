import type { CSSProperties } from "react";

/*
 * Shared chrome for the recharts-drawn chart blocks. Everything resolves to
 * `--openui-*` custom properties (the same contract as tone.ts): a block never
 * carries a literal colour and restyles with the host theme. The tooltip is
 * the chart's one interaction — a flat, token-dressed readout with no shadow
 * and no motion: these blocks are finished answers, not applications.
 */

export const CHART_MARGIN = { top: 4, right: 4, bottom: 0, left: 4 };

/*
 * Bar width cap, aligned to OpenUI's chart bars (`DEFAULT_MAX_BAR_WIDTH=12`).
 * Kept at 16 rather than 12: at 12 a two-series grouped pair reads as a single
 * skinny line in a chat column, and the cap exists to keep bars slender, not
 * to starve them.
 */
export const MAX_BAR_SIZE = 16;

/* Top-only bar radius, matching OpenUI's `getRadiusArray` for positive
   vertical bars. Negative bars keep the same small radius — 4px at the base
   is visually negligible and avoids per-value radius plumbing. */
export const BAR_RADIUS: [number, number, number, number] = [4, 4, 0, 0];

export const AXIS_TICK = {
  /* OpenUI chart axis labels are secondary ink, not the tertiary these were
     before — a chart axis is data, not decoration. */
  fill: "var(--openui-text-neutral-secondary)",
  fontSize: 11,
  fontFamily: "var(--openui-font-numbers)",
};

export const GRID_STROKE = "var(--openui-border-default)";

/*
 * Tooltip chrome, aligned to OpenUI's `.openui-chart-tooltip`: an elevated
 * readout on the foreground ink with a soft shadow — the chart's one
 * interaction, and it must read as a distinct layer from the chart surface.
 * `text-transform: capitalize` is deliberately left off: the values are data,
 * and capitalising a number or a signed figure would corrupt it.
 */
export const TOOLTIP_CONTENT_STYLE: CSSProperties = {
  background: "var(--openui-foreground)",
  border: "1px solid var(--openui-border-default)",
  borderRadius: "var(--openui-radius-l)",
  padding: "var(--openui-space-xs)",
  fontSize: 12,
  fontFamily: "var(--openui-font-label)",
  lineHeight: 1.25,
  letterSpacing: "var(--openui-letter-spacing-normal)",
  boxShadow: "var(--openui-shadow-s)",
};

export const TOOLTIP_LABEL_STYLE: CSSProperties = {
  color: "var(--openui-text-neutral-primary)",
};

export const TOOLTIP_ITEM_STYLE: CSSProperties = {
  color: "var(--openui-text-neutral-primary)",
};

export const TOOLTIP_CURSOR = {
  fill: "var(--openui-highlight-subtle)",
  stroke: "var(--openui-border-default)",
};

/* jsdom has no layout — tests render at this size, browsers measure live. */
export const CHART_INITIAL_DIMENSION = { width: 640, height: 200 };
