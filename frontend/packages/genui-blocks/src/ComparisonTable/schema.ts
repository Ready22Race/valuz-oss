import { z } from "zod/v4";

/*
 * ComparisonTable / DiffView — the two side-by-side shapes.
 */

/** A cell the model writes as a number as often as a string. */
const CellSchema = z.union([z.string(), z.number()]);

/**
 * Which direction wins for a row. Not a tone and not a trend: `better` says
 * where the good end of *this* measure is, which is a property of the metric
 * (a lower expense ratio is better, a higher margin is), not of the value.
 */
export const BetterSchema = z.enum(["high", "low"]);
export type Better = z.infer<typeof BetterSchema>;

/*
 * `values` is positional against `columns` — index 0 is the first subject.
 * `looseObject` so alias keys survive; key order is the call signature, which
 * reads the way the row is spoken: what is measured, the readings, the unit,
 * and which way is better.
 */
export const ComparisonRowSchema = z.looseObject({
  label: z.string(),
  values: z.array(CellSchema),
  unit: z.string().optional(),
  better: BetterSchema.optional(),
});

export const ComparisonTableSchema = z.object({
  columns: z.array(z.string()),
  rows: z.array(ComparisonRowSchema),
  title: z.string().optional(),
  note: z.string().optional(),
});

export const DiffItemSchema = z.looseObject({
  label: z.string(),
  before: z.string(),
  after: z.string(),
});

export const DiffViewSchema = z.object({
  items: z.array(DiffItemSchema),
  title: z.string().optional(),
});
