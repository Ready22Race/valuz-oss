import { describe, expect, it } from "vitest";
import {
  deriveBackgroundActive,
  deriveTurnActive,
  isTerminalSessionStatus,
  shouldApplySessionStatus,
  shouldRefreshConversationHistory,
  shouldShowNoModelEmptyState,
} from "./conversation-loading";

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

describe("shouldShowNoModelEmptyState", () => {
  it("only shows after a new conversation receives a successful empty catalog", () => {
    expect(
      shouldShowNoModelEmptyState({
        isNewConversation: true,
        pageLoading: false,
        providerCount: 0,
        providerStatus: "ready",
      }),
    ).toBe(true);
  });

  it.each(["loading", "error"] as const)(
    "does not mistake provider status %s for an empty configuration",
    (providerStatus) => {
      expect(
        shouldShowNoModelEmptyState({
          isNewConversation: true,
          pageLoading: false,
          providerCount: 0,
          providerStatus,
        }),
      ).toBe(false);
    },
  );

  it("never replaces an existing conversation transcript", () => {
    expect(
      shouldShowNoModelEmptyState({
        isNewConversation: false,
        pageLoading: false,
        providerCount: 0,
        providerStatus: "ready",
      }),
    ).toBe(false);
  });
});

describe("shouldRefreshConversationHistory", () => {
  it("retries the same session when its previous hydration did not succeed", () => {
    expect(
      shouldRefreshConversationHistory({
        hydratedSessionId: null,
        sessionId: "session-1",
        promotedWithLiveStream: false,
      }),
    ).toBe(true);
  });

  it("skips an already hydrated session and a live promotion", () => {
    expect(
      shouldRefreshConversationHistory({
        hydratedSessionId: "session-1",
        sessionId: "session-1",
        promotedWithLiveStream: false,
      }),
    ).toBe(false);
    expect(
      shouldRefreshConversationHistory({
        hydratedSessionId: null,
        sessionId: "session-1",
        promotedWithLiveStream: true,
      }),
    ).toBe(false);
  });
});

describe("shouldApplySessionStatus", () => {
  it("applies live frames, non-terminal and terminal alike", () => {
    expect(shouldApplySessionStatus("running", false)).toBe(true);
    expect(shouldApplySessionStatus("idle", false)).toBe(true);
    expect(shouldApplySessionStatus("failed", false)).toBe(true);
  });

  it("keeps replays FULLY inert — a replayed running must not revive a finished turn", () => {
    // Regression: the old gate applied replayed ``running`` while suppressing
    // the replayed terminal that follows it. On cloud sessions (live kernel
    // frames carry no event_uid, so the first reconnect backfill seeds the
    // seen-set and every later one is a replay) that re-flipped a finished
    // conversation to "running" until a manual refresh.
    expect(shouldApplySessionStatus("running", true)).toBe(false);
    expect(shouldApplySessionStatus("idle", true)).toBe(false);
    expect(shouldApplySessionStatus("failed", true)).toBe(false);
  });

  it("drops frames with no status at all", () => {
    expect(shouldApplySessionStatus(undefined, false)).toBe(false);
    expect(shouldApplySessionStatus(null, false)).toBe(false);
    expect(shouldApplySessionStatus("", false)).toBe(false);
  });
});

describe("deriveBackgroundActive", () => {
  it("is true when a background task outlives its launching turn", () => {
    // The exact live case: the turn ended (idle) but the server still reports
    // background work in flight, so the session is not done.
    expect(deriveBackgroundActive("idle", true)).toBe(true);
  });

  it("is false while a turn is running", () => {
    // The turn's own affordances already cover this; callers OR the two, so
    // returning true here would just double-count.
    expect(deriveBackgroundActive("running", true)).toBe(false);
  });

  it("is false without background work", () => {
    expect(deriveBackgroundActive("idle", false)).toBe(false);
    expect(deriveBackgroundActive("idle", undefined)).toBe(false);
    expect(deriveBackgroundActive(undefined, undefined)).toBe(false);
  });

  it("stays independent of deriveTurnActive", () => {
    // Load-bearing: deriveTurnActive drives the Stop button and send routing.
    // Background work must not make it true — a Stop would stop nothing and
    // the next message would be routed into the queue (host 409).
    expect(deriveTurnActive(false, "idle")).toBe(false);
    expect(deriveBackgroundActive("idle", true)).toBe(true);
  });
});
