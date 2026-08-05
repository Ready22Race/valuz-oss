import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

/*
 * Timeline / ActivityFeed — the two "what happened, in order" shapes.
 *
 * `looseObject`, not `object`: these are the element types of the blocks'
 * `items` arrays, and a strict object would *strip* the alias keys the
 * components read (`when`, `label`, `who`) on the way through — the block would
 * then render blank rows from a payload that carried every field.
 *
 * Key order is the positional call signature (OpenUI Lang binds arguments in
 * zod key order), so each schema reads the way its entry is spoken.
 */

export const TimelineItemSchema = z.looseObject({
  time: z.string(),
  title: z.string(),
  description: z.string().optional(),
  tone: ToneSchema.optional(),
  icon: z.string().optional(),
});

export const TimelineSchema = z.object({
  items: z.array(TimelineItemSchema),
  children: z.array(z.unknown()).optional(),
  title: z.string().optional(),
});

/*
 * `time` is optional even though an activity entry without one is barely worth
 * showing, and that is a deliberate trade. The order a human writes the call in
 * is actor, action, target, time — "张伟 审核通过 Q3 预算 09:12" — and a
 * required prop declared after an optional one cannot be reached by the
 * shortest call that supplies it: the argument silently lands on the optional
 * prop instead, with no parse error and no type error (AUTHORING.md). So the
 * natural order wins here and the description asks for the value instead.
 */
export const ActivityItemSchema = z.looseObject({
  actor: z.string(),
  action: z.string(),
  target: z.string().optional(),
  time: z.string().optional(),
  icon: z.string().optional(),
});

export const ActivityFeedSchema = z.object({
  items: z.array(ActivityItemSchema),
  children: z.array(z.unknown()).optional(),
  title: z.string().optional(),
});
