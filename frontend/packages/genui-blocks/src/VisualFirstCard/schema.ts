import { z } from "zod/v4";

export const VisualFirstCardSchema = z.object({
  imageUrl: z.string(),
  title: z.string(),
  body: z.string().optional(),
  imageAlt: z.string().optional(),
  /** 图片比例("16/9"、"3:2"、…),默认 3:2,对齐 OpenUI Image。 */
  ratio: z.string().optional(),
  /** 图片如何铺满比例盒,对齐 OpenUI scale:fill 拉伸 / fit 完整显示。 */
  scale: z.enum(["fill", "fit"]).optional(),
});
