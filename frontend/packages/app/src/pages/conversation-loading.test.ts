import { describe, expect, it } from "vitest";
import { deriveTurnActive, isTerminalSessionStatus } from "./conversation-loading";

describe("deriveTurnActive", () => {
  it("is not loading on a quiet session, whatever its terminal status", () => {
    expect(deriveTurnActive(false, "idle")).toBe(false);
    expect(deriveTurnActive(false, "failed")).toBe(false);
    expect(deriveTurnActive(false, null)).toBe(false);
    expect(deriveTurnActive(false, "created")).toBe(false);
  });

  it("is loading while a turn runs, WHOEVER started it (queue drain, schedule, another client)", () => {
    // No local send pending — the reconciled running status alone carries busy.
    // This is what the old ``sending && …`` formula could not express.
    expect(deriveTurnActive(false, "running")).toBe(true);
    expect(deriveTurnActive(true, "running")).toBe(true);
  });

  it("shows loading optimistically at send time before the status is known", () => {
    expect(deriveTurnActive(true, null)).toBe(true);
    expect(deriveTurnActive(true, undefined)).toBe(true);
    expect(deriveTurnActive(true, "")).toBe(true);
    expect(deriveTurnActive(true, "created")).toBe(true);
  });

  it("keeps loading through a stale pre-turn terminal status while a send is pending", () => {
    // Slow-start hazard (attachment parse threading): the session legitimately
    // still reads the PRE-turN ``idle`` for seconds after Send — collapsing
    // here froze the elapsed timer / reverted the Stop button (image upload).
    // ``sendPending`` is released by the turn's start/terminal events or a
    // send error, never left to hang on its own.
    expect(deriveTurnActive(true, "idle")).toBe(true);
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
