import { z } from "zod/v4";

/**
 * The outcome of something that already finished.
 *
 * `ToneSchema` is deliberately *not* reused here even though the two overlap.
 * Tone is a colour role and carries `neutral` and `brand`, neither of which is
 * an outcome — asking the model to pick a colour for a result is asking the
 * wrong question, and it answers it wrongly. This axis is the four outcomes a
 * finished operation actually has, and it spells the failure `error`, which is
 * the word a model reaches for when narrating one. `RESULT_TONE` in `index.tsx`
 * is the single mapping from this vocabulary onto the shared tone tokens.
 */
export const ResultStatusSchema = z.enum(["success", "warning", "error", "info"]);
export type ResultStatus = z.infer<typeof ResultStatusSchema>;

/** Key order is the call order: `Result("success", "Filed", "…")`. */
export const ResultSchema = z.object({
  status: ResultStatusSchema,
  title: z.string(),
  description: z.string().optional(),
});
