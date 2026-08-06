import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

/*
 * A labelled value and the grid that holds a set of them.
 *
 * Key order is the positional call signature (OpenUI Lang binds arguments in
 * zod key order), so it reads the way the pair is spoken: what it is, what it
 * is worth, what unit that is in.
 *
 * `unit` is separate from `value` on purpose. A column of figures only lines up
 * when the digits are the same shape, and "4.2" + "M" typeset as one string
 * cannot be sized or coloured differently from the number it trails.
 */
export const KeyValueSchema = z.object({
  label: z.string(),
  value: z.string(),
  unit: z.string().optional(),
  tone: ToneSchema.optional(),
});

export const KeyValueGroupSchema = z.object({
  children: z.array(z.unknown()),
});
