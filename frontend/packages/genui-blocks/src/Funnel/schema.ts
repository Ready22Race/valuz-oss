import { z } from "zod/v4";

/*
 * One stage. `looseObject` so `count`/`stage`/`name` survive parsing — see
 * DataListItemSchema for why a strict object empties a data block.
 *
 * Key order is the positional call signature: a stage is spoken as what it is,
 * then how many reached it.
 */
export const FunnelStageSchema = z.looseObject({
  label: z.string(),
  value: z.number(),
});

export const FunnelSchema = z.object({
  items: z.array(FunnelStageSchema),
  title: z.string().optional(),
  unit: z.string().optional(),
});
