import type { SessionEventDTO } from "../api/sessions-api";
import type {
  ConversationBlock,
  ConversationTurn,
  ConversationTurnAttachment,
  PrototypeToolCall,
} from "@valuz/shared";
import { t } from "@valuz/shared/i18n";

/* ── Helpers ───────────────────────────────────────────────── */

const parseTurnAttachments = (raw: string): ConversationTurnAttachment[] => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.map((entry) => {
    if (!entry || typeof entry !== "object") {
      return { name: "unknown", size: 0 };
    }
    const obj = entry as Record<string, unknown>;
    const explicitName = typeof obj.name === "string" ? obj.name : undefined;
    // `source_path` is the original file; `filepath` is the legacy single-path
    // key still present on user_message events persisted before the split.
    const sourcePath =
      typeof obj.source_path === "string"
        ? obj.source_path
        : typeof obj.filepath === "string"
          ? obj.filepath
          : undefined;
    const fromPath = sourcePath
      ? (sourcePath.split("/").pop() ?? sourcePath).replace(/\.parsed\.md$/, "")
      : undefined;
    const size = typeof obj.size === "number" ? obj.size : 0;
    return {
      name: explicitName ?? fromPath ?? "unknown",
      size,
    };
  });
};

export const resolveToolKind = (name: string): PrototypeToolCall["kind"] => {
  if (name.includes("skill")) return "skill";
  if (name.includes("search") || name.includes("doc")) return "kb";
  if (name.includes("bash") || name.includes("shell")) return "bash";
  if (name.includes("file")) return "file";
  return "fetch";
};

const payloadToBlock = (payload: Record<string, string>) =>
  Object.entries(payload)
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}: ${value}`)
    .join("\n");

const elapsedSince = (
  startTimestamp: number | undefined,
  endTimestamp: number | undefined,
): number | undefined => {
  if (!startTimestamp || !endTimestamp) return undefined;
  const start = new Date(startTimestamp).getTime();
  const end = new Date(endTimestamp).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) {
    return undefined;
  }
  return end - start;
};

/**
 * Classify a ``stop_reason`` / ``category`` value as a user cancel vs a
 * runtime/system interruption vs neither.
 *
 * Both render as a quiet grey line (not an ``ErrorMessageCard``), but they carry
 * DIFFERENT labels: ``user_interrupt`` is the user pressing Stop; ``interrupted``
 * is the agent subprocess being torn down / crashing mid-turn (see the kernel's
 * ``is_runtime_interruption``). Collapsing the two made a runtime crash render as
 * "用户取消了当前对话" — blaming the user for a system failure. Keep them apart.
 *
 * Accepts the bare string or a serialized ``{type|category}`` object.
 */
const interruptKind = (value: unknown): "user" | "runtime" | null => {
  const classify = (s: string): "user" | "runtime" | null => {
    const n = s.trim().toLowerCase();
    if (n === "user_interrupt") return "user";
    if (n === "interrupted") return "runtime";
    return null;
  };
  if (typeof value !== "string") return null;
  const direct = classify(value);
  if (direct) return direct;
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const obj = parsed as Record<string, unknown>;
    for (const key of ["type", "category"] as const) {
      const v = obj[key];
      if (typeof v === "string") {
        const c = classify(v);
        if (c) return c;
      }
    }
    return null;
  } catch {
    return null;
  }
};

const toMetaToolCall = (
  eventType: string,
  payload: Record<string, string>,
  seq: number,
): PrototypeToolCall | null => {
  if (eventType === "runtime.context.compiled") {
    return {
      id: `meta-compiled-${seq}`,
      kind: "kb",
      title: "runtime.context.compiled",
      subtitle: `project ${payload.project_id || "none"} · model ${payload.model || "default"}`,
      status: "success",
      output: payloadToBlock(payload),
    };
  }
  if (eventType === "runtime.engine.bound") {
    return {
      id: `meta-engine-${seq}`,
      kind: "fetch",
      title: "runtime.engine.bound",
      subtitle: `engine ${payload.engine || "unknown"}`,
      status: "success",
      output: payloadToBlock(payload),
    };
  }
  if (eventType === "runtime.engine.cost") {
    return {
      id: `meta-cost-${seq}`,
      kind: "fetch",
      title: "runtime.engine.cost",
      subtitle: "usage summary",
      status: "cached",
      output: payloadToBlock(payload),
    };
  }
  return null;
};

/* ── Event-window merge ────────────────────────────────────── */

/**
 * Merge a fetched transcript window into the live ``events`` array without
 * disturbing what's already there.
 *
 * ``buildTurns`` consumes events in ARRAY ORDER, and the array mixes two kinds
 * of entries: persisted rows (``seq > 0``, totally ordered by seq) and live
 * unpersisted frames (``seq === 0`` — streaming deltas of the in-flight
 * message). The merge rules follow from that:
 *
 * - Only genuinely-missing persisted rows are added (dedup by seq).
 * - Each missing row is INSERTED before the first existing persisted row with
 *   a larger seq — not tail-appended (history would render after the current
 *   turn) and not global-sorted (``seq 0`` compares lowest, so a sort throws
 *   the streaming deltas to the FRONT of the transcript).
 * - ``seq === 0`` entries stay glued exactly where they arrived: a delta
 *   re-ordered across its message's persisted seal renders as duplicated text.
 * - Missing rows newer than every existing persisted row land at the tail —
 *   the same position the live stream would have delivered them to.
 */
export const mergeEventWindow = (
  prev: SessionEventDTO[],
  incoming: SessionEventDTO[],
): SessionEventDTO[] => {
  const seen = new Set<number>();
  for (const e of prev) {
    if (e.seq > 0) seen.add(e.seq);
  }
  const missing = incoming
    .filter((e) => e.seq > 0 && !seen.has(e.seq))
    .sort((a, b) => a.seq - b.seq);
  if (missing.length === 0) return prev;
  const out: SessionEventDTO[] = [];
  let mi = 0;
  // A LEADING run of live entries has no persisted anchor to glue to — it is
  // the in-flight tail of a resume that hasn't loaded history yet (the blank
  // case), so history smaller than the first persisted row must go BEFORE it,
  // not after.
  const firstPersistedSeq = prev.find((e) => e.seq > 0)?.seq ?? Infinity;
  while (mi < missing.length && missing[mi].seq < firstPersistedSeq) {
    out.push(missing[mi]);
    mi += 1;
  }
  for (const e of prev) {
    if (e.seq > 0) {
      while (mi < missing.length && missing[mi].seq < e.seq) {
        out.push(missing[mi]);
        mi += 1;
      }
    }
    out.push(e);
  }
  while (mi < missing.length) {
    out.push(missing[mi]);
    mi += 1;
  }
  return out;
};

/* ── Turn builder ──────────────────────────────────────────── */

/**
 * Resumable turn builder. The event-fold state (turns, currentTurn,
 * activeToolCalls, dedup set, pending meta events) lives in this closure so a
 * caller can feed events in successive slices — ``pushAll(sliceA)`` then
 * ``pushAll(sliceB)`` — and get the SAME result as one ``pushAll([...A, ...B])``.
 * That resumability is what lets the streaming transcript append a token
 * without re-folding the whole event history each render (see
 * ``createIncrementalTurns`` / ``useIncrementalTurns``). ``buildTurns`` below is
 * the one-shot form used everywhere a full rebuild is wanted.
 */
const createTurnsBuilder = () => {
  const turns: ConversationTurn[] = [];
  let currentTurn: ConversationTurn | null = null;
  const activeToolCalls = new Map<string, PrototypeToolCall>();
  let lastUserSig: string | null = null;

  const ensureTurn = () => {
    if (!currentTurn) {
      currentTurn = {
        id: `turn-${turns.length + 1}`,
        userMessageSeq: 0,
        userText: "",
        blocks: [],
        failedMessage: null,
        cancelled: false,
      };
      turns.push(currentTurn);
    }
    return currentTurn;
  };

  // A turn can carry several concurrent FLOWS: the lead's own sequential
  // stream (``parentToolUseId === undefined``) plus one per subagent
  // (Task/Agent tool run, keyed by that call's tool_use_id — stamped on the
  // wire as ``parent_tool_use_id``). A background agent executes
  // CONCURRENTLY with the lead's streaming, so its events land interleaved
  // between the lead's delta frames. Every helper below therefore operates
  // on ONE flow at a time and treats blocks of other flows as invisible —
  // within a single flow the original sequential semantics (tool call
  // terminates the open text; canonical seals per segment) are unchanged.
  // Untagged events only ever see untagged blocks, so pre-existing behavior
  // (and any event stream from an older backend, which carries no tags) is
  // byte-for-byte identical.
  const flowOf = (b: ConversationBlock): string | undefined =>
    "parentToolUseId" in b ? b.parentToolUseId || undefined : undefined;

  const matchesLastUnsealed = (
    turn: ConversationTurn,
    kind: "assistant" | "thinking",
    messageId: string | undefined,
    parentToolUseId: string | undefined,
  ): (ConversationBlock & { kind: "assistant" | "thinking" }) | null => {
    for (let i = turn.blocks.length - 1; i >= 0; i--) {
      const b = turn.blocks[i];
      if (flowOf(b) !== parentToolUseId) continue; // other flow — invisible
      if (b.kind === "tool") return null;
      if (b.kind === kind) {
        if (b.sealed) return null;
        if (messageId !== undefined && b.messageId !== messageId) return null;
        return b as ConversationBlock & { kind: "assistant" | "thinking" };
      }
    }
    return null;
  };

  /** Last block of the given flow — so the sealed-redelivery check in
   * ``appendDelta`` still sees this flow's sealed canonical even when
   * another flow's events landed after it. */
  const lastFlowBlock = (
    turn: ConversationTurn,
    parentToolUseId: string | undefined,
  ): ConversationBlock | null => {
    for (let i = turn.blocks.length - 1; i >= 0; i--) {
      const b = turn.blocks[i];
      if (flowOf(b) !== parentToolUseId) continue;
      return b;
    }
    return null;
  };

  const appendDelta = (
    turn: ConversationTurn,
    kind: "assistant" | "thinking",
    text: string,
    messageId: string | undefined,
    parentToolUseId: string | undefined,
  ) => {
    if (!text) return;
    const open = matchesLastUnsealed(turn, kind, messageId, parentToolUseId);
    if (open) {
      open.text += text;
      return;
    }
    const last = lastFlowBlock(turn, parentToolUseId);
    if (
      last &&
      last.kind === kind &&
      last.sealed &&
      (messageId === undefined || last.messageId === messageId) &&
      // Drop only a genuine re-delivery — a chunk the sealed canonical text
      // already contains. A chunk with NEW content is a CONTINUATION segment:
      // runtimes that seal mid-turn (canonical per segment, e.g. around
      // provider-native search with no tool block in between) keep streaming
      // the next segment under the same turn-scoped message_id. The old
      // blanket drop rendered that whole segment blank until its canonical
      // landed ("no streaming, everything pops at once").
      last.text.includes(text)
    ) {
      return;
    }
    turn.blocks.push({ kind, text, messageId, sealed: false, parentToolUseId });
  };

  const replaceWithCanonical = (
    turn: ConversationTurn,
    kind: "assistant" | "thinking",
    text: string,
    messageId: string | undefined,
    elapsedMs?: number,
    parentToolUseId?: string,
  ) => {
    if (!text) return;
    const open = matchesLastUnsealed(turn, kind, messageId, parentToolUseId);
    if (open) {
      if (messageId != null) {
        open.text = text;
        open.sealed = true;
      } else {
        open.text += text;
      }
      if (open.kind === "thinking" && elapsedMs !== undefined) {
        open.elapsedMs = elapsedMs;
      }
      return;
    }
    turn.blocks.push(
      kind === "thinking"
        ? {
            kind,
            text,
            messageId,
            sealed: messageId != null,
            elapsedMs,
            parentToolUseId,
          }
        : { kind, text, messageId, sealed: messageId != null, parentToolUseId },
    );
  };

  interface MetaEvent {
    type: string;
    payload: Record<string, string>;
    timestamp: number | undefined;
  }
  const metaEvents: MetaEvent[] = [];

  const seenEventSigs = new Set<string>();
  const eventSig = (type: string, p: Record<string, string>): string | null => {
    if (type === "message.user")
      return `u::${p.message_id ?? ""}::${p.text ?? ""}`;
    if (type === "message.assistant.delta")
      return `a::${p.message_id ?? ""}::${p.text ?? ""}`;
    if (type === "message.assistant.thinking")
      return `t::${p.message_id ?? ""}::${p.text ?? ""}`;
    if (type === "message.assistant.text_delta")
      return `xd::${p.message_id ?? ""}::${p.text ?? ""}`;
    if (type === "message.assistant.thinking_delta")
      return `td::${p.message_id ?? ""}::${p.text ?? ""}`;
    if (type === "tool.call.started")
      return `ts::${p.id || p.tool_use_id || p.call_id || ""}`;
    if (type === "tool.call.completed")
      return `tc::${p.id || p.tool_use_id || p.call_id || ""}`;
    if (type === "session.compaction") return `cmp::${p.message_id ?? ""}`;
    return null;
  };

  const pushAll = (events: SessionEventDTO[]): void => {
    for (const envelope of events) {
    const { event_type: eventType, payload } = envelope.event;

    const sig = eventSig(eventType, payload);
    if (sig !== null) {
      if (seenEventSigs.has(sig)) continue;
      seenEventSigs.add(sig);
    }

    // Track the latest timestamp seen within the current turn so the
    // header can show ``已处理 X 秒`` even for turns that never fired a
    // thinking/tool block (a plain Q&A would otherwise have totalElapsedMs
    // = 0 and skip the header). Updated on EVERY event in the turn so
    // ``endTimestamp`` always reflects the most recent activity.
    if (currentTurn && envelope.timestamp) {
      currentTurn.endTimestamp = envelope.timestamp;
    }

    if (eventType === "message.user") {
      const userText = payload.text ?? "";
      const userSig = `${payload.message_id ?? ""}::${userText}`;
      if (userSig === lastUserSig) {
        continue;
      }
      lastUserSig = userSig;
      if (metaEvents.length && turns.length > 0) {
        const previousTurn = turns[turns.length - 1];
        for (const [i, item] of metaEvents.entries()) {
          const tool = toMetaToolCall(
            item.type,
            item.payload,
            envelope.seq + i,
          );
          if (tool) {
            const elapsedMs = elapsedSince(
              previousTurn.userTimestamp,
              item.timestamp,
            );
            previousTurn.blocks.push({ kind: "tool", tool, elapsedMs });
          }
        }
        metaEvents.length = 0;
      }
      currentTurn = {
        // ``envelope.seq`` is 0 for live SSE frames that haven't been
        // persisted yet (the kernel's broadcast sink emits them with
        // ``seq=0`` before the DB id is assigned). Two unpersisted
        // user-message frames in the same render — the broadcast +
        // its later DB-replay copy — would both produce ``turn-0`` and
        // collide on the React key, so the virtualizer would reuse
        // the same DOM node for two distinct turns. Prefer the stable
        // ``message_id`` (UUID) when available, fall back to the
        // ``envelope.seq`` only when message_id is missing.
        id: payload.message_id
          ? `turn-${payload.message_id}`
          : `turn-${envelope.seq}`,
        userMessageSeq: envelope.seq,
        userText,
        blocks: [],
        failedMessage: null,
        cancelled: false,
        attachments: payload.attachments
          ? parseTurnAttachments(payload.attachments)
          : undefined,
        userTimestamp: envelope.timestamp,
      };
      turns.push(currentTurn);
      activeToolCalls.clear();
      continue;
    }

    if (eventType === "session.idle") {
      if (currentTurn) {
        const kind = interruptKind(payload.stop_reason);
        if (kind === "user") currentTurn.cancelled = true;
        else if (kind === "runtime") currentTurn.interrupted = true;
      }
      continue;
    }

    if (eventType === "session.update") {
      if (payload.status === "cancelled" && currentTurn) {
        currentTurn.cancelled = true;
      }
      continue;
    }

    const turn = ensureTurn();

    if (eventType === "session.compaction") {
      // A context compaction happened in this turn (``/compact`` or
      // autocompact). Push a single label-only marker block; the event's
      // raw data is intentionally NOT parsed for display. For codex's
      // ``/compact`` the "Compacted." reply is suppressed upstream, so this
      // marker is the only visible artifact of the turn.
      turn.blocks.push({ kind: "compaction", messageId: payload.message_id });
      continue;
    }

    if (eventType === "message.assistant.text_delta") {
      appendDelta(
        turn,
        "assistant",
        payload.text ?? "",
        payload.message_id,
        payload.parent_tool_use_id || undefined,
      );
      continue;
    }

    if (eventType === "message.assistant.thinking_delta") {
      appendDelta(
        turn,
        "thinking",
        payload.text ?? "",
        payload.message_id,
        payload.parent_tool_use_id || undefined,
      );
      continue;
    }

    if (eventType === "message.assistant.delta") {
      replaceWithCanonical(
        turn,
        "assistant",
        payload.text ?? "",
        payload.message_id,
        undefined,
        payload.parent_tool_use_id || undefined,
      );
      continue;
    }

    if (eventType === "message.assistant.thinking") {
      replaceWithCanonical(
        turn,
        "thinking",
        payload.text ?? "",
        payload.message_id,
        elapsedSince(turn.userTimestamp, envelope.timestamp),
        payload.parent_tool_use_id || undefined,
      );
      continue;
    }

    if (
      eventType === "runtime.context.compiled" ||
      eventType === "runtime.engine.bound" ||
      eventType === "runtime.engine.cost"
    ) {
      metaEvents.push({
        type: eventType,
        payload,
        timestamp: envelope.timestamp,
      });
      continue;
    }

    if (eventType === "tool.call.input_delta") {
      // Live, non-persisted: partial tool-call input JSON streaming in
      // before the canonical tool.call.started. The first chunk builds a
      // provisional running card so a large file write shows immediate
      // progress instead of a dead wait; later chunks accumulate onto it.
      // started reconciles the card with the canonical full input.
      const id = payload.tool_use_id || "";
      if (!id) continue;
      const text = payload.text ?? "";
      const streaming = activeToolCalls.get(id);
      if (streaming) {
        streaming.input = (streaming.input ?? "") + text;
        continue;
      }
      const title = payload.name || "tool";
      const card: PrototypeToolCall = {
        id,
        kind: resolveToolKind(title.toLowerCase()),
        title,
        // Left empty while input streams — raw partial JSON would look
        // noisy in the always-visible header; started fills in a proper
        // summary and the expandable Input block shows the live content.
        subtitle: "",
        status: "running",
        input: text,
      };
      activeToolCalls.set(id, card);
      turn.blocks.push({
        kind: "tool",
        tool: card,
        elapsedMs: elapsedSince(turn.userTimestamp, envelope.timestamp),
        parentToolUseId: payload.parent_tool_use_id || undefined,
      });
      continue;
    }

    if (eventType === "tool.call.output_delta") {
      // Live, non-persisted: streamed tool output between started and
      // completed. The card already exists; accumulate onto it. completed
      // later replaces it with the canonical aggregated output.
      const id = payload.tool_use_id || "";
      if (!id) continue;
      const streaming = activeToolCalls.get(id);
      if (streaming) {
        streaming.output = (streaming.output ?? "") + (payload.text ?? "");
      }
      continue;
    }

    if (eventType === "tool.call.started") {
      const title = payload.name || payload.tool_name || payload.tool || "tool";
      const id =
        payload.id ||
        payload.call_id ||
        payload.tool_use_id ||
        `${title}-${envelope.seq}`;
      // A preceding tool.call.input_delta may already have built a
      // provisional running card for this id (streaming the partial input).
      const streamed = activeToolCalls.get(id);
      const card: PrototypeToolCall = {
        id,
        kind: resolveToolKind(title.toLowerCase()),
        title,
        subtitle:
          payload.summary ||
          payload.input ||
          payload.arguments ||
          streamed?.subtitle ||
          "",
        status: "running",
        // Canonical full input replaces the partial-JSON preview; fall back
        // to the streamed text if the started event omits the input.
        input: payload.input || payload.arguments || streamed?.input,
      };
      activeToolCalls.set(id, card);
      const startedElapsedMs = elapsedSince(
        turn.userTimestamp,
        envelope.timestamp,
      );
      // Reconcile the provisional block in place when input_delta already
      // pushed one, so started doesn't render a duplicate card.
      const startedIdx = turn.blocks.findIndex(
        (b) => b.kind === "tool" && b.tool.id === id,
      );
      const startedParent =
        payload.parent_tool_use_id ||
        (startedIdx >= 0
          ? (turn.blocks[startedIdx] as ConversationBlock & { kind: "tool" })
              .parentToolUseId
          : undefined) ||
        undefined;
      if (startedIdx >= 0) {
        turn.blocks[startedIdx] = {
          kind: "tool",
          tool: card,
          elapsedMs: startedElapsedMs,
          parentToolUseId: startedParent,
        };
      } else {
        turn.blocks.push({
          kind: "tool",
          tool: card,
          elapsedMs: startedElapsedMs,
          parentToolUseId: startedParent,
        });
      }
      continue;
    }

    if (eventType === "tool.call.completed") {
      const id =
        payload.id ||
        payload.call_id ||
        payload.tool_use_id ||
        `tool-${envelope.seq}`;
      const existing = activeToolCalls.get(id);
      const title =
        existing?.title ||
        payload.name ||
        payload.tool_name ||
        payload.tool ||
        "tool";
      const isError =
        payload.is_error === "True" ||
        payload.is_error === "true" ||
        Boolean(payload.error_message);
      const next: PrototypeToolCall = {
        id,
        kind: resolveToolKind(title.toLowerCase()),
        title,
        subtitle: existing?.subtitle ?? payload.summary ?? "",
        status: isError ? "error" : "success",
        input: existing?.input || payload.input || payload.arguments,
        output:
          payload.content ||
          payload.output ||
          payload.result ||
          payload.error_message,
      };
      const elapsedMs = elapsedSince(turn.userTimestamp, envelope.timestamp);
      const blockIndex = turn.blocks.findIndex(
        (b) => b.kind === "tool" && b.tool.id === id,
      );
      const completedParent =
        payload.parent_tool_use_id ||
        (blockIndex >= 0
          ? (turn.blocks[blockIndex] as ConversationBlock & { kind: "tool" })
              .parentToolUseId
          : undefined) ||
        undefined;
      if (blockIndex >= 0) {
        turn.blocks[blockIndex] = {
          kind: "tool",
          tool: next,
          elapsedMs,
          parentToolUseId: completedParent,
        };
      } else {
        turn.blocks.push({
          kind: "tool",
          tool: next,
          elapsedMs,
          parentToolUseId: completedParent,
        });
      }
      activeToolCalls.delete(id);
      continue;
    }

    if (eventType === "run.failed") {
      const kind = interruptKind(payload.category);
      if (kind === "user") {
        // User cancelled the run — render a quiet grey line, not the
        // ``ErrorMessageCard`` (with retry / switch-model) a real failure gets.
        turn.cancelled = true;
      } else if (kind === "runtime") {
        // Runtime/agent subprocess torn down or crashed mid-turn — same quiet
        // grey line, but a distinct label (NOT "user cancelled").
        turn.interrupted = true;
      } else {
        turn.failedMessage =
          payload.message ??
          t("conversation.runFailed" as Parameters<typeof t>[0]);
      }
    }
  }
  };

  // Trailing meta events (runtime.* with no following user message) attach to
  // the last turn. Two forms: the mutating one bakes them into the persistent
  // ``turns`` (used by the one-shot ``buildTurns``); the pure one returns them
  // as fresh blocks so the incremental snapshot can overlay them WITHOUT
  // mutating fold state (mutating would double-count on the next ``pushAll``).
  const applyTrailingMetaMutating = (): void => {
    if (metaEvents.length && turns.length > 0) {
      const lastTurn = turns[turns.length - 1];
      for (const [i, item] of metaEvents.entries()) {
        const tool = toMetaToolCall(item.type, item.payload, turns.length + i);
        if (tool) {
          const elapsedMs = elapsedSince(lastTurn.userTimestamp, item.timestamp);
          lastTurn.blocks.push({ kind: "tool", tool, elapsedMs });
        }
      }
    }
  };

  const computeTrailingMetaBlocks = (): ConversationBlock[] => {
    const out: ConversationBlock[] = [];
    if (metaEvents.length && turns.length > 0) {
      const lastTurn = turns[turns.length - 1];
      for (const [i, item] of metaEvents.entries()) {
        const tool = toMetaToolCall(item.type, item.payload, turns.length + i);
        if (tool) {
          const elapsedMs = elapsedSince(lastTurn.userTimestamp, item.timestamp);
          out.push({ kind: "tool", tool, elapsedMs });
        }
      }
    }
    return out;
  };

  return {
    turns,
    pushAll,
    applyTrailingMetaMutating,
    computeTrailingMetaBlocks,
  };
};

export const buildTurns = (events: SessionEventDTO[]): ConversationTurn[] => {
  const builder = createTurnsBuilder();
  builder.pushAll(events);
  builder.applyTrailingMetaMutating();
  return builder.turns;
};

/**
 * Incremental transcript builder — the streaming-perf counterpart to
 * ``buildTurns``. ``buildTurns(events)`` re-folds the ENTIRE event array on
 * every call; driven per-token during a long streamed reply that is O(N²) (each
 * token re-walks all prior events and re-concatenates the growing assistant
 * text from scratch), which stalls the main thread and makes deltas arrive in
 * visible bursts. This keeps the fold state alive across calls and, when
 * ``events`` is an append-only extension of what it already processed, folds
 * ONLY the new events, then clones just the growing tail turn(s) so React still
 * sees fresh references for what changed. Non-append changes (window replace,
 * reconcile splice, session switch) transparently fall back to a full rebuild.
 *
 * The returned turns honour the ``useStableTurns`` reference contract directly
 * (stable refs for sealed turns, fresh refs + fresh block/tool refs for the
 * mutated tail), so callers do NOT need to additionally wrap the result.
 */
export interface IncrementalTurns {
  update(events: SessionEventDTO[]): ConversationTurn[];
}

export const createIncrementalTurns = (): IncrementalTurns => {
  let builder = createTurnsBuilder();
  let processed = 0;
  let lastEnvelope: SessionEventDTO | null = null;
  let snapshot: ConversationTurn[] = [];

  const cloneBlock = (b: ConversationBlock): ConversationBlock =>
    b.kind === "tool" ? { ...b, tool: { ...b.tool } } : { ...b };
  const cloneTurn = (t: ConversationTurn): ConversationTurn => ({
    ...t,
    blocks: t.blocks.map(cloneBlock),
  });

  const buildSnapshot = (): ConversationTurn[] => {
    const src = builder.turns;
    // Reuse every turn strictly before the last of the PREVIOUS snapshot: only
    // the last turn (deltas) and — at a turn boundary — the just-sealed
    // second-to-last (meta flush) are ever mutated, so anything below that line
    // is final and its reference can be shared verbatim.
    const reuseBoundary = Math.max(0, snapshot.length - 1);
    const out: ConversationTurn[] = [];
    for (let i = 0; i < src.length; i += 1) {
      out.push(i < reuseBoundary && snapshot[i] ? snapshot[i] : cloneTurn(src[i]));
    }
    // Overlay any pending trailing meta onto the (already fresh-cloned) last
    // turn. Never touches builder state, so the next pushAll won't double-count.
    const trailing = builder.computeTrailingMetaBlocks();
    if (trailing.length > 0 && out.length > 0) {
      const lastIdx = out.length - 1;
      const last =
        lastIdx < reuseBoundary ? cloneTurn(src[lastIdx]) : out[lastIdx];
      out[lastIdx] = {
        ...last,
        blocks: [...last.blocks, ...trailing.map(cloneBlock)],
      };
    }
    snapshot = out;
    return out;
  };

  const update = (events: SessionEventDTO[]): ConversationTurn[] => {
    const appendOnly =
      events.length >= processed &&
      (processed === 0 || events[processed - 1] === lastEnvelope);
    if (!appendOnly) {
      builder = createTurnsBuilder();
      processed = 0;
      snapshot = [];
    }
    if (events.length > processed) {
      builder.pushAll(events.slice(processed));
    }
    processed = events.length;
    lastEnvelope = events.length > 0 ? events[events.length - 1] : null;
    return buildSnapshot();
  };

  return { update };
};
