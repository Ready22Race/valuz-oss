import { z } from "zod/v4";

import { AlignSchema, SizeSchema } from "../lib/schema";

/*
 * A paragraph run. `text` leads and is the only required prop; `align` and
 * `size` are typographic treatment of the whole run, which is the only kind of
 * emphasis this block has — see `index.tsx` for why there is no inline markup.
 */
export const RichTextSchema = z.object({
  text: z.string(),
  align: AlignSchema.optional(),
  size: SizeSchema.optional(),
});
