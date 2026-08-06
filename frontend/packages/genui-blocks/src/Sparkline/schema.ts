import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

/*
 * A bare trend line.
 *
 * Key order is the positional call signature: `Sparkline([1, 2, 3], "Revenue")`
 * is how the model writes it, so `values` leads and everything after it is
 * chrome. Putting `label` first would silently bind the array to it — no parse
 * error, no type error, an empty block.
 */
export const SparklineSchema = z.object({
  values: z.array(z.number()),
  label: z.string().optional(),
  tone: ToneSchema.optional(),
});
