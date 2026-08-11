import { describe, expect, it } from "vitest";

import { CHART_SERIES, seriesColor } from "./chart";

describe("seriesColor", () => {
  it("should resolve distinct slots to distinct --vgb-chart-N tokens", () => {
    expect(seriesColor(0)).toBe(
      "var(--vgb-chart-1, var(--openui-text-neutral-primary))",
    );
    expect(seriesColor(1)).toBe(
      "var(--vgb-chart-2, var(--openui-text-neutral-primary))",
    );
    expect(seriesColor(0)).not.toBe(seriesColor(1));
  });

  it("should cycle past the palette size and wrap", () => {
    // 8 slots, so index 8 maps back to slot 1.
    expect(seriesColor(CHART_SERIES)).toBe(seriesColor(0));
    expect(seriesColor(CHART_SERIES + 1)).toBe(seriesColor(1));
  });

  it("should treat negative indexes as their absolute value", () => {
    expect(seriesColor(-1)).toBe(seriesColor(1));
    expect(seriesColor(-CHART_SERIES)).toBe(seriesColor(0));
  });
});
