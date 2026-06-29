import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

const { listEvents, subscribeEvents, sendMessage } = vi.hoisted(() => ({
  listEvents: vi.fn(),
  subscribeEvents: vi.fn(),
  sendMessage: vi.fn(),
}));

vi.mock("@valuz/core", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return { ...actual, sessionsApi: { listEvents, subscribeEvents, sendMessage } };
});

import { useLeadFollowUpChat } from "./use-lead-follow-up-chat";

const evt = (seq: number, ts: number, userText: string) => ({
  seq,
  timestamp: ts,
  event: { event_type: "message.user", payload: { text: userText } },
});

// Lead assistant message (e.g. the finish-turn closing summary that lands a
// beat after task_completed). ``message_id`` keeps buildTurns from de-duping.
const asst = (seq: number, ts: number, text: string) => ({
  seq,
  timestamp: ts,
  event: {
    event_type: "message.assistant.delta",
    payload: { text, message_id: `m${seq}` },
  },
});

beforeEach(() => {
  listEvents.mockReset();
  subscribeEvents.mockReset();
  sendMessage.mockReset();
});

describe("useLeadFollowUpChat", () => {
  it("only keeps events after sinceTs", async () => {
    listEvents.mockResolvedValue({
      session_id: "s1",
      items: [evt(1, 50, "orchestration noise"), evt(2, 150, "follow-up question")],
    });
    subscribeEvents.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 100 }),
    );
    await waitFor(() => expect(result.current.turns.length).toBe(1));
    expect(result.current.turns[0].userText).toBe("follow-up question");
  });

  it("drops the lead's leaked closing summary that lands after task_completed", async () => {
    // The finish turn emits its wrap-up assistant_message a beat AFTER
    // task_completed (ts 200 > sinceTs 100), then the user opens the follow-up
    // (ts 300). A raw ``timestamp > sinceTs`` filter would surface the summary
    // at the top; anchoring on the first user message must drop it.
    listEvents.mockResolvedValue({
      session_id: "s1",
      items: [
        evt(1, 50, "original task goal"),
        asst(2, 200, "交付完成。✅ leaked closing summary"),
        evt(3, 300, "please tweak the headline"),
      ],
    });
    subscribeEvents.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 100 }),
    );
    await waitFor(() => expect(result.current.turns.length).toBe(1));
    expect(result.current.turns[0].userText).toBe("please tweak the headline");
    const allText = JSON.stringify(result.current.turns);
    expect(allText).not.toContain("leaked closing summary");
    expect(allText).not.toContain("original task goal");
  });

  it("stays empty until the user sends the first follow-up message", async () => {
    // Post-completion the lead's summary exists but the user hasn't replied
    // yet — the follow-up surface must be a clean slate, not a phantom turn.
    listEvents.mockResolvedValue({
      session_id: "s1",
      items: [
        evt(1, 50, "original task goal"),
        asst(2, 200, "交付完成。✅ closing summary"),
      ],
    });
    subscribeEvents.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 100 }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());
    expect(result.current.turns).toEqual([]);
  });

  it("keeps turns empty when sinceTs is null", async () => {
    listEvents.mockResolvedValue({
      session_id: "s1",
      items: [evt(1, 50, "noise"), evt(2, 150, "more noise")],
    });
    subscribeEvents.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: null }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());
    expect(result.current.turns).toEqual([]);
  });

  it("send() forwards to sessionsApi.sendMessage and toggles sending", async () => {
    listEvents.mockResolvedValue({ session_id: "s1", items: [] });
    subscribeEvents.mockResolvedValue(undefined);
    let resolveSend: () => void = () => {};
    sendMessage.mockImplementation(
      () => new Promise<void>((r) => { resolveSend = () => r(); }),
    );
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 0 }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());
    act(() => { void result.current.send("hello"); });
    await waitFor(() => expect(result.current.sending).toBe(true));
    expect(sendMessage).toHaveBeenCalledWith("s1", "hello");
    act(() => resolveSend());
    await waitFor(() => expect(result.current.sending).toBe(false));
  });

  it("send() ignores whitespace-only input", async () => {
    listEvents.mockResolvedValue({ session_id: "s1", items: [] });
    subscribeEvents.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 0 }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());
    await act(async () => { await result.current.send("   "); });
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("no-ops when leadSessionId is null", () => {
    renderHook(() => useLeadFollowUpChat({ leadSessionId: null, sinceTs: 0 }));
    expect(listEvents).not.toHaveBeenCalled();
  });
});
