import { describe, expect, it } from "vitest";

import { slotPath } from "./slots";

describe("slotPath", () => {
  it("produces the same path for identical source+params", () => {
    const a = slotPath("test.source", { symbol: "US:NVDA", range: "1Y" });
    const b = slotPath("test.source", { symbol: "US:NVDA", range: "1Y" });
    expect(a).toBe(b);
  });

  it("is insensitive to param insertion order — same fields, different order, same path", () => {
    const a = slotPath("test.source", { symbol: "US:NVDA", range: "1Y" });
    const b = slotPath("test.source", { range: "1Y", symbol: "US:NVDA" });
    expect(a).toBe(b);
  });

  it("does not collide across different params on the same source", () => {
    const a = slotPath("test.source", { symbol: "US:NVDA" });
    const b = slotPath("test.source", { symbol: "US:AAPL" });
    expect(a).not.toBe(b);
  });

  it("does not collide across different sources with the same params", () => {
    const a = slotPath("test.source.a", { symbol: "US:NVDA" });
    const b = slotPath("test.source.b", { symbol: "US:NVDA" });
    expect(a).not.toBe(b);
  });

  it("does not collide when a param value differs only in type (string vs number)", () => {
    const a = slotPath("test.source", { limit: 8 });
    const b = slotPath("test.source", { limit: "8" });
    expect(a).not.toBe(b);
  });

  it("follows the /data/<source>/<digest> convention", () => {
    const path = slotPath("test.source", { symbol: "US:NVDA" });
    expect(path).toMatch(/^\/data\/test\.source\/[0-9a-f]{8}$/);
  });
});
