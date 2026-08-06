import { z } from "zod/v4";

/** Where an item sits in the bridge. Local to this family — there is no shared enum for it. */
export const WaterfallKindSchema = z.enum(["start", "delta", "end"]);

/*
 * One bar of the bridge. `looseObject` so the alias keys the component reads
 * (`amount`, `change`, `type`) survive parsing — a strict object strips them
 * while validating the array and the bridge renders as a row of blanks.
 *
 * Key order is the positional call signature, and it is the order a bridge is
 * spoken: what moved, by how much, and only then which kind of bar it is.
 */
export const WaterfallItemSchema = z.looseObject({
  label: z.string(),
  value: z.number(),
  kind: WaterfallKindSchema.optional(),
});

/**
 * A fresh props schema, per component.
 *
 * **Two `defineComponent` calls must never share one schema object.** The
 * library keys registration off the schema, so passing the same object to
 * `Waterfall` and `BridgeChart` makes the second silently shadow the first:
 * `Waterfall(...)` then renders a BridgeChart, with no parse error, no type
 * error, and both names still present in the library. Only the `data-slot`
 * gives it away. The factory makes an identical-but-distinct object for each.
 */
function waterfallProps() {
  return z.object({
    items: z.array(
      z.looseObject({
        label: z.string(),
        value: z.number(),
        kind: WaterfallKindSchema.optional(),
      }),
    ),
    title: z.string().optional(),
    unit: z.string().optional(),
  });
}

export const WaterfallSchema = waterfallProps();
export const BridgeChartSchema = waterfallProps();
