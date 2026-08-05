import { readText } from "../lib/props";
import type { Align, Size } from "../lib/schema";
import type { SplitRatio } from "./schema";

/**
 * The values this family lets model output reach CSS with.
 *
 * Three props here (`minColumnWidth`, `maxHeight`, `ratio`) are free-form
 * strings that end up in a style attribute, so every one of them is read
 * through a reader below rather than interpolated. Two reasons, in order:
 * a string that is not a length silently voids the whole declaration — the
 * grid loses its column formula and collapses to one column with no error
 * anywhere — and a string that reaches CSS unchecked is a string that can
 * close the declaration and open another.
 *
 * Every reader is total: an unusable value falls back to the block's default
 * instead of throwing or emitting nothing.
 */

/** `16rem`, `320px`, `50%` — a plain number with a unit, nothing else. */
const LENGTH = /^\d{1,5}(?:\.\d+)?(?:px|rem|em|ch|vh|vw|%)$/;
const BARE_NUMBER = /^\d{1,5}(?:\.\d+)?$/;

export function cssLength(value: unknown, fallback: string): string {
  const raw = typeof value === "string" ? value.trim() : typeof value === "number" ? String(value) : "";
  if (!raw) return fallback;
  if (LENGTH.test(raw)) return raw;
  // `"320"` is the mistake the model actually makes, and it is unambiguous.
  if (BARE_NUMBER.test(raw)) return `${raw}px`;
  return fallback;
}

/** `16/9`, `16:9`, `4 / 3`, or a bare decimal. */
const RATIO = /^(\d{1,4}(?:\.\d+)?)\s*[/:]\s*(\d{1,4}(?:\.\d+)?)$/;

export function cssRatio(value: unknown, fallback = "16 / 9"): string {
  const raw = typeof value === "string" ? value.trim() : typeof value === "number" ? String(value) : "";
  if (!raw) return fallback;
  const pair = RATIO.exec(raw);
  if (pair) {
    // A zero on either side makes `aspect-ratio` degenerate — the box loses its
    // height and the media it was reserving space for jumps the page on load,
    // which is the exact failure this block exists to prevent.
    return Number(pair[1]) > 0 && Number(pair[2]) > 0 ? `${pair[1]} / ${pair[2]}` : fallback;
  }
  if (BARE_NUMBER.test(raw) && Number(raw) > 0) return raw;
  return fallback;
}

/**
 * Gaps between children. `Size` is reused rather than given this family its own
 * enum: every enum member is copied into the prompt once per block that names
 * it, and "small | medium | large" already means the right thing here.
 */
const GAP: Record<Size, string> = {
  small: "var(--openui-space-2xs)",
  medium: "var(--openui-space-s)",
  large: "var(--openui-space-m)",
};

export function gapSpace(size: Size | undefined, fallback: Size): string {
  return GAP[size ?? fallback] ?? GAP[fallback];
}

/**
 * Standalone vertical space. A whole step larger than a gap at every size — a
 * Spacer only earns its place when one break in a sequence is meant to read as
 * bigger than the rhythm around it.
 */
const SPACER: Record<Size, string> = {
  small: "var(--openui-space-s)",
  medium: "var(--openui-space-l)",
  large: "var(--openui-space-2xl)",
};

export function spacerSpace(size: Size | undefined): string {
  return SPACER[size ?? "medium"] ?? SPACER.medium;
}

/**
 * A Split's proportion, as a `data-ratio` attribute rather than a track list.
 *
 * The column formula deliberately does *not* come through here into an inline
 * style, which is the one thing that would look tidier and be wrong: an inline
 * `grid-template-columns` beats every stylesheet rule, including the container
 * query that collapses the split to one column below 30rem. The block would
 * then hold two 8rem columns in a narrow chat column and nothing would report
 * it. The attribute lets the stylesheet own both states.
 */
const SPLIT_RATIOS = new Set<string>(["half", "wide-narrow", "narrow-wide"]);

export function splitRatio(value: unknown): SplitRatio {
  const raw = readText(value).trim().toLowerCase();
  return SPLIT_RATIOS.has(raw) ? (raw as SplitRatio) : "half";
}

/** Where a run of children sits on its line. */
const JUSTIFY: Record<Align, string> = {
  left: "flex-start",
  center: "center",
  right: "flex-end",
};

export function justifyContent(align: Align | undefined): string {
  return JUSTIFY[align ?? "left"] ?? JUSTIFY.left;
}
