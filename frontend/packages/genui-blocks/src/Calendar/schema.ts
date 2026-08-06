import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

/*
 * Calendar — one month, with the events that fall in it marked.
 *
 * `month` is the whole scope of the block: there is no next month to reach,
 * because there is nothing behind a control that would fetch one. `today` is a
 * prop rather than a lookup for the same reason a chart never fetches — the
 * block cannot know the date, and marking the machine's idea of "today" on a
 * calendar the answer built for some other week is a quiet lie.
 *
 * Key order is the positional call signature: the month, its events, then how
 * the week is laid out.
 */

/** Which day the week is drawn from. Local to this block; there is no shared enum. */
export const WeekStartSchema = z.enum(["mon", "sun"]);

export const CalendarEventSchema = z.looseObject({
  date: z.string(),
  label: z.string(),
  tone: ToneSchema.optional(),
});

export const CalendarSchema = z.object({
  month: z.string(),
  events: z.array(CalendarEventSchema),
  weekStart: WeekStartSchema.optional(),
  today: z.string().optional(),
  title: z.string().optional(),
});
