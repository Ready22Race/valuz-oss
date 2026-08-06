"use client";

import type { ReactNode } from "react";

import { seriesTone } from "./chart";
import type { Tone } from "./schema";
import { toneText } from "./tone";

/**
 * The chrome every hand-drawn chart shares.
 *
 * Components only — the geometry helpers live in `./chart.ts`, so this file
 * stays a Fast-Refresh boundary.
 */

/**
 * Frame, heading and accessible summary.
 *
 * The summary is the load-bearing part. A chart drawn as `<span>`s and `<rect>`s
 * announces *nothing* to a screen reader — not "chart", not the values, not even
 * that something is there — so every block passes one sentence saying what the
 * picture shows. It leads the figure so it is read before the labels, and it is
 * the only place a value that could not fit on screen still exists.
 *
 * Deliberately frameless: no card, no background. These blocks are dropped into
 * Cards, ReportPages and Stacks that already draw a surface, and a second frame
 * inside the first reads as a bug. Wrap in a Card when a frame is wanted.
 */
export function ChartFrame({
  slot,
  summary,
  title,
  unit,
  footnote,
  children,
}: {
  slot: string;
  summary: string;
  title?: string;
  unit?: string;
  footnote?: ReactNode;
  children: ReactNode;
}) {
  return (
    <figure
      className="vgb-chart"
      data-slot={`vgb-${slot}`}
      data-a2ui-component={slot}
    >
      <figcaption className="vgb-chart-caption">
        <span className="vgb-chart-sr">{summary}</span>
        {title ? <span className="vgb-chart-title">{title}</span> : null}
        {unit ? <span className="vgb-chart-unit">{unit}</span> : null}
      </figcaption>
      {children}
      {footnote ? <p className="vgb-chart-note">{footnote}</p> : null}
    </figure>
  );
}

/**
 * Series key.
 *
 * Present whenever there are two or more series and absent for one, because a
 * legend with a single swatch only restates the title. Identity is the swatch
 * beside the name, never the name's own colour: a light categorical hue is
 * illegible as text.
 */
export function ChartLegend({ names }: { names: string[] }) {
  if (names.length < 2) return null;
  return (
    <ul className="vgb-chart-legend">
      {names.map((name, index) => (
        <li className="vgb-chart-legend-item" key={`${name}-${index}`}>
          <span
            aria-hidden="true"
            className="vgb-chart-swatch"
            style={{ backgroundColor: toneText(seriesTone(index)) }}
          />
          <span className="vgb-chart-legend-name">{name}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * One labelled row: name, plot track, figure.
 *
 * Long labels **wrap**; they are never truncated and never rotated. A chat
 * column routinely carries a category name longer than the bar beside it, and
 * both alternatives lose information — a clipped label needs an interaction to
 * recover, and this first version is static by design. A wrapped label costs
 * row height, which is the one budget a vertically scrolling column has.
 */
export function ChartRow({
  label,
  tone,
  children,
  figure,
  sub,
  detail,
  mismatch,
}: {
  label: string;
  tone?: Tone;
  children: ReactNode;
  figure?: ReactNode;
  sub?: ReactNode;
  /** A full-width line under the track, for values that will not fit on a mark. */
  detail?: ReactNode;
  /** The row's figure does not reconcile with what the chart computed. */
  mismatch?: boolean;
}) {
  return (
    <div
      className="vgb-chart-row"
      data-a2ui-chart-row
      data-chart-mismatch={mismatch ? "true" : undefined}
    >
      <span className="vgb-chart-label">
        {tone ? (
          <span
            aria-hidden="true"
            className="vgb-chart-swatch"
            style={{ backgroundColor: toneText(tone) }}
          />
        ) : null}
        <span className="vgb-chart-label-text">{label}</span>
      </span>
      <div className="vgb-chart-track" data-a2ui-chart-track>
        {children}
      </div>
      <span className="vgb-chart-figure">
        <span className="vgb-chart-value">{figure}</span>
        {sub ? <span className="vgb-chart-sub">{sub}</span> : null}
      </span>
      {detail ? <span className="vgb-chart-detail">{detail}</span> : null}
    </div>
  );
}
