import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

/*
 * One five-number summary. `looseObject` so `p25`/`p50`/`p75` and friends
 * survive parsing.
 *
 * Key order is the positional call signature and it is the order the five
 * numbers are always quoted in — low to high — so
 * `BoxPlotItem("EU", 1, 4, 6, 9, 12)` binds the way it reads.
 */
export const BoxPlotItemSchema = z.looseObject({
  label: z.string(),
  min: z.number(),
  q1: z.number(),
  median: z.number(),
  q3: z.number(),
  max: z.number(),
  outliers: z.array(z.number()).optional(),
});

export const BoxPlotSchema = z.object({
  items: z.array(BoxPlotItemSchema),
  title: z.string().optional(),
  unit: z.string().optional(),
  tone: ToneSchema.optional(),
});
