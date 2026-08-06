import { z } from "zod/v4";

/*
 * One node. `looseObject` so `key`/`name` survive parsing.
 *
 * `id` leads because that is what the links point at; `label` is what the
 * reader sees, and defaults to the id when it is missing.
 */
export const SankeyNodeSchema = z.looseObject({
  id: z.string(),
  label: z.string().optional(),
});

/*
 * One flow. `looseObject` so `source`/`target` survive parsing.
 *
 * Key order is the positional call signature and it is how a flow is spoken:
 * from here, to there, this much.
 */
export const SankeyLinkSchema = z.looseObject({
  from: z.string(),
  to: z.string(),
  value: z.number(),
});

/**
 * Nodes, then links, then the frame's title and the unit every flow is in.
 *
 * One unit for the whole diagram is not a simplification — a Sankey's only
 * invariant is that what arrives at a node equals what leaves it, and two units
 * in one diagram makes that sum meaningless.
 */
export const SankeySchema = z.object({
  nodes: z.array(SankeyNodeSchema),
  links: z.array(SankeyLinkSchema),
  title: z.string().optional(),
  unit: z.string().optional(),
});
