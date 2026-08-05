import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

/*
 * EventStrip — events placed along a stated range.
 *
 * The range is a required prop rather than something derived from the events,
 * and that is the whole point of the block: "three announcements" reads very
 * differently across one week and across five years. A strip that sized itself
 * to its own contents would always look evenly busy.
 *
 * Key order is the positional call signature and it is the order the strip is
 * spoken: from when, to when, and what happened in between.
 */

export const EventStripEventSchema = z.looseObject({
  at: z.string(),
  label: z.string(),
  tone: ToneSchema.optional(),
});

export const EventStripSchema = z.object({
  start: z.string(),
  end: z.string(),
  events: z.array(EventStripEventSchema),
  unit: z.string().optional(),
  title: z.string().optional(),
});
