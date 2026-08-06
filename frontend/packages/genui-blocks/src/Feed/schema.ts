import { z } from "zod/v4";

/*
 * A stream of short entries — headlines, notices, digest items.
 *
 * `looseObject` for the item so alias keys (`headline`, `summary`, `image`)
 * survive the trip to the component instead of being stripped into a blank
 * row. Key order is the positional call signature: what it says, what it adds,
 * when, and only then the picture — a Feed entry is worth rendering without an
 * image and never worth rendering without a title.
 */
export const FeedItemSchema = z.looseObject({
  title: z.string(),
  body: z.string().optional(),
  time: z.string().optional(),
  imageUrl: z.string().optional(),
  source: z.string().optional(),
});

export const FeedSchema = z.object({
  items: z.array(FeedItemSchema),
  title: z.string().optional(),
});
