import { z } from "zod/v4";

/**
 * Key order is the call order: `Progress(72, "Indexing", "1,204 of 1,670")`.
 * `percent` is required and therefore first — a bar with no value is an
 * indeterminate spinner, and this block deliberately has no such mode.
 */
export const ProgressSchema = z.object({
  percent: z.number(),
  label: z.string().optional(),
  detail: z.string().optional(),
});
