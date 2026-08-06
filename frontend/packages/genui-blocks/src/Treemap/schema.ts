import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

/*
 * One slice. `looseObject` so `name`/`amount`/`share` survive parsing.
 *
 * Key order is the positional call signature: what it is, how big it is, and
 * only then any colour override.
 */
export const TreemapItemSchema = z.looseObject({
  label: z.string(),
  value: z.number(),
  tone: ToneSchema.optional(),
});

export const TreemapSchema = z.object({
  items: z.array(TreemapItemSchema),
  title: z.string().optional(),
  unit: z.string().optional(),
});
