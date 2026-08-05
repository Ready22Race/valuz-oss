import { z } from "zod/v4";

/*
 * StatusList / ProgressList — the two "state of the work" shapes.
 *
 * `StatusSchema` is declared here rather than in `lib/schema` because it is
 * this family's vocabulary: no other block colours by run state, and every enum
 * member is copied into the LLM prompt once per block that references it.
 * The five states are the ones a plan actually reports, and they map onto the
 * shared tones in `lib/tone` rather than to colours of their own.
 */
export const StatusSchema = z.enum([
  "pending",
  "running",
  "success",
  "error",
  "blocked",
]);
export type Status = z.infer<typeof StatusSchema>;

export const StatusItemSchema = z.looseObject({
  label: z.string(),
  status: StatusSchema,
  detail: z.string().optional(),
});

export const StatusListSchema = z.object({
  items: z.array(StatusItemSchema),
  children: z.array(z.unknown()).optional(),
  title: z.string().optional(),
});

/*
 * `percent` accepts a number as readily as a string: `62`, `"62"` and `"62%"`
 * are all routine model output, and declaring the union keeps those payloads
 * valid instead of falling through to the raw props.
 */
export const ProgressItemSchema = z.looseObject({
  label: z.string(),
  percent: z.union([z.number(), z.string()]),
  detail: z.string().optional(),
});

export const ProgressListSchema = z.object({
  items: z.array(ProgressItemSchema),
  title: z.string().optional(),
});
