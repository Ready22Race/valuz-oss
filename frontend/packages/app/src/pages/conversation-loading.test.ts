import { describe, expect, it } from "vitest";
import { deriveTurnActive, isTerminalSessionStatus } from "./conversation-loading";

describe("deriveTurnActive", () => {
  it("is not loading when nothing was sent", () => {
    expect(deriveTurnActive(false, "running")).toBe(false);
    expect(deriveTurnActive(false, "idle")).toBe(false);
    expect(deriveTurnActive(false, null)).toBe(false);
  });

  it("shows loading optimistically at send time before the status is known", () => {
    // Brand-new draft / pre-first-read: status unknown → the optimistic
    // ``sending`` must show through (no flicker).
    expect(deriveTurnActive(true, null)).toBe(true);
    expect(deriveTurnActive(true, undefined)).toBe(true);
    expect(deriveTurnActive(true, "")).toBe(true);
    expect(deriveTurnActive(true, "created")).toBe(true);
  });

  it("stays loading while the turn is running", () => {
    expect(deriveTurnActive(true, "running")).toBe(true);
  });

  it("un-sticks when the session reaches a terminal status, even if sending was never cleared", () => {
    // This is the bug: sending stuck true (missed terminal SSE frame), but the
    // reconciled status is terminal → the UI must show NOT loading.
    expect(deriveTurnActive(true, "idle")).toBe(false);
    expect(deriveTurnActive(true, "failed")).toBe(false);
    expect(deriveTurnActive(true, "cancelled")).toBe(false);
    expect(deriveTurnActive(true, "archived")).toBe(false);
    expect(deriveTurnActive(true, "terminated")).toBe(false);
  });
});

describe("isTerminalSessionStatus", () => {
  it("classifies running / created / unknown as non-terminal", () => {
    expect(isTerminalSessionStatus("running")).toBe(false);
    expect(isTerminalSessionStatus("created")).toBe(false);
    expect(isTerminalSessionStatus(null)).toBe(false);
    expect(isTerminalSessionStatus(undefined)).toBe(false);
    expect(isTerminalSessionStatus("")).toBe(false);
  });

  it("classifies idle / failed / cancelled / archived / terminated as terminal", () => {
    for (const s of ["idle", "failed", "cancelled", "archived", "terminated"]) {
      expect(isTerminalSessionStatus(s)).toBe(true);
    }
  });
});
