/* ── Tool call types ──────────────────────────────────────── */

export type PrototypeToolCallStatus =
  "success" | "running" | "cached" | "error";

export type PrototypeToolCallKind = "kb" | "fetch" | "skill" | "file" | "bash";

export interface PrototypeToolCall {
  id: string;
  kind: PrototypeToolCallKind;
  title: string;
  subtitle?: string;
  status: PrototypeToolCallStatus;
  input?: string;
  output?: string;
}

/* ── Background tasks (run_in_background shell commands) ──── */

export type BackgroundTaskStatus = "running" | "completed" | "failed" | "stopped";

/**
 * One background task the agent launched (``run_in_background`` Bash).
 * Derived by folding the persisted ``session.bg_task.*`` events, so it is
 * correct on live streams AND on history replay: a task that started in an
 * earlier page visit and is still running shows as ``running`` after
 * re-entering the conversation.
 */
export interface BackgroundTaskState {
  taskId: string;
  /** tool_use_id of the Bash call that spawned it — lets a card attach to
   *  the matching tool block if needed. */
  toolUseId?: string;
  description: string;
  status: BackgroundTaskStatus;
  /** Human summary from the terminal event (e.g. exit-code line). */
  summary?: string;
  /** Path of the file the task's stdout/stderr is streamed to. */
  outputFile?: string;
  /** Unix epoch ms of the started event, when the wire carried one. */
  startedAtMs?: number;
}

/* ── Conversation turn types ─────────────────────────────── */

export type ConversationBlock =
  | { kind: "assistant"; text: string; messageId?: string; sealed?: boolean }
  | {
      kind: "thinking";
      text: string;
      messageId?: string;
      sealed?: boolean;
      elapsedMs?: number;
    }
  | { kind: "tool"; tool: PrototypeToolCall; elapsedMs?: number }
  // Context-compaction marker (``/compact`` or autocompact), for either
  // runtime. Label-only — the kernel ``compaction`` event's raw data is
  // intentionally not parsed for display; it just marks where the context
  // window was summarized within the turn.
  | { kind: "compaction"; messageId?: string };

export interface ConversationTurnAttachment {
  name: string;
  size: number;
}

export interface ConversationTurn {
  id: string;
  /** Seq of the user_message event this turn was built from. ``0`` for
   * live broadcast frames not yet persisted to the events DB; the
   * persisted copy arrives later with a real seq. The dedup logic in
   * ``effectiveTurns`` uses this directly instead of parsing the id,
   * which now uses ``message_id`` (a UUID) for stability across the
   * live → persisted transition. */
  userMessageSeq: number;
  userText: string;
  blocks: ConversationBlock[];
  failedMessage: string | null;
  /** The run ended because the user cancelled it (``run.failed`` with
   *  ``category === "user_interrupt"``). Rendered as a quiet grey line rather
   *  than the ``ErrorMessageCard`` a genuine failure gets. Optional: absent is
   *  treated as ``false``. */
  cancelled?: boolean;
  /** The run ended because the runtime/agent subprocess was torn down or
   *  crashed mid-turn (``category === "interrupted"`` — NOT a user action).
   *  Rendered as the same quiet grey line as ``cancelled`` but with a distinct
   *  label so a system interruption is not misattributed to the user ("用户取消
   *  了当前对话"). Optional: absent is treated as ``false``. */
  interrupted?: boolean;
  attachments?: ConversationTurnAttachment[];
  /** Unix epoch milliseconds (UTC). */
  userTimestamp?: number;
  /** Unix epoch milliseconds (UTC) of the last event currently associated
   * with this turn. Used as the fallback "finish time" when computing the
   * ``已处理 X 秒`` header for turns that have no thinking/tool work to
   * derive elapsedMs from (e.g. a direct one-shot assistant reply). */
  endTimestamp?: number;
}
