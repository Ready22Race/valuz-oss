/**
 * Loading-state derivation for the conversation view.
 *
 * The composer's Stop button, the streaming logo, and the "已处理 X 秒" timer are
 * all driven by whether the current turn is active. Historically that was a bare
 * local ``sending`` boolean flipped as a side-effect of the SSE subscription
 * lifecycle — set optimistically on Send, released only when a single terminal
 * frame (``session.idle`` / terminal ``session.update`` / ``run.failed``) reached
 * the *currently-live* subscription. A lifecycle race (a re-subscribe whose
 * ``after_seq`` is already past the turn's terminal event, a dropped/deduped
 * frame, or a non-idle ending) could drop that frame, leaving ``sending`` stuck
 * true forever.
 *
 * The fix: keep ``sending`` as the optimistic *start* signal, but GATE the
 * displayed loading state on the authoritative, reconciled session ``status``.
 * The instant the session is terminal, the turn is over — regardless of whether
 * ``sending`` was ever cleared. The subscription streams content; the session
 * status owns "is a turn running".
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
 * - ``sending`` false → never loading.
 * - ``sending`` true + terminal status → NOT loading (the turn is over; this is
 *   what un-sticks a missed terminal frame).
 * - ``sending`` true + running / created / unknown status → loading. An unknown
 *   status (``null`` / ``""`` — a brand-new draft not yet created, or the brief
 *   window before the first status read) is treated as non-terminal so the
 *   optimistic ``sending`` shows through without a flicker at send time.
 */
export const deriveTurnActive = (
  sending: boolean,
  status: string | null | undefined,
): boolean => sending && !isTerminalSessionStatus(status);
