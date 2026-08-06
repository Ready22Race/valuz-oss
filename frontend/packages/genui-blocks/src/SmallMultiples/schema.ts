import { z } from "zod/v4";

/*
 * One panel. `looseObject` so `name`/`data`/`points` survive parsing.
 *
 * Key order is the positional call signature and it is how a panel is spoken:
 * whose series this is, then the series.
 */
export const SmallMultipleSchema = z.looseObject({
  label: z.string(),
  values: z.array(z.number()),
});

/**
 * `items` leads, then the frame's title and the unit every panel is measured in.
 *
 * There is deliberately no per-panel unit. The block's contract is that all
 * panels share one scale, and a panel measured in something else cannot share
 * it — that is two charts, not one grid.
 */
export const SmallMultiplesSchema = z.object({
  items: z.array(SmallMultipleSchema),
  title: z.string().optional(),
  unit: z.string().optional(),
});
