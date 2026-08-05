import { z } from "zod/v4";

/**
 * Key order is the call order:
 * `ErrorState("Fetch failed", "The provider timed out.", "TimeoutError: …")`.
 * OpenUI Lang binds positionally in zod key order, so `title` must stay first
 * and `detail` must stay ahead of `icon` — the model reaches for the technical
 * line far more often than it names an icon.
 */
export const ErrorStateSchema = z.object({
  title: z.string(),
  description: z.string().optional(),
  detail: z.string().optional(),
  icon: z.string().optional(),
});
