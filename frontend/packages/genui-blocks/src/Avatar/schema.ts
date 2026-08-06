import { z } from "zod/v4";

import { SizeSchema } from "../lib/schema";

/*
 * `name` is required and leads because it is what the block is *for*: the image
 * is the decoration, the name is the fallback, the accessible label, and the
 * only thing that is always available. An avatar built from a URL alone has
 * nothing to draw when the URL is unusable — which, for a model-authored URL,
 * is the common case.
 */
export const AvatarSchema = z.object({
  name: z.string(),
  imageUrl: z.string().optional(),
  size: SizeSchema.optional(),
});
