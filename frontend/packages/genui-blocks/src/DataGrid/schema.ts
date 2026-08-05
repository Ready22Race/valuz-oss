import { z } from "zod/v4";

import { AlignSchema } from "../lib/schema";

/*
 * DataGrid — a dense table for many columns.
 *
 * `sortedBy` and `filteredBy` are the load-bearing props here, and they are
 * *statements*, not settings. The block prints them as a sentence under the
 * table and does nothing else with them: it never sorts, never filters, and
 * never draws a header a reader could try to click. The rows arrive in the
 * order the answer chose and leave in it.
 *
 * Key order is the positional call signature — required first, then the
 * optionals in the order a caller reaches for them, so the shortest useful
 * call is `DataGrid(columns, rows)` and the next is `DataGrid(columns, rows,
 * title)`.
 */

/** A cell the model writes as a number as often as a string. */
const CellSchema = z.union([z.string(), z.number()]);

/**
 * One column heading. `emphasis` marks the column the answer is about — it
 * changes ink weight only, and never re-orders or re-formats anything.
 */
export const DataGridColumnSchema = z.looseObject({
  label: z.string(),
  unit: z.string().optional(),
  align: AlignSchema.optional(),
  emphasis: z.boolean().optional(),
});

export const DataGridSchema = z.object({
  columns: z.array(DataGridColumnSchema),
  rows: z.array(z.array(CellSchema)),
  title: z.string().optional(),
  sortedBy: z.string().optional(),
  filteredBy: z.string().optional(),
});
