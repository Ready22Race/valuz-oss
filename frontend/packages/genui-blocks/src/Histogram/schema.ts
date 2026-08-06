import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

/*
 * One bin. `looseObject` so `range`/`bucket`/`n` survive parsing.
 *
 * Key order is the positional call signature: the interval, then how many fell
 * in it.
 */
export const HistogramBinSchema = z.looseObject({
  label: z.string(),
  count: z.number(),
});

export const HistogramSchema = z.object({
  bins: z.array(HistogramBinSchema),
  title: z.string().optional(),
  unit: z.string().optional(),
  tone: ToneSchema.optional(),
});
