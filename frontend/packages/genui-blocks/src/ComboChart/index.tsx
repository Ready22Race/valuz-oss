"use client";

import { defineComponent } from "@openuidev/react-lang";

import {
  asPct,
  formatValue,
  offsetPct,
  readCells,
  seriesTone,
  sizePct,
  spanOf,
} from "../lib/chart";
import { ChartFrame, ChartLegend } from "../lib/chart-parts";
import { readRecord, readText, readTextFromKeys, toArray } from "../lib/props";
import type { Span } from "../lib/chart";
import { toneText } from "../lib/tone";
import { ComboChartSchema } from "./schema";

export { ComboChartSchema, ComboSeriesSchema } from "./schema";

/**
 * Category cap.
 *
 * Deliberately a cap rather than a sideways scroll, which is what the wide
 * charts in this family do. A combo chart's claim is about how the line moves
 * *against* the bars, and half of that comparison scrolled out of view is the
 * comparison not being made. Sixteen columns still fit a chat column; past that
 * the answer wants two charts.
 */
const MAX_CATEGORIES = 16;

/** Bars take the first palette slot, the line the second — same as the legend. */
const BAR_TONE = seriesTone(0);
const LINE_TONE = seriesTone(1);

interface Series {
  name: string;
  values: (number | undefined)[];
}

function readSeries(value: unknown, fallback: string): Series {
  // A model that has just written GroupedBar reaches for an array here out of
  // habit. One series wrapped in a list is still one series; anything past the
  // first is not drawable by this shape and is left behind.
  const record = readRecord(Array.isArray(value) ? value[0] : value);
  return {
    name: readTextFromKeys(record, ["name", "label", "title", "series"]) || fallback,
    values: readCells(record.values ?? record.data ?? record.points),
  };
}

/** The line, as runs of consecutive readings — a hole breaks it rather than bridging it. */
function segmentsOf(values: (number | undefined)[], count: number, span: Span): string[] {
  const runs: string[][] = [];
  let run: string[] = [];
  for (let index = 0; index < count; index += 1) {
    const value = values[index];
    if (value === undefined) {
      if (run.length > 0) runs.push(run);
      run = [];
      continue;
    }
    const x = Math.round((((index + 0.5) / count) * 100 + Number.EPSILON) * 100) / 100;
    const y = Math.round((100 - offsetPct(value, span)) * 100) / 100;
    run.push(`${x},${y}`);
  }
  if (run.length > 0) runs.push(run);
  // A single reading has no line to draw, so it becomes a short flat dash at its
  // own height rather than disappearing.
  return runs.map((points) =>
    points.length === 1 ? `${points[0]} ${points[0]}` : points.join(" "),
  );
}

export const ComboChart = defineComponent({
  name: "ComboChart",
  props: ComboChartSchema,
  description:
    "Bars and a line over the same categories — a level and the rate, a volume and its price, an amount and its share. " +
    "categories names the columns; bars and line are each {name, values} where the nth value belongs to the nth category. " +
    "Both series are drawn against ONE shared scale by default, which is almost always what you want. A second axis is only used when sameScale is explicitly false AND barUnit and lineUnit name genuinely different units — an unlabelled second axis can be positioned to show any correlation you like, so it is refused rather than guessed at. " +
    "barUnit and lineUnit name what each series measures (\"USD m\", \"%\"). At most 16 categories are drawn. Use GroupedBar when both series are the same measure, and two separate charts when they are unrelated.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const bars = readSeries(raw.bars ?? raw.bar ?? raw.columns, "Bars");
    const line = readSeries(raw.line ?? raw.trend ?? raw.overlay, "Line");
    const labels = toArray(raw.categories ?? raw.labels).map((value) => readText(value));

    const count = Math.min(
      MAX_CATEGORIES,
      Math.max(labels.length, bars.values.length, line.values.length),
    );
    const truncated =
      Math.max(labels.length, bars.values.length, line.values.length) - count;

    const barNumbers = bars.values
      .slice(0, count)
      .filter((value): value is number => value !== undefined);
    const lineNumbers = line.values
      .slice(0, count)
      .filter((value): value is number => value !== undefined);
    // Neither series carries a number: there is nothing to draw, so nothing is
    // drawn. An empty plot holding its height is the defect.
    if (count === 0 || (barNumbers.length === 0 && lineNumbers.length === 0)) return null;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const barUnit = readTextFromKeys(raw, ["barUnit", "bar_unit"]);
    const lineUnit = readTextFromKeys(raw, ["lineUnit", "line_unit"]);
    const sameScale = raw.sameScale ?? raw.same_scale;

    /*
     * **One scale unless a second is both asked for and justified.**
     *
     * Two axes is the way this chart lies: slide one scale against the other and
     * any two series can be made to look correlated, or made to look unrelated.
     * So a split needs the author to say `sameScale: false` *and* two different
     * units to say it about. `sameScale: false` with one unit — or with none
     * named — keeps the shared scale and says why in the note, because the same
     * measure on two different scales is never right.
     */
    const asked = sameScale === false;
    const differ = Boolean(barUnit) && Boolean(lineUnit) && barUnit !== lineUnit;
    const split = asked && differ;
    const refused = asked && !differ;

    const barSpan = spanOf(split ? barNumbers : [...barNumbers, ...lineNumbers]);
    const lineSpan = split ? spanOf(lineNumbers) : barSpan;
    const barZero = offsetPct(0, barSpan);
    const sharedUnit = barUnit || lineUnit;

    const range = (numbers: number[], span: Span) =>
      numbers.length > 0
        ? `${formatValue(Math.min(...numbers))} to ${formatValue(Math.max(...numbers))}`
        : `${formatValue(span.min)} to ${formatValue(span.max)}`;

    const summary =
      `Combination chart${title ? ` of ${title}` : ""}: ${count} categories. ` +
      `Bars, ${bars.name}, ${range(barNumbers, barSpan)}${barUnit ? ` ${barUnit}` : ""}; ` +
      `line, ${line.name}, ${range(lineNumbers, lineSpan)}${lineUnit ? ` ${lineUnit}` : ""}. ` +
      (split
        ? "The two series use different scales and are not comparable against each other."
        : "Both series are drawn against one shared scale.");

    const notes = [
      split
        ? `Two scales: the bars are read against the left axis (${barUnit}, ` +
          `${formatValue(barSpan.min)} to ${formatValue(barSpan.max)}) and the line ` +
          `against the right axis (${lineUnit}, ${formatValue(lineSpan.min)} to ` +
          `${formatValue(lineSpan.max)}). The two are not comparable — where the line ` +
          "crosses a bar means nothing, since either scale can be shifted to put it " +
          "anywhere."
        : "",
      refused
        ? "A separate scale was requested but both series carry the same unit, or none " +
          "was named, so one shared scale is used: the same measure drawn against two " +
          "different scales is never right."
        : "",
      truncated > 0
        ? `${truncated} further categories were not drawn — a combination chart only ` +
          "makes its point while the whole of both series is on screen at once."
        : "",
    ].filter(Boolean);

    const axis = (side: "left" | "right", span: Span, unit: string, tone: string) => (
      <span className={`vgb-combo-axis vgb-combo-axis-${side}`}>
        <span className="vgb-chart-sub">{formatValue(span.max)}</span>
        {unit ? (
          <span className="vgb-combo-axis-name" style={{ color: tone }}>
            {unit}
          </span>
        ) : null}
        <span className="vgb-chart-sub">{formatValue(span.min)}</span>
      </span>
    );

    return (
      <ChartFrame
        footnote={
          notes.length > 0 ? (
            <span data-combo-split={split ? "true" : undefined}>{notes.join(" ")}</span>
          ) : null
        }
        slot="combo-chart"
        summary={summary}
        title={title}
        unit={split ? undefined : sharedUnit}
      >
        <ChartLegend names={[bars.name, line.name]} />
        <div className="vgb-combo" data-a2ui-combo data-combo-scales={split ? "split" : "shared"}>
          {axis("left", barSpan, split ? barUnit : sharedUnit, toneText(BAR_TONE))}
          <div className="vgb-combo-plot">
            <div className="vgb-combo-cells">
              {Array.from({ length: count }, (_, index) => {
                const value = bars.values[index];
                const lo = value === undefined ? 0 : Math.min(0, value);
                const hi = value === undefined ? 0 : Math.max(0, value);
                const height = sizePct(hi - lo, barSpan);
                return (
                  <div className="vgb-combo-cell" key={index}>
                    {/* A zero value draws no bar at all: a 2px stub reads as a
                        small value rather than as nothing. */}
                    {value !== undefined && height > 0 ? (
                      <span
                        aria-hidden="true"
                        className="vgb-combo-bar"
                        style={{
                          backgroundColor: toneText(BAR_TONE),
                          bottom: asPct(offsetPct(lo, barSpan)),
                          height: asPct(height),
                        }}
                      />
                    ) : null}
                  </div>
                );
              })}
            </div>
            {barSpan.min < 0 ? (
              <span
                aria-hidden="true"
                className="vgb-combo-zero"
                style={{ bottom: asPct(barZero) }}
              />
            ) : null}
            <svg
              aria-hidden="true"
              className="vgb-combo-line"
              preserveAspectRatio="none"
              viewBox="0 0 100 100"
            >
              {segmentsOf(line.values, count, lineSpan).map((points, index) => (
                <polyline
                  fill="none"
                  key={index}
                  points={points}
                  stroke={toneText(LINE_TONE)}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
            </svg>
          </div>
          {split ? axis("right", lineSpan, lineUnit, toneText(LINE_TONE)) : null}
          {/* Every figure is printed. At a hundredfold ratio the small bar is a
              sliver and the line is flat against an edge, so the text under the
              column is the only thing still carrying the value. */}
          <div className="vgb-combo-foot">
            {Array.from({ length: count }, (_, index) => {
              const barValue = bars.values[index];
              const lineValue = line.values[index];
              return (
                <div className="vgb-combo-foot-cell" key={index}>
                  <span className="vgb-combo-label">{labels[index] ?? ""}</span>
                  <span className="vgb-chart-value" style={{ color: toneText(BAR_TONE) }}>
                    {barValue === undefined ? "—" : formatValue(barValue)}
                  </span>
                  <span className="vgb-chart-value" style={{ color: toneText(LINE_TONE) }}>
                    {lineValue === undefined ? "—" : formatValue(lineValue)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </ChartFrame>
    );
  },
});
