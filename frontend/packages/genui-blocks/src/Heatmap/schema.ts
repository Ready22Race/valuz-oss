import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

/*
 * One row of the grid. `looseObject` so `name`/`data`/`cells` survive parsing.
 *
 * Key order is the positional call signature: the row's name, then its cells in
 * column order.
 */
export const HeatmapRowSchema = z.looseObject({
  label: z.string(),
  values: z.array(z.number()),
});

/*
 * `columns` is required and sits second so `Heatmap([rows], ["Q1", "Q2"])`
 * binds the way the model writes it. Unlabelled columns would make the grid
 * unreadable, which is worse than not drawing it.
 */
export const HeatmapSchema = z.object({
  rows: z.array(HeatmapRowSchema),
  columns: z.array(z.string()),
  title: z.string().optional(),
  unit: z.string().optional(),
  tone: ToneSchema.optional(),
});
