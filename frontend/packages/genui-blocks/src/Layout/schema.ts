import { z } from "zod/v4";

import { AlignSchema, SizeSchema } from "../lib/schema";

/**
 * Props for the layout family — the frame every other block sits inside.
 *
 * This family owns *structure* only: the document frame, the runs and grids
 * that place children, and the three pieces of furniture (rule, gap, ratio box)
 * that have no content of their own. Nothing here renders data. When a block
 * needs a title *and* a figure it belongs in the card family; when it needs to
 * place three of those side by side, it belongs here.
 *
 * Key order below is the call signature, not a style choice: OpenUI Lang binds
 * arguments positionally in zod key order, so `{ title?, children }` would make
 * `Thing([a, b])` assign the array to `title` and leave the block empty — no
 * parse error, no type error, nothing in the console. Required props come
 * first and the child slot comes before optional scalars, except where a block
 * is named by its heading (PageHeader) and a human would write that first.
 */

/** Which way a ScrollArea scrolls. */
export const ScrollAxisSchema = z.enum(["vertical", "horizontal", "both"]);
export type ScrollAxis = z.infer<typeof ScrollAxisSchema>;

export const PageSchema = z.object({
  children: z.array(z.unknown()),
  title: z.string().optional(),
  subtitle: z.string().optional(),
  meta: z.string().optional(),
});

/*
 * PageHeader is the one block here whose first argument is not the child slot.
 * It is named by its title — `PageHeader("Q3 Review", "Group revenue")` is the
 * call a human writes and therefore the call the model writes — and its child
 * slot is the rare case (a tag row beside the title).
 */
export const PageHeaderSchema = z.object({
  title: z.string().optional(),
  subtitle: z.string().optional(),
  meta: z.string().optional(),
  children: z.array(z.unknown()).optional(),
});

export const PageFooterSchema = z.object({
  children: z.array(z.unknown()).optional(),
  note: z.string().optional(),
});

export const InlineSchema = z.object({
  children: z.array(z.unknown()),
  gap: SizeSchema.optional(),
  align: AlignSchema.optional(),
});

export const ClusterSchema = z.object({
  children: z.array(z.unknown()),
  gap: SizeSchema.optional(),
});

export const DashboardGridSchema = z.object({
  children: z.array(z.unknown()),
  minColumnWidth: z.string().optional(),
});

export const DividerSchema = z.object({
  label: z.string().optional(),
});

export const SpacerSchema = z.object({
  size: SizeSchema.optional(),
});

export const AspectRatioSchema = z.object({
  children: z.array(z.unknown()),
  ratio: z.string().optional(),
});

export const ScrollAreaSchema = z.object({
  children: z.array(z.unknown()),
  maxHeight: z.string().optional(),
  axis: ScrollAxisSchema.optional(),
});

export const CollapsibleSchema = z.object({
  children: z.array(z.unknown()),
  title: z.string(),
  defaultOpen: z.boolean().optional(),
});
