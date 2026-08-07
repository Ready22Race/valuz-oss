import type { CSSProperties } from "react";

/*
 * Shared chrome for the recharts-drawn chart blocks. Everything resolves to
 * `--openui-*` custom properties (the same contract as tone.ts): a block never
 * carries a literal colour and restyles with the host theme. The tooltip is
 * the chart's one interaction — a flat, token-dressed readout with no shadow
 * and no motion: these blocks are finished answers, not applications.
 */

export const CHART_MARGIN = { top: 4, right: 4, bottom: 0, left: 4 };

/** Bars never balloon to fill a sparse category axis. */
export const MAX_BAR_SIZE = 32;

export const AXIS_TICK = {
  fill: "var(--openui-text-neutral-tertiary)",
  fontSize: 11,
  fontFamily: "var(--openui-font-numbers)",
};

export const GRID_STROKE = "var(--openui-border-default)";

export const TOOLTIP_CONTENT_STYLE: CSSProperties = {
  background: "var(--openui-background)",
  border: "1px solid var(--openui-border-default)",
  borderRadius: "var(--openui-radius-s)",
  padding: "var(--openui-space-3xs) var(--openui-space-2xs)",
  fontSize: "var(--openui-font-size-2xs)",
  fontFamily: "var(--openui-font-numbers)",
  boxShadow: "none",
};

export const TOOLTIP_LABEL_STYLE: CSSProperties = {
  color: "var(--openui-text-neutral-secondary)",
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
