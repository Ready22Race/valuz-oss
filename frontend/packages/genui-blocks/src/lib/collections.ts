import { readRecord, readText, toArray } from "./props";
import type { Tone } from "./schema";
import { ToneSchema } from "./schema";

/**
 * Readers shared by the data-collection families (Timeline, ActivityFeed,
 * StatusList, ProgressList, ComparisonTable, DiffView, Tree, Breadcrumb,
 * DescriptionList, Feed).
 *
 * Same rationale as `lib/props`: these blocks are handed model output, not
 * code. Every reader is total — a malformed field degrades to a missing line
 * rather than an unmounted component — and none of them ever rewrites a value
 * it renders. Parsing happens so the block can *decide* something (which cell
 * wins, how wide a bar is); the text on screen is always the text that arrived.
 */

/**
 * Items as flat records.
 *
 * Two shapes have to survive. The model routinely writes a bare string where
 * the schema asks for an object (`Breadcrumb(["研究", "半导体"])`), so a string
 * folds onto `textKey` instead of vanishing. And A2UI nests a component's own
 * fields under `props`, which `readRecord` flattens.
 */
export function readItems(
  value: unknown,
  textKey: string,
): Record<string, unknown>[] {
  return toArray(value).map((entry) => {
    if (typeof entry === "string" || typeof entry === "number") {
      return { [textKey]: readText(entry) };
    }
    return readRecord(entry);
  });
}

const TONES = new Set<string>(ToneSchema.options);

/** A tone, if the value names one. Anything else is undefined, not neutral. */
export function readTone(value: unknown): Tone | undefined {
  const text = readText(value).trim().toLowerCase();
  return TONES.has(text) ? (text as Tone) : undefined;
}

/**
 * A percentage on a 0–100 scale.
 *
 * `62`, `"62"`, `"62%"` and `"1,062"` all arrive in practice. Out-of-range
 * values are clamped because a bar cannot render 140% of its track — but note
 * that the *label* keeps the number that arrived, so a clamp never quietly
 * restates the data. `0.62` is read as 0.62%, not 62%: guessing the scale would
 * be a silent rewrite of the value, and the description asks for 0–100.
 */
export function readPercent(value: unknown): number | undefined {
  const raw =
    typeof value === "number"
      ? value
      : Number(readText(value).replace(/[,%\s]/g, ""));
  return Number.isFinite(raw) ? raw : undefined;
}

/** The share of a track a percentage fills, always inside 0–100. */
export function clampPercent(value: number): number {
  return Math.min(100, Math.max(0, value));
}

/** `62` → "62", `61.5` → "61.5", `61.53` → "61.5". Trailing zeros are noise. */
export function formatPercent(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}
