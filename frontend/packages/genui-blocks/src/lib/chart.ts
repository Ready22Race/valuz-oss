import {
  readLooseNumber,
  readRecord,
  readTextFromKeys,
  toArray,
} from "./props";
import type { Tone, Trend } from "./schema";
import { toneText } from "./tone";

/**
 * Geometry and colour helpers shared by the hand-drawn chart blocks.
 *
 * Every chart in this package is inline SVG or CSS boxes — deliberately, since
 * OpenUI already ships recharts-backed BarChart/LineChart/AreaChart/PieChart/
 * ScatterChart/RadarChart and these are the shapes it lacks. Adding a charting
 * library here would pull a second renderer into a package whose whole contract
 * is "@openuidev/* + react + zod".
 *
 * Two rules the helpers below exist to enforce:
 *
 *  1. **Colour comes from a tone, never from a literal.** `toneTint` is the only
 *     way a chart paints a fill, and it resolves through `toneText()` — so a
 *     block restyles itself with the host theme like every other block does.
 *  2. **A span is never zero.** All-equal data, a single point and all-zero
 *     values are routine in model output; dividing by the range without a floor
 *     turns them into `NaN%` inline styles, which render as a collapsed or
 *     absent bar with no error anywhere.
 */

/**
 * Series colours, in fixed order.
 *
 * Assigned by position and never cycled past the end: a 7th series would have
 * to reuse a hue, and two series wearing one colour is worse than not drawing
 * them, so the multi-series blocks cap at `MAX_SERIES` and say so rather than
 * inventing a rainbow. `neutral` sits last because it reads as "the rest".
 */
export const SERIES_TONES: Tone[] = [
  "brand",
  "info",
  "success",
  "warning",
  "danger",
  "neutral",
];

export const MAX_SERIES = SERIES_TONES.length;

export function seriesTone(index: number): Tone {
  return SERIES_TONES[Math.abs(index) % SERIES_TONES.length] ?? "brand";
}

/** Number of palette slots `seriesColor()` can address. */
export const CHART_SERIES = 8;

/**
 * A series colour, resolved to a `--vgb-chart-N` token.
 *
 * `seriesTone()` maps a position to a *semantic* tone and is right where the
 * colour must stay semantically meaningful (Sankey nodes, trend statements).
 * Multi-series charts instead need hues that stay distinct under any host
 * theme — a host maps `info` text to the same hue as `brand`, so two series
 * painted with `seriesTone(0)`/`seriesTone(1)` are indistinguishable. This
 * reads the chart-dedicated palette (`base.css`), which is decoupled from the
 * text-semantic tones, so series 1 and 2 differ even when the host's `info`
 * and `brand` agree. Cycles past `CHART_SERIES` and wraps.
 */
export function seriesColor(index: number): string {
  const n = (Math.abs(index) % CHART_SERIES) + 1;
  return `var(--vgb-chart-${n}, var(--openui-text-neutral-primary))`;
}

/**
 * A tone at partial strength, still token-derived.
 *
 * `color-mix` keeps the custom property as the input, so the result follows the
 * theme; mixing toward `transparent` rather than toward a surface colour means
 * the block never has to know what it is sitting on. Opacity on the element
 * itself would have faded its text too, and a value label is not data ink.
 */
export function toneTint(tone: Tone | undefined, percent: number): string {
  const clamped = Math.max(0, Math.min(100, Math.round(percent)));
  return `color-mix(in srgb, ${toneText(tone)} ${clamped}%, transparent)`;
}

const DECIMAL = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const SHARE = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

/** A chart figure: grouped, at most two decimals, never abbreviated. */
export function formatValue(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return DECIMAL.format(value);
}

/** A contribution, written with its sign so its direction reads without a legend. */
export function formatSigned(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${DECIMAL.format(value)}`;
}

/** A ratio as a percentage. `0.431` → `"43.1%"`. */
export function formatShare(ratio: number): string {
  if (!Number.isFinite(ratio)) return "—";
  return `${SHARE.format(ratio * 100)}%`;
}

/** The value domain a chart's marks are positioned against. */
export interface Span {
  min: number;
  max: number;
  /** `max - min`, floored at 1 so every division below is safe. */
  size: number;
}

/**
 * The domain covering `values`, always including zero.
 *
 * Including zero is what makes a bar's length mean its magnitude: a domain of
 * 98–100 draws a 99 as a half-length bar, which is a different claim than the
 * data makes. The size floor covers the all-equal and all-zero cases.
 */
export function spanOf(values: number[]): Span {
  const finite = values.filter((value) => Number.isFinite(value));
  const min = Math.min(0, ...finite);
  const max = Math.max(0, ...finite);
  const size = max - min;
  return { min, max, size: size > 0 ? size : 1 };
}

/**
 * The domain covering `values` and nothing more.
 *
 * The opposite choice from `spanOf`, and the right one wherever a mark's
 * *position* is the data rather than its length — a box plot of 98 to 100
 * anchored at zero draws five identical slivers at the right-hand edge.
 */
export function extentOf(values: number[]): Span {
  const finite = values.filter((value) => Number.isFinite(value));
  if (finite.length === 0) return { min: 0, max: 1, size: 1 };
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const size = max - min;
  return { min, max, size: size > 0 ? size : 1 };
}

/** Where `value` sits in `span`, as a percentage from the left edge. */
export function offsetPct(value: number, span: Span): number {
  if (!Number.isFinite(value)) return 0;
  return clampPct(((value - span.min) / span.size) * 100);
}

/** A length of `size` value-units, as a percentage of `span`. */
export function sizePct(size: number, span: Span): number {
  if (!Number.isFinite(size)) return 0;
  return clampPct((size / span.size) * 100);
}

function clampPct(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

/** A CSS percentage, rounded to a hundredth so inline styles stay readable. */
export function asPct(value: number): string {
  return `${Math.round(clampPct(value) * 100) / 100}%`;
}

/**
 * A numeric list from model output.
 *
 * Holes are preserved, because a heatmap row's third cell being empty is not
 * the same fact as the row having three cells. Use `readNumbers` where a hole
 * carries no meaning.
 */
export function readCells(value: unknown): (number | undefined)[] {
  return toArray(value).map((entry) => readLooseNumber(entry));
}

/** A numeric list with the unreadable entries dropped. */
export function readNumbers(value: unknown): number[] {
  return readCells(value).filter(
    (entry): entry is number => entry !== undefined,
  );
}

/** The label of a data item, under any of the keys a model reaches for. */
export function readLabel(record: Record<string, unknown>): string {
  return readTextFromKeys(record, [
    "label",
    "name",
    "title",
    "category",
    "stage",
    "text",
  ]);
}

/** A data item flattened out of whatever wrapper it arrived in. */
export function readItems(value: unknown): Record<string, unknown>[] {
  return toArray(value).map((entry) => readRecord(entry));
}

/** Direction of a series, from its ends. */
export function trendOf(first: number, last: number): Trend {
  if (last > first) return "up";
  if (last < first) return "down";
  return "flat";
}
