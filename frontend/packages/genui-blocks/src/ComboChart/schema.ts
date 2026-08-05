import { z } from "zod/v4";

/*
 * One series across every category. `looseObject` so `label`/`data` survive
 * parsing. `values` is positional by category: the nth number belongs to the
 * nth category, which is why the arrays have to be the same length.
 */
export const ComboSeriesSchema = z.looseObject({
  name: z.string().optional(),
  values: z.array(z.number()),
});

/**
 * Categories, then the two series, then how to scale them.
 *
 * `sameScale` is declared last and defaults to *true* by omission, which is the
 * whole safety property of this block: a second axis has to be asked for
 * explicitly and is still refused unless the two units actually differ. A dual
 * axis with no unit on it can be made to show any correlation you like, so the
 * default must never be the dangerous one.
 */
export const ComboChartSchema = z.object({
  categories: z.array(z.string()),
  bars: ComboSeriesSchema,
  line: ComboSeriesSchema,
  barUnit: z.string().optional(),
  lineUnit: z.string().optional(),
  sameScale: z.boolean().optional(),
  title: z.string().optional(),
});
