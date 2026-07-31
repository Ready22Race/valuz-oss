/**
 * The turn header's elapsed counter must run ONCE, from Send to the end of
 * the turn.
 *
 * It used to run twice. The optimistic turn is anchored on the client's Send
 * time; the real turn that replaces it is anchored on the kernel's
 * ``message.user`` stamp, which is written at run() entry — after the runtime
 * is up. Locally that gap is milliseconds, but a sandboxed / cloud kernel has
 * to boot first, so the counter visibly fell back to "已处理 0 秒" after
 * already showing twenty-odd seconds. ``clientSentAtMs`` carries the Send time
 * across that handover, and ``startingRuntime`` renames the header for the
 * part of the turn where nothing is being processed yet.
 */
import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConversationTurn } from "@valuz/shared";
import { ConversationTurnList } from "./ConversationTurnList";

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getVirtualItems: () =>
      Array.from({ length: count }).map((_, index) => ({
        index,
        start: index * 220,
      })),
    getTotalSize: () => count * 220,
    measureElement: () => {},
    scrollToIndex: () => {},
  }),
}));

const T0 = 1_700_000_000_000; // arbitrary fixed epoch — the test owns the clock
const BOOT_MS = 20_000; // Send → runtime up
const NOW_MS = 30_000; // Send → "now"

function renderTurn(
  turn: ConversationTurn,
  opts: { sending: boolean; startingRuntime?: "local" | "cloud" | null },
) {
  const scrollContainerRef = createRef<HTMLDivElement>();
  return render(
    <div ref={scrollContainerRef}>
      <ConversationTurnList
        turns={[turn]}
        scrollContainerRef={scrollContainerRef}
        sending={opts.sending}
        loading={false}
        error={null}
        startingRuntime={opts.startingRuntime}
      />
    </div>,
  );
}

afterEach(() => {
  vi.useRealTimers();
});

describe("turn header elapsed", () => {
  it("counts from the Send stamp, not the runtime's, once the turn is live", () => {
    vi.useFakeTimers();
    vi.setSystemTime(T0 + NOW_MS);

    // The kernel echo has landed (``startingRuntime`` cleared) and stamped the
    // turn 20s after Send. Anchoring on that stamp would show "已处理 10 秒".
    renderTurn(
      {
        id: "turn-1",
        userMessageSeq: 1,
        userText: "hi",
        blocks: [],
        failedMessage: null,
        userTimestamp: T0 + BOOT_MS,
        clientSentAtMs: T0,
      },
      { sending: true, startingRuntime: null },
    );

    expect(screen.getByText("已处理 30 秒")).toBeTruthy();
    expect(screen.queryByText("已处理 10 秒")).toBeNull();
  });

  it("names the runtime startup instead of claiming to process, counter still running", () => {
    vi.useFakeTimers();
    vi.setSystemTime(T0 + NOW_MS);

    // Pre-echo: the optimistic turn has no kernel stamp at all.
    const { rerender, unmount } = renderTurn(
      {
        id: "pending-turn",
        userMessageSeq: 0,
        userText: "hi",
        blocks: [],
        failedMessage: null,
        userTimestamp: T0,
        clientSentAtMs: T0,
      },
      { sending: true, startingRuntime: "cloud" },
    );
    expect(screen.getByText("正在启动云端运行环境 · 30 秒")).toBeTruthy();

    // Local execution gets its own wording off the same counter.
    const scrollContainerRef = createRef<HTMLDivElement>();
    rerender(
      <div ref={scrollContainerRef}>
        <ConversationTurnList
          turns={[
            {
              id: "pending-turn",
              userMessageSeq: 0,
              userText: "hi",
              blocks: [],
              failedMessage: null,
              userTimestamp: T0,
              clientSentAtMs: T0,
            },
          ]}
          scrollContainerRef={scrollContainerRef}
          sending
          loading={false}
          error={null}
          startingRuntime="local"
        />
      </div>,
    );
    expect(screen.getByText("正在启动本地运行环境 · 30 秒")).toBeTruthy();
    unmount();
  });

  it("keeps the boot window in the frozen total when the turn settles", () => {
    // Block elapsedMs are measured from the KERNEL stamp, so a settled turn
    // would drop by exactly the boot time without folding the offset back in.
    renderTurn(
      {
        id: "turn-1",
        userMessageSeq: 1,
        userText: "hi",
        blocks: [
          {
            kind: "tool",
            tool: {
              id: "t1",
              kind: "bash",
              title: "Bash",
              status: "success",
            },
            elapsedMs: 5_000,
          },
        ],
        failedMessage: null,
        userTimestamp: T0 + BOOT_MS,
        clientSentAtMs: T0,
      },
      { sending: false },
    );

    expect(screen.getByText("已处理 25 秒")).toBeTruthy();
  });

  it("falls back to the kernel stamp for history-loaded turns", () => {
    // No ``clientSentAtMs``: nobody was watching this turn go out, so there is
    // no boot window to account for and the old behaviour stands.
    renderTurn(
      {
        id: "turn-1",
        userMessageSeq: 1,
        userText: "hi",
        blocks: [
          {
            kind: "tool",
            tool: {
              id: "t1",
              kind: "bash",
              title: "Bash",
              status: "success",
            },
            elapsedMs: 5_000,
          },
        ],
        failedMessage: null,
        userTimestamp: T0,
      },
      { sending: false },
    );

    expect(screen.getByText("已处理 5 秒")).toBeTruthy();
  });
});
