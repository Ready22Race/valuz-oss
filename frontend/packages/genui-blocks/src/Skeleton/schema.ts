import { z } from "zod/v4";

/** The shape a placeholder stands in for. */
export const SkeletonVariantSchema = z.enum(["text", "block", "circle"]);
export type SkeletonVariant = z.infer<typeof SkeletonVariantSchema>;

/**
 * Both props are optional, so key order only decides how the shortest call
 * reads: `Skeleton(3, "text")`.
 */
export const SkeletonSchema = z.object({
  lines: z.number().optional(),
  variant: SkeletonVariantSchema.optional(),
});
