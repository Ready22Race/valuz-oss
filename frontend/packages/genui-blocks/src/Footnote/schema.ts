import { z } from "zod/v4";

/*
 * The author's own aside, numbered.
 *
 * `index` is a number, matching `Citation`/`SourceItem`: the marker in the
 * prose and the note at the foot are the same reference, and one type for both
 * is what lets the model line them up without a rule telling it to.
 *
 * Both props are required — a footnote with no number cannot be resolved from
 * the text, and one with no text is not a footnote.
 */
export const FootnoteSchema = z.object({
  index: z.number(),
  text: z.string(),
});

export const FootnoteListSchema = z.object({
  children: z.array(z.unknown()),
});
