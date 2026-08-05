import { z } from "zod/v4";

/**
 * Key order is the call order: `EmptyState("No filings yet", "…", "inbox")`.
 * OpenUI Lang binds positionally in zod key order, so `title` must stay first.
 */
export const EmptyStateSchema = z.object({
  title: z.string(),
  description: z.string().optional(),
  icon: z.string().optional(),
});
