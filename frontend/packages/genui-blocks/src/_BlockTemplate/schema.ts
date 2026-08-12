/**
 * Template schema — copy this directory to src/<YourBlock>/ and fill in.
 * The leading underscore keeps it out of the block registry.
 *
 * Rules (see AUTHORING.md for the full list):
 *  - import z from "zod/v4", never "zod"
 *  - reuse ToneSchema/TrendSchema/AlignSchema/SizeSchema from ../lib/schema
 *  - required props first, then `children`, then optional scalars — key order
 *    is load-bearing for the OpenUI Lang render harness
 *  - keep props flat and few; every prop is prompt surface
 */
import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

export const BlockTemplateSchema = z.object({
  /** Primary content/headline. Required props come first. */
  title: z.string(),
  /** Supporting text. */
  description: z.string().optional(),
  /** Semantic tone; drives colour through lib/tone, never a literal colour. */
  tone: ToneSchema.optional(),
  /**
   * Child slot — z.array(z.unknown()) accepts both OpenUI refs and other blocks.
   * Put children BEFORE optional scalars for positional binding.
   */
  children: z.array(z.unknown()).optional(),
});
