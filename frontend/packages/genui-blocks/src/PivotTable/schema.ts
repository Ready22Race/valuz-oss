import { z } from "zod/v4";

/*
 * PivotTable — a cross-tabulation the model has already computed.
 *
 * The block cross-tabulates nothing: `rows` is the finished matrix. What it
 * does do is *check* the totals it is given against the cells, because a total
 * that does not add up is the one error a pivot makes look authoritative.
 *
 * Key order is the positional call signature and it is the order the table is
 * spoken: what the rows are, what the columns are, the column names, the
 * matrix — then the totals, and only then the presentation.
 */

/** A cell the model writes as a number as often as a string. */
const CellSchema = z.union([z.string(), z.number()]);

/** One row of the matrix. `values` is positional against `columns`. */
export const PivotRowSchema = z.looseObject({
  label: z.string(),
  values: z.array(CellSchema),
});

export const PivotTableSchema = z.object({
  rowLabel: z.string(),
  columnLabel: z.string(),
  columns: z.array(z.string()),
  rows: z.array(PivotRowSchema),
  rowTotals: z.array(CellSchema).optional(),
  columnTotals: z.array(CellSchema).optional(),
  grandTotal: CellSchema.optional(),
  unit: z.string().optional(),
  title: z.string().optional(),
});
