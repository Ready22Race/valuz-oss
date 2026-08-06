import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

/*
 * Kanban — a board as a *view*, not a workspace.
 *
 * `limit` is the one prop that has to be read carefully: it is a work-in-
 * progress limit the board is being measured against, and the block reports
 * whether the column is over it. It does not enforce anything, hide anything,
 * or stop anything, because there is nothing here to stop.
 *
 * Key order is the positional call signature; a board is written as its
 * columns, and everything else is decoration.
 */

export const KanbanItemSchema = z.looseObject({
  title: z.string(),
  meta: z.string().optional(),
  tone: ToneSchema.optional(),
});

export const KanbanColumnSchema = z.looseObject({
  label: z.string(),
  items: z.array(KanbanItemSchema),
  limit: z.number().optional(),
});

export const KanbanSchema = z.object({
  columns: z.array(KanbanColumnSchema),
  title: z.string().optional(),
});
