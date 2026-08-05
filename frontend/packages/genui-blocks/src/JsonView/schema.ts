import { z } from "zod/v4";

/*
 * `value` is the data itself and leads. It is `unknown` rather than a union of
 * the JSON types because that is honest: this block is handed whatever a tool
 * returned, and narrowing the schema would only move the mismatch to a place
 * where nothing checks it — nothing validates props before they reach a block,
 * so the reader in `format.ts` is the real contract.
 *
 * `collapsedDepth` is the depth at which nesting stops being expanded (3 by
 * default). It is a cap, not a target: leaving it out is the normal call.
 */
export const JsonViewSchema = z.object({
  value: z.unknown(),
  title: z.string().optional(),
  collapsedDepth: z.number().optional(),
});
