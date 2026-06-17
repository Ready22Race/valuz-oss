/**
 * Unified run / session / task status → color tone. One color per semantic,
 * shared by the Activity pills, the sidebar dots, and the project/task status
 * tags. See docs/desktop/status-colors.md.
 */

type Tone =
  | "brand"
  | "success"
  | "error"
  | "warning"
  | "teal"
  | "draft"
  | "neutral";

const TONE: Record<string, Tone> = {
  // active / about-to-run → brand (the pulse, running/active only, signals live)
  running: "brand",
  active: "brand",
  created: "brand",
  completed: "success",
  failed: "error",
  // blocked & paused share warning; distinguished by their label text
  blocked: "warning",
  paused: "warning",
  // a chat that finished a round normally (resumable) — teal: distinct from the
  // gray of terminated runs, the emerald of completed, and the brand purple of
  // running
  idle: "teal",
  // ``draft`` — task saved but not yet kicked off. Slate-blue cool grey
  // reads as "in preparation", distinct from neutral (terminated) and
  // brand (live).
  draft: "draft",
  stopped: "neutral",
  cancelled: "neutral",
  archived: "neutral",
};

const PILL_CLASS: Record<Tone, string> = {
  brand: "bg-info-light text-info-text",
  success: "bg-success-light text-success-text",
  error: "bg-error-light text-error-text",
  warning: "bg-warning-light text-warning-text",
  teal: "bg-[color-mix(in_oklab,var(--accent-teal)_14%,var(--background))] text-[color-mix(in_oklab,var(--accent-teal)_62%,var(--foreground))]",
  draft: "bg-surface-2 text-ink-body",
  neutral: "bg-surface-muted text-ink-meta",
};

const DOT_CLASS: Record<Tone, string> = {
  brand: "bg-brand",
  success: "bg-success",
  error: "bg-error",
  warning: "bg-warning",
  teal: "bg-accent-teal",
  draft: "bg-ink-meta",
  neutral: "bg-ink-meta",
};

const PULSE = new Set(["running", "active"]);

export const statusTone = (status: string): Tone => TONE[status] ?? "neutral";
export const statusPillClass = (status: string): string =>
  PILL_CLASS[statusTone(status)];
export const statusDotClass = (status: string): string =>
  DOT_CLASS[statusTone(status)];
export const statusPulses = (status: string): boolean => PULSE.has(status);
