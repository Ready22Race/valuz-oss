"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readLooseNumber, readText } from "../lib/props";
import type { SkeletonVariant } from "./schema";
import { SkeletonSchema } from "./schema";

export { SkeletonSchema, SkeletonVariantSchema } from "./schema";
export type { SkeletonVariant } from "./schema";

const VARIANTS: readonly SkeletonVariant[] = ["text", "block", "circle"];

/** A text placeholder without several lines is not a paragraph; a box is one box. */
const DEFAULT_SHAPES: Record<SkeletonVariant, number> = { text: 3, block: 1, circle: 1 };

/**
 * The cap is the point.
 *
 * `lines` comes from a model, and `lines: 400` is a page of grey bars that
 * pushes every real answer below the fold. Twelve is past any honest use and
 * still bounded.
 */
const MAX_SHAPES = 12;

function readVariant(value: unknown): SkeletonVariant {
  const key = readText(value).trim().toLowerCase();
  return (VARIANTS as readonly string[]).includes(key) ? (key as SkeletonVariant) : "text";
}

function readCount(value: unknown, fallback: number): number {
  const parsed = readLooseNumber(value);
  if (parsed === undefined || !Number.isFinite(parsed)) return fallback;
  return Math.min(MAX_SHAPES, Math.max(1, Math.round(parsed)));
}

export const Skeleton = defineComponent({
  name: "Skeleton",
  props: SkeletonSchema,
  description:
    "Grey placeholder shapes standing in for content that is not here — use it to show the shape of an answer you are about to give, or a section deliberately left blank. " +
    "variant picks the shape: text (default) draws lines of a paragraph, block draws one filled rectangle for a chart or an image, circle draws avatar dots. lines is how many shapes to draw, 1–12, defaulting to 3 for text and 1 for the others. " +
    "It holds no content and nothing is loading behind it, so it is hidden from screen readers rather than announced as busy; if the content exists, write the content instead.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const variant = readVariant(raw.variant);
    const count = readCount(raw.lines, DEFAULT_SHAPES[variant]);

    return (
      <div
        className="vgb-skeleton"
        data-slot="vgb-skeleton"
        data-variant={variant}
        // Hidden, not announced. `role="status"`/`aria-busy` would claim
        // something is loading — nothing is: these blocks render a finished
        // model response, so the placeholder never resolves into content.
        aria-hidden="true"
      >
        {Array.from({ length: count }, (_, index) => (
          <span key={index} className="vgb-skeleton-shape" />
        ))}
      </div>
    );
  },
});
