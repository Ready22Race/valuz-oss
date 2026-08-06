import { z } from "zod/v4";

/*
 * One series across every category. `looseObject` so `label`/`data` survive
 * parsing.
 *
 * `values` is positional by category: the nth number belongs to the nth
 * category, which is why the two arrays have to be the same length.
 *
 * `name` is optional and still declared first. The required-props-first rule
 * exists for positional argument binding, and this is never a component's own
 * props — it is only ever written as an object literal with explicit keys, so
 * the order here is the reading order and nothing else. A series with no name
 * is numbered rather than dropped.
 */
export const ChartSeriesSchema = z.looseObject({
  name: z.string().optional(),
  values: z.array(z.number()),
});

/**
 * A fresh props schema, per component.
 *
 * **Two `defineComponent` calls must never share one schema object.** The
 * library keys registration off the schema, so passing the same object to
 * `GroupedBar` and `StackedBar` makes the second silently shadow the first:
 * `GroupedBar(...)` then renders a StackedBar, with no parse error, no type
 * error, and both names still present in the library. Only the `data-slot`
 * gives it away.
 *
 * Categories lead, then the series. That is the positional call signature —
 * `GroupedBar(["Q1", "Q2"], [{ name: "EU", values: [4, 6] }])` — and it is also
 * the reading order: what is being compared, then what is being measured.
 */
function categoryBarProps() {
  return z.object({
    categories: z.array(z.string()),
    series: z.array(
      z.looseObject({
        name: z.string().optional(),
        values: z.array(z.number()),
      }),
    ),
    title: z.string().optional(),
    unit: z.string().optional(),
  });
}

export const GroupedBarSchema = categoryBarProps();
export const StackedBarSchema = categoryBarProps();
