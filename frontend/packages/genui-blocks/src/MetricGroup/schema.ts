import { z } from "zod/v4";

import { TrendSchema } from "../lib/schema";

/*
 * Row shape. `looseObject`, not `object`: nothing validates props before they
 * reach the block, and a strict object would still leave the component reading
 * keys the model spelled differently (`title` for `label`, `change` for
 * `delta`). Declaring it loose says out loud that the extra keys are expected.
 */
export const MetricGroupItemSchema = z.looseObject({
  label: z.string(),
  value: z.string(),
  delta: z.string().optional(),
  trend: TrendSchema.optional(),
});

/*
 * `items` leads even though a reader meets the title first. OpenUI Lang binds
 * arguments in zod key order, so an optional `title` declared ahead of the
 * required data would make `MetricGroup([…])` assign the array to the title and
 * leave the group empty — no parse error, no type error, just a blank block.
 * `basis` trails because it is the footnote, and reads that way in the call.
 */
export const MetricGroupSchema = z.object({
  items: z.array(MetricGroupItemSchema),
  title: z.string().optional(),
  basis: z.string().optional(),
});
