import { z } from "zod/v4";

import { ToneSchema, TrendSchema } from "../lib/schema";

/*
 * A change with no figure attached to it.
 *
 * `value` leads and is the only required prop: a delta without its figure is
 * nothing, while a delta without a direction can still be read from the sign.
 * `tone` trails because it is the override — the colour normally comes from
 * `trend` through `trendTone`, and passing both is only right when the
 * direction and the sentiment genuinely disagree (a fall in costs, say).
 */
export const StatDeltaSchema = z.object({
  value: z.string(),
  trend: TrendSchema.optional(),
  basis: z.string().optional(),
  tone: ToneSchema.optional(),
});
