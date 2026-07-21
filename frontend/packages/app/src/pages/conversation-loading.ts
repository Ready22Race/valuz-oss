/**
 * Loading-state derivation for the conversation view
 * (docs/design/session-stream-lifetime.md §2.1).
 *
 * The composer's Stop button, the streaming logo, and the "已处理 X 秒" timer
 * are all driven by whether a turn is active. On the session-lifetime stream
 * the stream's open/close says NOTHING about turns, so the state is derived
 * from two authoritative inputs:
 *
 * - ``sendPending`` — the optimistic click → turn-start bridge. Set on Send,
 *   released by the turn's ``message.user`` echo / turn-start
 *   ``session.update{running}`` / a genuine terminal frame / a send error.
 *   While pending it OVERRIDES a terminal status: during a slow start
 *   (attachment parse threading) the session legitimately still reads the
 *   pre-turn ``idle`` for seconds — honoring it froze the elapsed timer and
 *   reverted the Stop button (the image-upload regression).
 * - ``status === "running"`` — the reconciled session status, written by the
 *   data-plane ``session.update`` events (the kernel announces ``running`` at
 *   turn start since #590), the optimistic send write, and the turn-boundary
 *   ``refreshActiveSession``. This is what carries busy for turns started by
 *   ANY actor — queue drain, schedule, another client — not just local sends.
 */

/** Session statuses that mean the turn is finished (no loading / no running pill). */
export const TERMINAL_SESSION_STATUSES: ReadonlySet<string> = new Set([
  "idle",
  "failed",
  "cancelled",
  "archived",
  "terminated",
]);

export const isTerminalSessionStatus = (
  status: string | null | undefined,
): boolean => status != null && TERMINAL_SESSION_STATUSES.has(status);

/**
 * Whether the composer should render its loading / Stop state.
 *
 * - ``sendPending`` true → loading, regardless of status (the optimistic
 *   click → turn-start bridge; a stale pre-turn terminal status must not
 *   collapse it — the slow-start hazard above).
 * - status ``running`` → loading (a turn is in flight, whoever started it).
 * - anything else → not loading. A stuck ``sendPending`` cannot pin the state
 *   forever: it is released by the turn's start/terminal events and by send
 *   errors, and the turn-boundary reconciliation converges ``status``.
 */
export const deriveTurnActive = (
  sendPending: boolean,
  status: string | null | undefined,
): boolean => sendPending || status === "running";
