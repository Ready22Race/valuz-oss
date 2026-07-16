import { describe, expect, it } from "vitest";
import type { SessionEventDTO } from "../api/sessions-api";
import { buildTurns, mergeEventWindow } from "./conversation-utils";

const evt = (
  seq: number,
  eventType: string,
  payload: Record<string, string>,
  timestamp?: number,
): SessionEventDTO => ({
  seq,
  event: { event_type: eventType, payload },
  timestamp,
});

describe("buildTurns — streaming deltas", () => {
  it("should accumulate text_delta into a live assistant block during streaming", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.text_delta", {
        text: "Hel",
        message_id: "a1",
      }),
      evt(3, "message.assistant.text_delta", {
        text: "lo",
        message_id: "a1",
      }),
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "Hello", messageId: "a1", sealed: false },
    ]);
  });

  it("should replace the live block's text on canonical message.assistant.delta and seal it", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.text_delta", {
        text: "Hel",
        message_id: "a1",
      }),
      evt(3, "message.assistant.text_delta", {
        text: "lo",
        message_id: "a1",
      }),
      evt(4, "message.assistant.delta", {
        text: "Hello world",
        message_id: "a1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "Hello world", messageId: "a1", sealed: true },
    ]);
  });

  it("should accumulate thinking_delta separately from text_delta", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.thinking_delta", {
        text: "Let me",
        message_id: "a1",
      }),
      evt(3, "message.assistant.thinking_delta", {
        text: " think",
        message_id: "a1",
      }),
      evt(4, "message.assistant.text_delta", {
        text: "Done",
        message_id: "a1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      {
        kind: "thinking",
        text: "Let me think",
        messageId: "a1",
        sealed: false,
      },
      { kind: "assistant", text: "Done", messageId: "a1", sealed: false },
    ]);
  });

  it("should attach thinking elapsed time from user to canonical thinking event timestamps", () => {
    const turns = buildTurns([
      evt(
        1,
        "message.user",
        { text: "hi", message_id: "u1" },
        Date.parse("2026-05-07T10:00:00.000Z"),
      ),
      evt(
        2,
        "message.assistant.thinking",
        {
          text: "Let me think",
          message_id: "a1",
        },
        Date.parse("2026-05-07T10:00:02.350Z"),
      ),
    ]);

    expect(turns[0]!.blocks[0]).toEqual({
      kind: "thinking",
      text: "Let me think",
      messageId: "a1",
      sealed: true,
      elapsedMs: 2350,
    });
  });

  it("should attach tool elapsed time from user to tool.call.completed timestamp", () => {
    const turns = buildTurns([
      evt(
        1,
        "message.user",
        { text: "hi", message_id: "u1" },
        Date.parse("2026-05-07T10:00:00.000Z"),
      ),
      evt(
        2,
        "tool.call.started",
        { name: "Read", tool_use_id: "t1", input: "{}" },
        Date.parse("2026-05-07T10:00:01.000Z"),
      ),
      evt(
        3,
        "tool.call.completed",
        { tool_use_id: "t1", content: "ok" },
        Date.parse("2026-05-07T10:00:04.500Z"),
      ),
    ]);

    const toolBlock = turns[0]!.blocks.find((b) => b.kind === "tool");
    expect(toolBlock?.kind).toBe("tool");
    expect((toolBlock as { elapsedMs?: number }).elapsedMs).toBe(4500);
  });

  it("should attach tool elapsed time from user to tool.call.started timestamp when the tool is still running", () => {
    const turns = buildTurns([
      evt(
        1,
        "message.user",
        { text: "hi", message_id: "u1" },
        Date.parse("2026-05-07T10:00:00.000Z"),
      ),
      evt(
        2,
        "tool.call.started",
        { name: "Read", tool_use_id: "t1", input: "{}" },
        Date.parse("2026-05-07T10:00:01.500Z"),
      ),
    ]);

    const toolBlock = turns[0]!.blocks.find((b) => b.kind === "tool");
    expect(toolBlock?.kind).toBe("tool");
    expect((toolBlock as { elapsedMs?: number }).elapsedMs).toBe(1500);
  });

  it("should attach elapsed time to meta tools flushed at end of stream", () => {
    const turns = buildTurns([
      evt(
        1,
        "message.user",
        { text: "hi", message_id: "u1" },
        Date.parse("2026-05-07T10:00:00.000Z"),
      ),
      evt(
        2,
        "message.assistant.delta",
        { text: "done", message_id: "a1" },
        Date.parse("2026-05-07T10:00:01.000Z"),
      ),
      evt(
        3,
        "runtime.engine.cost",
        { engine: "claude", input_tokens: "10" },
        Date.parse("2026-05-07T10:00:05.000Z"),
      ),
    ]);

    const toolBlock = turns[0]!.blocks.find((b) => b.kind === "tool");
    expect(toolBlock?.kind).toBe("tool");
    expect((toolBlock as { elapsedMs?: number }).elapsedMs).toBe(5000);
  });

  it("should keep two AssistantMessages from the same turn as separate blocks", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.text_delta", {
        text: "First",
        message_id: "a1",
      }),
      evt(3, "message.assistant.delta", {
        text: "First message.",
        message_id: "a1",
      }),
      evt(4, "tool.call.started", {
        name: "Read",
        tool_use_id: "t1",
        input: "{}",
      }),
      evt(5, "tool.call.completed", {
        tool_use_id: "t1",
        content: "ok",
      }),
      evt(6, "message.assistant.text_delta", {
        text: "Second",
        message_id: "a2",
      }),
      evt(7, "message.assistant.delta", {
        text: "Second message.",
        message_id: "a2",
      }),
    ]);

    const blocks = turns[0]!.blocks;
    const textBlocks = blocks.filter((b) => b.kind === "assistant");
    expect(textBlocks.map((b) => (b as { text: string }).text)).toEqual([
      "First message.",
      "Second message.",
    ]);
  });

  it("should append to the existing thinking block when text_delta interleaves between thinking_deltas", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.thinking_delta", {
        text: "The user is asking me to ",
        message_id: "a1",
      }),
      evt(3, "message.assistant.text_delta", {
        text: "代码评审是",
        message_id: "a1",
      }),
      evt(4, "message.assistant.thinking_delta", {
        text: "continue writing.",
        message_id: "a1",
      }),
      evt(5, "message.assistant.text_delta", {
        text: "团队知识传递。",
        message_id: "a1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      {
        kind: "thinking",
        text: "The user is asking me to continue writing.",
        messageId: "a1",
        sealed: false,
      },
      {
        kind: "assistant",
        text: "代码评审是团队知识传递。",
        messageId: "a1",
        sealed: false,
      },
    ]);
  });

  it("should dedup re-delivered thinking_delta / text_delta so a phantom block doesn't appear after the canonical sealed", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.thinking_delta", {
        text: "Let me think.",
        message_id: "a1",
      }),
      evt(3, "message.assistant.thinking", {
        text: "Let me think.",
        message_id: "a1",
      }),
      evt(4, "message.assistant.text_delta", {
        text: "Done.",
        message_id: "a1",
      }),
      evt(5, "message.assistant.delta", {
        text: "Done.",
        message_id: "a1",
      }),
      evt(6, "message.assistant.thinking_delta", {
        text: "Let me think.",
        message_id: "a1",
      }),
      evt(7, "message.assistant.text_delta", {
        text: "Done.",
        message_id: "a1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      {
        kind: "thinking",
        text: "Let me think.",
        messageId: "a1",
        sealed: true,
      },
      { kind: "assistant", text: "Done.", messageId: "a1", sealed: true },
    ]);
  });

  it("should fall back to legacy concatenation when message_id is absent (history replay shape)", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi" }),
      evt(2, "message.assistant.delta", { text: "Part one. " }),
      evt(3, "message.assistant.delta", { text: "Part two." }),
    ]);

    const text = turns[0]!.blocks
      .filter((b) => b.kind === "assistant")
      .map((b) => (b as { text: string }).text)
      .join("|");
    expect(text).toBe("Part one. Part two.");
  });

  it("should drop a duplicate message.user event that the SSE adapter re-delivers via DB poll fallback", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.user", { text: "hi", message_id: "u1" }),
      evt(3, "message.assistant.delta", { text: "hello", message_id: "a1" }),
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0]!.userText).toBe("hi");
  });

  it("should dedup SSE double-delivery of thinking, tool, and assistant canonical events", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.thinking", {
        text: "let me think",
        message_id: "a1",
      }),
      evt(3, "message.assistant.thinking", {
        text: "let me think",
        message_id: "a1",
      }),
      evt(4, "tool.call.started", {
        name: "Read",
        tool_use_id: "t1",
        input: "{}",
      }),
      evt(5, "tool.call.started", {
        name: "Read",
        tool_use_id: "t1",
        input: "{}",
      }),
      evt(6, "tool.call.completed", { tool_use_id: "t1", content: "ok" }),
      evt(7, "message.assistant.delta", {
        text: "done",
        message_id: "a1",
      }),
      evt(8, "message.assistant.delta", {
        text: "done",
        message_id: "a1",
      }),
    ]);

    expect(turns).toHaveLength(1);
    const blocks = turns[0]!.blocks;
    expect(blocks.map((b) => b.kind)).toEqual([
      "thinking",
      "tool",
      "assistant",
    ]);
    const tools = blocks.filter((b) => b.kind === "tool");
    expect(tools).toHaveLength(1);
    const thinkingBlocks = blocks.filter((b) => b.kind === "thinking");
    expect(thinkingBlocks).toHaveLength(1);
  });

  it("should keep multi-block AssistantMessage segments interleaved with tools (history replay shape)", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.delta", {
        text: "First message.",
        message_id: "a1",
      }),
      evt(3, "tool.call.started", {
        name: "Read",
        tool_use_id: "t1",
        input: "{}",
      }),
      evt(4, "tool.call.completed", {
        tool_use_id: "t1",
        content: "ok",
      }),
      evt(5, "message.assistant.delta", {
        text: "Second message.",
        message_id: "a1",
      }),
    ]);

    expect(turns).toHaveLength(1);
    const blocks = turns[0]!.blocks;
    expect(blocks.map((b) => b.kind)).toEqual([
      "assistant",
      "tool",
      "assistant",
    ]);
    const textBlocks = blocks.filter((b) => b.kind === "assistant");
    expect(textBlocks.map((b) => (b as { text: string }).text)).toEqual([
      "First message.",
      "Second message.",
    ]);
  });

  it("should still open a new turn for a genuine subsequent user message in the same session", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "message.assistant.delta", { text: "hello", message_id: "a1" }),
      evt(3, "message.user", { text: "hi", message_id: "u2" }),
      evt(4, "message.assistant.delta", { text: "again", message_id: "a2" }),
    ]);

    expect(turns).toHaveLength(2);
    expect(turns[0]!.userText).toBe("hi");
    expect(turns[1]!.userText).toBe("hi");
  });
});

describe("buildTurns — user interrupt", () => {
  it("marks the current turn as cancelled on session.idle with user_interrupt", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "stop me", message_id: "u1" }),
      evt(2, "message.assistant.text_delta", {
        text: "Partial answer",
        message_id: "a1",
      }),
      evt(3, "session.idle", { stop_reason: "user_interrupt" }),
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0]!.cancelled).toBe(true);
    expect(turns[0]!.failedMessage).toBeNull();
    expect(turns[0]!.blocks).toEqual([
      {
        kind: "assistant",
        text: "Partial answer",
        messageId: "a1",
        sealed: false,
      },
    ]);
  });

  it("recognizes serialized stop_reason objects without marking end_turn", () => {
    const interrupted = buildTurns([
      evt(1, "message.user", { text: "stop me", message_id: "u1" }),
      evt(2, "session.idle", {
        stop_reason: JSON.stringify({ type: "user_interrupt" }),
      }),
    ]);
    const clean = buildTurns([
      evt(1, "message.user", { text: "done", message_id: "u1" }),
      evt(2, "session.idle", { stop_reason: "end_turn" }),
    ]);

    expect(interrupted[0]!.cancelled).toBe(true);
    expect(clean[0]!.cancelled).toBe(false);
  });
});

describe("buildTurns — runtime interrupt (not user cancel)", () => {
  it("marks session.idle with category 'interrupted' as interrupted, not cancelled", () => {
    const bare = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "session.idle", { stop_reason: "interrupted" }),
    ]);
    expect(bare[0]!.interrupted).toBe(true);
    expect(bare[0]!.cancelled).toBeFalsy();

    const serialized = buildTurns([
      evt(1, "message.user", { text: "hi", message_id: "u1" }),
      evt(2, "session.idle", {
        stop_reason: JSON.stringify({ category: "interrupted" }),
      }),
    ]);
    expect(serialized[0]!.interrupted).toBe(true);
    expect(serialized[0]!.cancelled).toBeFalsy();
  });

  it("run.failed with category 'interrupted' is interrupted, not a hard failure", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "go", message_id: "u1" }),
      evt(2, "run.failed", {
        category: "interrupted",
        message: "runtime process interrupted: boom",
      }),
    ]);
    expect(turns[0]!.interrupted).toBe(true);
    expect(turns[0]!.cancelled).toBeFalsy();
    expect(turns[0]!.failedMessage).toBeNull();
  });
});

describe("buildTurns — attachment names", () => {
  it("derives the attachment name from source_path (original file)", () => {
    const turns = buildTurns([
      evt(1, "message.user", {
        text: "summarize",
        message_id: "u1",
        attachments: JSON.stringify([
          { source_path: "/ws/report.pdf", parsed_path: "/ws/report.md" },
        ]),
      }),
    ]);

    expect(turns[0]!.attachments).toEqual([{ name: "report.pdf", size: 0 }]);
  });

  it("falls back to the legacy filepath key on pre-split events", () => {
    const turns = buildTurns([
      evt(1, "message.user", {
        text: "look",
        message_id: "u1",
        attachments: JSON.stringify([{ filepath: "/ws/old.parsed.md" }]),
      }),
    ]);

    // Legacy events stored only the parsed path; the ``.parsed.md`` suffix is
    // stripped for display.
    expect(turns[0]!.attachments).toEqual([{ name: "old", size: 0 }]);
  });
});

describe("buildTurns — compaction marker", () => {
  it("appends a compaction block when a session.compaction event lands", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "/compact", message_id: "u1" }),
      evt(2, "session.compaction", { message_id: "a1" }),
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0]!.blocks).toEqual([{ kind: "compaction", messageId: "a1" }]);
  });

  it("places the marker inline between assistant blocks (autocompact mid-turn)", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "go", message_id: "u1" }),
      evt(2, "message.assistant.delta", { text: "before", message_id: "a1" }),
      evt(3, "session.compaction", { message_id: "a1" }),
      evt(4, "message.assistant.delta", { text: "after", message_id: "a2" }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "before", messageId: "a1", sealed: true },
      { kind: "compaction", messageId: "a1" },
      { kind: "assistant", text: "after", messageId: "a2", sealed: true },
    ]);
  });

  it("dedups a compaction event delivered twice (live broadcast + persisted replay)", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "/compact", message_id: "u1" }),
      // Live broadcast frame (seq 0) and its persisted replay carry the same
      // stamped message_id — only one divider should result.
      evt(0, "session.compaction", { message_id: "a1" }),
      evt(2, "session.compaction", { message_id: "a1" }),
    ]);

    expect(turns[0]!.blocks).toEqual([{ kind: "compaction", messageId: "a1" }]);
  });
});

describe("buildTurns — tool input/output streaming", () => {
  const toolBlock = (turn: ReturnType<typeof buildTurns>[number]) =>
    turn.blocks.find((b) => b.kind === "tool") as
      Extract<(typeof turn.blocks)[number], { kind: "tool" }> | undefined;

  it("builds a running card from the first input_delta, before tool.call.started", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "write a file", message_id: "u1" }),
      evt(2, "tool.call.input_delta", {
        tool_use_id: "t1",
        name: "Write",
        text: '{"file_path":"/a.txt",',
        message_id: "a1",
      }),
      evt(3, "tool.call.input_delta", {
        tool_use_id: "t1",
        text: '"content":"hello"}',
        message_id: "a1",
      }),
    ]);

    const tool = toolBlock(turns[0]!);
    expect(tool?.tool.title).toBe("Write");
    expect(tool?.tool.status).toBe("running");
    // Partial-JSON chunks accumulate onto the same card.
    expect(tool?.tool.input).toBe('{"file_path":"/a.txt","content":"hello"}');
    // Exactly one card — no duplicate block.
    expect(turns[0]!.blocks.filter((b) => b.kind === "tool")).toHaveLength(1);
  });

  it("reconciles the streamed card with the canonical input on tool.call.started (no duplicate)", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "go", message_id: "u1" }),
      evt(2, "tool.call.input_delta", {
        tool_use_id: "t1",
        name: "Write",
        text: '{"file_pa',
        message_id: "a1",
      }),
      evt(3, "tool.call.started", {
        tool_use_id: "t1",
        name: "Write",
        input: '{"file_path":"/a.txt","content":"hello"}',
        message_id: "a1",
      }),
      evt(4, "tool.call.completed", {
        tool_use_id: "t1",
        content: "wrote 1 file",
      }),
    ]);

    expect(turns[0]!.blocks.filter((b) => b.kind === "tool")).toHaveLength(1);
    const tool = toolBlock(turns[0]!);
    expect(tool?.tool.status).toBe("success");
    // Canonical full input replaced the partial-JSON preview.
    expect(tool?.tool.input).toBe('{"file_path":"/a.txt","content":"hello"}');
    expect(tool?.tool.output).toBe("wrote 1 file");
  });

  it("accumulates output_delta onto a running card between started and completed", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "run", message_id: "u1" }),
      evt(2, "tool.call.started", {
        tool_use_id: "t1",
        name: "Bash",
        input: '{"cmd":"echo hi"}',
        message_id: "a1",
      }),
      evt(3, "tool.call.output_delta", { tool_use_id: "t1", text: "line 1\n" }),
      evt(4, "tool.call.output_delta", { tool_use_id: "t1", text: "line 2\n" }),
    ]);

    const tool = toolBlock(turns[0]!);
    expect(tool?.tool.status).toBe("running");
    expect(tool?.tool.output).toBe("line 1\nline 2\n");
  });

  it("lets completed replace streamed output with the canonical aggregated output", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "run", message_id: "u1" }),
      evt(2, "tool.call.started", {
        tool_use_id: "t1",
        name: "Bash",
        message_id: "a1",
      }),
      evt(3, "tool.call.output_delta", { tool_use_id: "t1", text: "partial" }),
      evt(4, "tool.call.completed", {
        tool_use_id: "t1",
        content: "full output",
      }),
    ]);

    const tool = toolBlock(turns[0]!);
    expect(tool?.tool.status).toBe("success");
    expect(tool?.tool.output).toBe("full output");
  });
});

describe("mergeEventWindow — resume window merge", () => {
  it("returns prev unchanged when the window brings nothing new", () => {
    const prev = [evt(1, "message.user", { text: "hi" })];
    const out = mergeEventWindow(prev, [evt(1, "message.user", { text: "hi" })]);
    expect(out).toBe(prev); // identity — no re-render
  });

  it("fills an empty prev with the whole window (the blank-resume case)", () => {
    const win = [
      evt(1, "message.user", { text: "hi" }),
      evt(2, "message.assistant.delta", { text: "past", message_id: "a1" }),
    ];
    expect(mergeEventWindow([], win).map((e) => e.seq)).toEqual([1, 2]);
  });

  it("inserts missing history BEFORE live seq-0 deltas, never sorting deltas to the front", () => {
    // prev: only the in-flight message's live deltas (what a mid-turn resume has)
    const prev = [
      evt(0, "message.assistant.text_delta", { text: "now", message_id: "a2" }),
      evt(0, "message.assistant.text_delta", { text: "…", message_id: "a2" }),
    ];
    const win = [
      evt(1, "message.user", { text: "hi" }),
      evt(2, "message.assistant.delta", { text: "past", message_id: "a1" }),
    ];
    const out = mergeEventWindow(prev, win);
    // history first, live tail last — and the two deltas keep their order
    expect(out.map((e) => e.seq)).toEqual([1, 2, 0, 0]);
    expect(out[2]).toBe(prev[0]);
    expect(out[3]).toBe(prev[1]);
  });

  it("keeps seq-0 deltas glued in place when older persisted rows are backfilled", () => {
    const delta = evt(0, "message.assistant.text_delta", {
      text: "live",
      message_id: "a2",
    });
    const prev = [evt(3, "message.user", { text: "again" }), delta];
    const win = [
      evt(1, "message.user", { text: "hi" }),
      evt(2, "message.assistant.delta", { text: "past", message_id: "a1" }),
      evt(3, "message.user", { text: "again" }),
    ];
    const out = mergeEventWindow(prev, win);
    expect(out.map((e) => e.seq)).toEqual([1, 2, 3, 0]);
    expect(out[3]).toBe(delta); // still right after its seq-3 anchor
  });

  it("tail-appends window rows newer than everything in prev (gap-fill)", () => {
    const prev = [
      evt(1, "message.user", { text: "hi" }),
      evt(0, "message.assistant.text_delta", { text: "live", message_id: "a1" }),
    ];
    const win = [evt(2, "tool.call.started", { name: "shell" })];
    expect(mergeEventWindow(prev, win).map((e) => e.seq)).toEqual([1, 0, 2]);
  });
});

describe("buildTurns — segmented assistant message (mid-turn canonical seal)", () => {
  // GPT-5.5-style provider-native search turns: the runtime seals segment 1
  // with a canonical ``message.assistant.delta`` MID-TURN, then keeps
  // streaming segment 2 deltas under the SAME turn-scoped message_id with no
  // tool/thinking block in between. The old blanket "delta after sealed
  // same-id block" drop rendered the whole second segment blank until its
  // canonical landed.
  it("keeps streaming a continuation segment after a mid-turn seal (same id, nothing between)", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(0, "message.assistant.text_delta", { text: "seg1", message_id: "a1" }),
      evt(2, "message.assistant.delta", { text: "seg1-full", message_id: "a1" }),
      evt(0, "message.assistant.text_delta", { text: "seg2-part1 ", message_id: "a1" }),
      evt(0, "message.assistant.text_delta", { text: "seg2-part2", message_id: "a1" }),
    ]);
    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "seg1-full", messageId: "a1", sealed: true },
      {
        kind: "assistant",
        text: "seg2-part1 seg2-part2",
        messageId: "a1",
        sealed: false,
      },
    ]);
  });

  it("the continuation block seals in place when segment 2's canonical arrives", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(2, "message.assistant.delta", { text: "seg1-full", message_id: "a1" }),
      evt(0, "message.assistant.text_delta", { text: "seg2 draft", message_id: "a1" }),
      evt(3, "message.assistant.delta", { text: "seg2-full", message_id: "a1" }),
    ]);
    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "seg1-full", messageId: "a1", sealed: true },
      { kind: "assistant", text: "seg2-full", messageId: "a1", sealed: true },
    ]);
  });

  it("still drops a re-delivered chunk already contained in the sealed text", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(2, "message.assistant.delta", { text: "Hello world", message_id: "a1" }),
      evt(0, "message.assistant.text_delta", { text: "world", message_id: "a1" }),
    ]);
    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "Hello world", messageId: "a1", sealed: true },
    ]);
  });
});

describe("buildTurns — concurrent subagent events (parent_tool_use_id)", () => {
  // Incident shape: a background Task/Agent run executes CONCURRENTLY with
  // the lead's own streaming, so its tool events land interleaved between
  // the lead's text_delta frames. Untagged, each one used to shred the
  // streaming text into fragments; tagged, they are out-of-band and must
  // leave the lead's open block alone.
  it("should keep the lead's streaming text in one block when tagged subagent tool events interleave", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(0, "message.assistant.text_delta", { text: "已并行", message_id: "a1" }),
      evt(2, "tool.call.started", {
        id: "t1",
        tool_use_id: "t1",
        name: "mcp__valuz-search__news_search",
        parent_tool_use_id: "agent-1",
      }),
      evt(0, "message.assistant.text_delta", {
        text: "启动两个任务",
        message_id: "a1",
      }),
      evt(3, "tool.call.completed", {
        id: "t1",
        tool_use_id: "t1",
        content: "401",
        is_error: "true",
        parent_tool_use_id: "agent-1",
      }),
      evt(0, "message.assistant.text_delta", { text: "，稍等", message_id: "a1" }),
      evt(4, "message.assistant.delta", {
        text: "已并行启动两个任务，稍等",
        message_id: "a1",
      }),
    ]);

    const assistantBlocks = turns[0]!.blocks.filter(
      (b) => b.kind === "assistant",
    );
    expect(assistantBlocks).toEqual([
      {
        kind: "assistant",
        text: "已并行启动两个任务，稍等",
        messageId: "a1",
        sealed: true,
      },
    ]);
  });

  it("should tag interleaved subagent tool blocks with parentToolUseId", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(0, "message.assistant.text_delta", { text: "hi", message_id: "a1" }),
      evt(2, "tool.call.started", {
        id: "t1",
        tool_use_id: "t1",
        name: "mcp__valuz-stock__index_quote",
        parent_tool_use_id: "agent-1",
      }),
    ]);

    const toolBlock = turns[0]!.blocks.find((b) => b.kind === "tool");
    expect(toolBlock).toMatchObject({ parentToolUseId: "agent-1" });
  });

  it("should not let a tagged subagent canonical claim the lead's open streaming block", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(0, "message.assistant.text_delta", {
        text: "Lead says",
        message_id: "a1",
      }),
      evt(2, "message.assistant.delta", {
        text: "Subagent report",
        message_id: "a1",
        parent_tool_use_id: "agent-1",
      }),
      evt(0, "message.assistant.text_delta", { text: " more", message_id: "a1" }),
      evt(3, "message.assistant.delta", {
        text: "Lead says more",
        message_id: "a1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "Lead says more", messageId: "a1", sealed: true },
      {
        kind: "assistant",
        text: "Subagent report",
        messageId: "a1",
        sealed: true,
        parentToolUseId: "agent-1",
      },
    ]);
  });

  it("should still split the streaming text on an UNTAGGED tool event (sequential-runtime behavior preserved)", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(0, "message.assistant.text_delta", { text: "A", message_id: "a1" }),
      evt(2, "tool.call.started", {
        id: "t1",
        tool_use_id: "t1",
        name: "Bash",
      }),
      evt(0, "message.assistant.text_delta", { text: "B", message_id: "a1" }),
    ]);

    const assistantBlocks = turns[0]!.blocks.filter(
      (b) => b.kind === "assistant",
    );
    expect(assistantBlocks).toEqual([
      { kind: "assistant", text: "A", messageId: "a1", sealed: false },
      { kind: "assistant", text: "B", messageId: "a1", sealed: false },
    ]);
  });

  it("should keep the parentToolUseId tag when tool.call.completed lacks it but started carried it", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(2, "tool.call.started", {
        id: "t1",
        tool_use_id: "t1",
        name: "Read",
        parent_tool_use_id: "agent-1",
      }),
      evt(3, "tool.call.completed", {
        id: "t1",
        tool_use_id: "t1",
        content: "ok",
      }),
    ]);

    const toolBlock = turns[0]!.blocks.find((b) => b.kind === "tool");
    expect(toolBlock).toMatchObject({
      parentToolUseId: "agent-1",
      tool: { status: "success" },
    });
  });
});

describe("buildTurns — concurrent subagent streaming (tagged deltas)", () => {
  it("should stream tagged subagent deltas into their own block without touching the lead's open block", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(0, "message.assistant.text_delta", { text: "Lead ", message_id: "a1" }),
      evt(0, "message.assistant.text_delta", {
        text: "Sub ",
        message_id: "a1",
        parent_tool_use_id: "agent-1",
      }),
      evt(0, "message.assistant.text_delta", { text: "says", message_id: "a1" }),
      evt(0, "message.assistant.text_delta", {
        text: "reports",
        message_id: "a1",
        parent_tool_use_id: "agent-1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      { kind: "assistant", text: "Lead says", messageId: "a1", sealed: false },
      {
        kind: "assistant",
        text: "Sub reports",
        messageId: "a1",
        sealed: false,
        parentToolUseId: "agent-1",
      },
    ]);
  });

  it("should let a tagged canonical seal the matching tagged open block", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(0, "message.assistant.text_delta", {
        text: "Sub par",
        message_id: "a1",
        parent_tool_use_id: "agent-1",
      }),
      evt(2, "message.assistant.delta", {
        text: "Sub partial done",
        message_id: "a1",
        parent_tool_use_id: "agent-1",
      }),
    ]);

    expect(turns[0]!.blocks).toEqual([
      {
        kind: "assistant",
        text: "Sub partial done",
        messageId: "a1",
        sealed: true,
        parentToolUseId: "agent-1",
      },
    ]);
  });

  it("should keep two concurrent subagent streams in separate blocks", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(0, "message.assistant.text_delta", {
        text: "A1 ",
        message_id: "a1",
        parent_tool_use_id: "agent-1",
      }),
      evt(0, "message.assistant.text_delta", {
        text: "B1 ",
        message_id: "a1",
        parent_tool_use_id: "agent-2",
      }),
      evt(0, "message.assistant.text_delta", {
        text: "A2",
        message_id: "a1",
        parent_tool_use_id: "agent-1",
      }),
      evt(0, "message.assistant.text_delta", {
        text: "B2",
        message_id: "a1",
        parent_tool_use_id: "agent-2",
      }),
    ]);

    const texts = turns[0]!.blocks.map((b) =>
      b.kind === "assistant" ? [b.text, b.parentToolUseId] : null,
    );
    expect(texts).toEqual([
      ["A1 A2", "agent-1"],
      ["B1 B2", "agent-2"],
    ]);
  });

  it("should split a subagent's own text at its OWN tool call (per-flow sequential semantics preserved)", () => {
    const turns = buildTurns([
      evt(1, "message.user", { text: "q", message_id: "u1" }),
      evt(0, "message.assistant.text_delta", {
        text: "before",
        message_id: "a1",
        parent_tool_use_id: "agent-1",
      }),
      evt(2, "tool.call.started", {
        id: "t1",
        tool_use_id: "t1",
        name: "Bash",
        parent_tool_use_id: "agent-1",
      }),
      evt(0, "message.assistant.text_delta", {
        text: "after",
        message_id: "a1",
        parent_tool_use_id: "agent-1",
      }),
    ]);

    const subBlocks = turns[0]!.blocks.filter(
      (b) => b.kind === "assistant" && b.parentToolUseId === "agent-1",
    );
    expect(subBlocks).toEqual([
      {
        kind: "assistant",
        text: "before",
        messageId: "a1",
        sealed: false,
        parentToolUseId: "agent-1",
      },
      {
        kind: "assistant",
        text: "after",
        messageId: "a1",
        sealed: false,
        parentToolUseId: "agent-1",
      },
    ]);
  });
});
