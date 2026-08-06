import { z } from "zod/v4";

/*
 * Outline — the three shapes for structure without time: Tree (nesting),
 * Breadcrumb (a path through it), DescriptionList (term and definition).
 */

/*
 * `children` is `z.array(z.unknown())`, not a self-reference.
 *
 * A recursive zod schema would describe the nesting exactly and would also hang
 * the prompt generator: `resolveTypeAnnotation` in lang-core expands a nested
 * object shape inline, so a schema that contains itself expands forever. The
 * shape of a child is stated in `Tree`'s description instead, where the model
 * reads it anyway, and the component reads the nesting defensively.
 */
export const TreeItemSchema = z.looseObject({
  label: z.string(),
  detail: z.string().optional(),
  children: z.array(z.unknown()).optional(),
});

export const TreeSchema = z.object({
  items: z.array(TreeItemSchema),
  title: z.string().optional(),
});

/*
 * `current` marks where the reader is. It stays optional because the model
 * rarely sets it and the last entry is the current one in every breadcrumb ever
 * written — the component falls back to that rather than rendering a path that
 * ends nowhere.
 */
export const BreadcrumbItemSchema = z.looseObject({
  label: z.string(),
  current: z.boolean().optional(),
});

export const BreadcrumbSchema = z.object({
  items: z.array(BreadcrumbItemSchema),
});

export const DescriptionItemSchema = z.looseObject({
  term: z.string(),
  description: z.string(),
});

export const DescriptionListSchema = z.object({
  items: z.array(DescriptionItemSchema),
  title: z.string().optional(),
});
