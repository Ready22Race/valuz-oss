import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

/*
 * One bubble. `looseObject` so the alias keys the component reads (`value`,
 * `r`, `weight`, `name`) survive parsing — a strict object strips them while
 * validating the array and the plot renders as a row of identical dots.
 *
 * Key order is the positional call signature and it is the order a point is
 * spoken: where it sits, then how big it is, then what it is called. `size` is
 * required rather than optional — a bubble chart with no third dimension is a
 * scatter, and OpenUI's own ScatterChart already draws that.
 */
export const BubblePointSchema = z.looseObject({
  x: z.number(),
  y: z.number(),
  size: z.number(),
  label: z.string().optional(),
  tone: ToneSchema.optional(),
});

/**
 * The three axes lead, because all three are the chart.
 *
 * `xLabel` / `yLabel` / `sizeLabel` are declared before `title` on purpose: an
 * unlabelled axis is the failure mode this shape has, and the positional call
 * `BubbleChart(points, "Revenue", "Margin", "Headcount")` puts them where a
 * model will actually write them. `title` is decoration and sits last.
 */
export const BubbleChartSchema = z.object({
  points: z.array(BubblePointSchema),
  xLabel: z.string().optional(),
  yLabel: z.string().optional(),
  sizeLabel: z.string().optional(),
  title: z.string().optional(),
});
