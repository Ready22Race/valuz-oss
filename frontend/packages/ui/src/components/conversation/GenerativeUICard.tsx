import { useEffect, useRef, useState, type ComponentProps } from "react";
import { Renderer } from "@openuidev/react-lang";
import { ThemeProvider } from "@openuidev/react-ui";
import { createValuzLibrary } from "@valuz/genui-blocks";
import { Maximize2 } from "lucide-react";

import { useI18n } from "../../hooks/use-i18n";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Spinner } from "../ui/spinner";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";

type OpenUiTheme = NonNullable<
  ComponentProps<typeof ThemeProvider>["lightTheme"]
>;

const chartPalette = [
  "var(--accent-sky)",
  "var(--accent-teal)",
  "var(--accent-amber)",
  "var(--accent-pink)",
  "var(--accent-blue)",
  "var(--accent-lime)",
  "var(--accent-orange)",
  "var(--accent-fuchsia)",
];

/** Maps OpenUI directly onto the authoritative Valuz design tokens. */
const VALUZ_OPENUUI_THEME: OpenUiTheme = {
  background: "var(--color-background)",
  foreground: "var(--color-surface)",
  popoverBackground: "var(--color-surface)",
  sunkLight: "var(--color-surface-soft)",
  sunk: "var(--color-surface)",
  sunkDeep: "var(--color-surface-muted)",
  elevatedLight: "var(--color-surface-soft)",
  elevated: "var(--color-surface)",
  elevatedStrong: "var(--color-surface)",
  elevatedIntense: "var(--color-surface)",
  highlightSubtle: "var(--color-surface-soft)",
  highlight: "var(--color-surface-2)",
  highlightStrong: "var(--color-surface-muted)",
  highlightIntense: "var(--color-surface-border)",
  infoBackground: "var(--info-soft)",
  successBackground: "var(--success-soft)",
  alertBackground: "var(--warning-soft)",
  dangerBackground: "var(--error-soft)",

  textNeutralPrimary: "var(--color-ink-heading)",
  textNeutralSecondary: "var(--color-ink-body)",
  textNeutralTertiary: "var(--color-ink-disabled)",
  textNeutralLink: "var(--color-brand)",
  textBrand: "var(--color-brand)",
  textAccentPrimary: "white",
  textAccentSecondary: "var(--color-brand-700)",
  textAccentTertiary: "var(--color-brand)",
  textSuccessPrimary: "var(--success-text)",
  textSuccessInverted: "white",
  textAlertPrimary: "var(--warning-text)",
  textAlertInverted: "var(--foreground)",
  textDangerPrimary: "var(--error-text)",
  textDangerSecondary: "var(--error-text)",
  textDangerTertiary: "var(--color-ink-disabled)",
  textDangerInvertedPrimary: "white",
  textInfoPrimary: "var(--info-text)",
  textInfoInverted: "white",

  interactiveAccentDefault: "var(--color-brand)",
  interactiveAccentHover: "var(--color-brand-hover)",
  interactiveAccentPressed: "var(--color-brand-700)",
  interactiveAccentDisabled:
    "color-mix(in oklab, var(--color-brand) 40%, transparent)",
  interactiveDestructiveDefault: "var(--error-soft)",
  interactiveDestructiveHover: "var(--error-border)",
  interactiveDestructiveDisabled: "var(--color-surface-2)",
  interactiveDestructivePressed: "var(--error-border)",
  interactiveDestructiveAccentDefault: "var(--error-strong)",
  interactiveDestructiveAccentHover: "var(--error-hover)",
  interactiveDestructiveAccentPressed: "var(--error-hover)",
  interactiveDestructiveAccentDisabled:
    "color-mix(in oklab, var(--error-strong) 40%, transparent)",

  borderDefault: "var(--color-surface-border)",
  borderInteractive: "var(--color-surface-border-strong)",
  borderInteractiveEmphasis: "var(--color-surface-border-strong)",
  borderInteractiveSelected: "var(--color-brand)",
  borderAccent: "var(--color-brand)",
  borderAccentEmphasis: "var(--color-brand-600)",
  borderAccentSelected: "var(--color-brand-700)",
  borderInfo: "var(--info-border)",
  borderInfoEmphasis: "var(--color-brand)",
  borderAlert: "var(--warning-border)",
  borderAlertEmphasis: "var(--warning)",
  borderSuccess: "var(--success-border)",
  borderSuccessEmphasis: "var(--success)",
  borderDanger: "var(--error-border)",
  borderDangerEmphasis: "var(--error)",

  space000: "0px",
  space3xs: "4px",
  space2xs: "4px",
  spaceXs: "8px",
  spaceS: "8px",
  spaceSM: "12px",
  spaceM: "12px",
  spaceML: "16px",
  spaceL: "16px",
  spaceXl: "20px",
  space2xl: "24px",
  space3xl: "32px",
  radiusNone: "0px",
  radius3xs: "4px",
  radius2xs: "4px",
  radiusXs: "4px",
  radiusS: "4px",
  radiusM: "6px",
  radiusL: "8px",
  radiusXl: "10px",
  radius2xl: "12px",
  radius3xl: "12px",
  radius4xl: "12px",
  radius5xl: "12px",
  radius6xl: "12px",
  radius7xl: "12px",
  radius8xl: "12px",
  radius9xl: "12px",
  radiusFull: "9999px",

  fontBody:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontHeading:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontLabel:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontNumbers:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontCode: 'ui-monospace, "SF Mono", Menlo, monospace',
  fontSize2xs: "10px",
  fontSizeXs: "11px",
  fontSizeSm: "12px",
  fontSizeMd: "13px",
  fontSizeLg: "14px",
  fontSizeXl: "15px",
  fontSize2xl: "18px",
  fontSize3xl: "24px",
  fontSize4xl: "24px",
  fontSize5xl: "24px",
  fontWeightRegular: "400",
  fontWeightMedium: "500",
  fontWeightBold: "600",
  fontWeightHeavy: "600",
  letterSpacingNormal: "0",
  letterSpacingTight: "0",
  letterSpacingTighter: "0",

  shadow0: "none",
  shadowS: "var(--shadow-outline)",
  // OpenUI's card/popover primitives already draw a border. Valuz requires
  // bordered surfaces to use the ring-free outline shadow, avoiding a double edge.
  shadowM: "var(--shadow-outline)",
  shadowL: "var(--shadow-2)",
  shadowXl: "var(--shadow-3)",
  shadow2xl: "var(--shadow-4)",
  shadow3xl: "var(--shadow-4)",

  defaultChartPalette: chartPalette,
  barChartPalette: chartPalette,
  lineChartPalette: chartPalette,
  areaChartPalette: chartPalette,
  pieChartPalette: chartPalette,
  radarChartPalette: chartPalette,
  radialChartPalette: chartPalette,
  horizontalBarChartPalette: chartPalette,
};

/**
 * Extract the raw text payload from a kernel tool-output string.
 *
 * MCP tool results surface on the frontend wrapped in a JSON content-block
 * envelope — ``[{"type":"text","text":"<payload>"}]`` — because the host
 * toolkit MCP server returns ``TextContent`` and the kernel JSON-stringifies
 * the content blocks at the SSE boundary (``event_sse_adapter._stringify``).
 * Some runtimes also emit a Python-repr variant (``[{'type': 'text', ...}]``).
 * The OpenUI ``<Renderer>`` needs the inner text (the OpenUI Lang), not the
 * envelope, so unwrap both; fall through to the raw string when there's none.
 */
export function extractContentText(raw: string | undefined | null): string {
  const s = (raw ?? "").trim();
  if (!s) return "";

  // 1. JSON envelope (the common path).
  try {
    const parsed: unknown = JSON.parse(s);
    const text = readTextBlocks(parsed);
    if (text !== null) return text;
  } catch {
    /* not JSON — try repr / fall through */
  }

  // 2. Python-repr envelope from other runtimes: {'type': 'text', 'text': '…'}.
  const repr = matchReprText(s);
  if (repr !== null) return repr;

  // 3. No envelope — already raw text (OpenUI Lang passed through directly).
  return s;
}

function readTextBlocks(parsed: unknown): string | null {
  const entries = Array.isArray(parsed) ? parsed : [parsed];
  const texts: string[] = [];
  for (const e of entries) {
    if (
      e &&
      typeof e === "object" &&
      typeof (e as Record<string, unknown>).text === "string"
    ) {
      texts.push((e as Record<string, string>).text);
    }
  }
  if (texts.length) return texts.join("");
  if (typeof parsed === "string") return parsed; // double-stringified
  return null;
}

function matchReprText(s: string): string | null {
  // Match  'text': '…'  tolerating escaped quotes inside the value.
  const m = s.match(/'text'\s*:\s*'((?:[^'\\]|\\.)*)'/);
  if (!m || !m[1]) return null;
  return m[1]
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "\t")
    .replace(/\\'/g, "'")
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, "\\");
}

export interface GenerativeUICardProps {
  /** OpenUI Lang string — the generate_ui tool's output. */
  openui?: string;
  /** Tool status; "running" while the tool hasn't returned yet. */
  status?: "running" | "success" | "error";
  /** Reasoning stream (``tool.call.thinking_delta``, live-only) from the
   * ephemeral generation session. Shown dimmed while running so the model's
   * thinking phase is visible progress instead of a silent wait; dropped
   * from the DOM once the tool completes (it never persists to history). */
  thinking?: string;
}

/**
 * OpenUI's own components plus the Valuz blocks, as one library.
 *
 * Built once at module scope: `createValuzLibrary()` walks and re-registers
 * every component, and the result is immutable, so rebuilding it per render
 * would be pure waste. The merge is additive — no block shadows an OpenUI
 * component (a test in `@valuz/genui-blocks` enforces that), so anything the
 * model could emit before it still emits now.
 */
const GENERATIVE_UI_LIBRARY = createValuzLibrary();

const OPENUI_SCOPE_SELECTOR = '[data-openui-scope="generative-ui"]';

const GENERATIVE_UI_LAYOUT_CSS = `
  ${OPENUI_SCOPE_SELECTOR} {
    min-width: 0;
    max-width: 100%;
    container-type: inline-size;
    container-name: genui-inline;
  }

  ${OPENUI_SCOPE_SELECTOR} * {
    box-sizing: border-box;
    min-width: 0;
  }

  ${OPENUI_SCOPE_SELECTOR} :where([class^="openui-"], [class*=" openui-"]) {
    max-width: 100%;
  }

  /* OpenUI rows are inline-style flex containers. Size peer cards from their
     content so compact KPIs can share a row while dense modules naturally take
     more room. flex-grow distributes any remaining space without forcing every
     module to start from the same fixed width. */
  ${OPENUI_SCOPE_SELECTOR} .openui-card {
    flex-basis: max-content !important;
  }

  ${OPENUI_SCOPE_SELECTOR} .openui-card-card,
  ${OPENUI_SCOPE_SELECTOR} .openui-card-clear {
    border-color: transparent;
    background: transparent;
    box-shadow: none;
  }

  ${OPENUI_SCOPE_SELECTOR} .openui-card-sunk {
    border-color: transparent;
    background: var(--color-surface-soft);
    box-shadow: none;
  }

  /* A compact row of three or more cards is the KPI strip. Keep headings and
     larger content sections unframed, and give only these metrics a soft tile. */
  ${OPENUI_SCOPE_SELECTOR}
    :has(> .openui-card:nth-child(3)) > .openui-card {
    flex: 1 1 15rem !important;
    border-color: transparent;
    background: var(--color-surface-soft);
    border-radius: 8px;
    padding: var(--openui-space-l);
  }

  /* Older generated dashboards sometimes express KPI tiles as anonymous Stack
     children instead of Cards. Match their title/value/tag signature so saved
     conversations receive the same stable surface without changing their data. */
  ${OPENUI_SCOPE_SELECTOR}
    :has(> :nth-child(3))
    > :not([class]):has(> .openui-tag):has(> :nth-child(2) .openui-markdown-renderer) {
    flex: 1 1 15rem;
    background: var(--color-surface-soft);
    border-radius: 8px;
    padding: var(--openui-space-l);
  }

  ${OPENUI_SCOPE_SELECTOR}
    :has(> .openui-card:nth-child(3)) > .openui-card .openui-tag,
  ${OPENUI_SCOPE_SELECTOR}
    :has(> :nth-child(3))
    > :not([class]):has(> .openui-tag):has(> :nth-child(2) .openui-markdown-renderer)
    > .openui-tag {
    min-height: 0;
    width: fit-content;
    padding: 0;
    gap: 4px;
    border: 0;
    border-radius: 0;
    background: transparent;
  }

  ${OPENUI_SCOPE_SELECTOR}
    :has(> .openui-card:nth-child(3)) > .openui-card
    .openui-tag-success,
  ${OPENUI_SCOPE_SELECTOR}
    :has(> .openui-card:nth-child(3)) > .openui-card
    .openui-tag-success .openui-tag-text {
    color: var(--error-text);
  }

  ${OPENUI_SCOPE_SELECTOR}
    :has(> :nth-child(3))
    > :not([class]):has(> .openui-tag):has(> :nth-child(2) .openui-markdown-renderer)
    > .openui-tag-success,
  ${OPENUI_SCOPE_SELECTOR}
    :has(> :nth-child(3))
    > :not([class]):has(> .openui-tag):has(> :nth-child(2) .openui-markdown-renderer)
    > .openui-tag-success .openui-tag-text {
    color: var(--error-text);
  }

  ${OPENUI_SCOPE_SELECTOR}
    :has(> .openui-card:nth-child(3)) > .openui-card
    .openui-tag-danger,
  ${OPENUI_SCOPE_SELECTOR}
    :has(> .openui-card:nth-child(3)) > .openui-card
    .openui-tag-danger .openui-tag-text {
    color: var(--success-text);
  }

  ${OPENUI_SCOPE_SELECTOR}
    :has(> :nth-child(3))
    > :not([class]):has(> .openui-tag):has(> :nth-child(2) .openui-markdown-renderer)
    > .openui-tag-danger,
  ${OPENUI_SCOPE_SELECTOR}
    :has(> :nth-child(3))
    > :not([class]):has(> .openui-tag):has(> :nth-child(2) .openui-markdown-renderer)
    > .openui-tag-danger .openui-tag-text {
    color: var(--success-text);
  }

  /* Dashboard tables behave like report sections: column labels and row rules
     provide structure without adding another rounded container. */
  ${OPENUI_SCOPE_SELECTOR} .openui-table-container {
    width: 100%;
    border: 0;
    border-radius: 0;
  }

  ${OPENUI_SCOPE_SELECTOR} :has(> .openui-scrollable-table-wrapper),
  ${OPENUI_SCOPE_SELECTOR} :has(> .openui-table-container),
  ${OPENUI_SCOPE_SELECTOR} .openui-scrollable-table-wrapper {
    width: 100%;
    flex: 1 1 100%;
  }

  ${OPENUI_SCOPE_SELECTOR} .openui-table {
    width: max-content;
    min-width: 100%;
  }

  ${OPENUI_SCOPE_SELECTOR} .openui-table-row:nth-child(even) {
    background: transparent;
  }

  ${OPENUI_SCOPE_SELECTOR} :where(.openui-table-head, .openui-table-cell) {
    padding-inline: var(--openui-space-s, 8px);
    white-space: nowrap;
  }

  ${OPENUI_SCOPE_SELECTOR} :where(
    .openui-table-head:first-child,
    .openui-table-cell:first-child
  ) {
    padding-left: 0;
  }

  ${OPENUI_SCOPE_SELECTOR} :where(
    .openui-table-head:last-child,
    .openui-table-cell:last-child
  ) {
    padding-right: 0;
  }

  ${OPENUI_SCOPE_SELECTOR} :where(p, span, div, td, th) {
    overflow-wrap: anywhere;
  }

  /* OpenUI chart roots sit inside anonymous flex wrappers that otherwise shrink
     to their intrinsic plot width. Cover every chart component exposed by the
     library and let Cartesian plots consume the space left after the Y axis. */
  ${OPENUI_SCOPE_SELECTOR} :has(> :where(
    .openui-bar-chart-container,
    .openui-bar-chart-condensed-container,
    .openui-line-chart-container,
    .openui-line-chart-condensed-container,
    .openui-area-chart-container,
    .openui-area-chart-condensed-container,
    .openui-horizontal-bar-chart-container,
    .openui-scatter-chart-container,
    .openui-radar-chart-container-wrapper,
    .openui-pie-chart-container-wrapper,
    .openui-radial-chart-container-wrapper,
    .openui-single-stacked-bar-chart-container
  )) {
    width: 100%;
    flex: 1 1 100%;
  }

  ${OPENUI_SCOPE_SELECTOR} :where(
    .openui-bar-chart-container,
    .openui-bar-chart-condensed-container,
    .openui-line-chart-container,
    .openui-line-chart-condensed-container,
    .openui-area-chart-container,
    .openui-area-chart-condensed-container,
    .openui-horizontal-bar-chart-container,
    .openui-scatter-chart-container,
    .openui-radar-chart-container-wrapper,
    .openui-pie-chart-container-wrapper,
    .openui-radial-chart-container-wrapper,
    .openui-single-stacked-bar-chart-container,
    [class$="-chart-condensed-container-inner"]
  ) {
    width: 100% !important;
    flex: 1 1 100% !important;
  }

  ${OPENUI_SCOPE_SELECTOR} :where(
    .openui-bar-chart-condensed,
    .openui-line-chart-condensed,
    .openui-area-chart-condensed,
    .openui-chart-container,
    .recharts-responsive-container
  ) {
    width: 100% !important;
    flex: 1 1 0 !important;
  }

  ${OPENUI_SCOPE_SELECTOR}
    .openui-horizontal-bar-chart-container-inner-wrapper {
    height: auto !important;
    overflow: visible;
  }

  ${OPENUI_SCOPE_SELECTOR}
    .openui-horizontal-bar-chart-main-container {
    height: auto;
    overflow-y: visible;
  }

  @container genui-inline (max-width: 48rem) {
    ${OPENUI_SCOPE_SELECTOR} .openui-card {
      flex-basis: min(100%, 18rem) !important;
    }

    ${OPENUI_SCOPE_SELECTOR}
      :has(> .openui-card:nth-child(3)) > .openui-card,
    ${OPENUI_SCOPE_SELECTOR}
      :has(> :nth-child(3))
      > :not([class]):has(> .openui-tag):has(> :nth-child(2) .openui-markdown-renderer) {
      flex-basis: min(100%, 14rem) !important;
    }
  }

  @container genui-inline (max-width: 34rem) {
    ${OPENUI_SCOPE_SELECTOR} .openui-card {
      flex-basis: 100% !important;
    }

    ${OPENUI_SCOPE_SELECTOR}
      :has(> .openui-card:nth-child(3)) > .openui-card,
    ${OPENUI_SCOPE_SELECTOR}
      :has(> :nth-child(3))
      > :not([class]):has(> .openui-tag):has(> :nth-child(2) .openui-markdown-renderer) {
      flex-basis: 100% !important;
    }

    ${OPENUI_SCOPE_SELECTOR} .openui-card-clear {
      padding-inline: 0;
    }
  }
`;

function OpenUiBody({
  body,
  status,
}: {
  body: string;
  status?: GenerativeUICardProps["status"];
}) {
  return (
    <ThemeProvider
      lightTheme={VALUZ_OPENUUI_THEME}
      cssSelector={OPENUI_SCOPE_SELECTOR}
    >
      <Renderer
        library={GENERATIVE_UI_LIBRARY}
        response={body}
        isStreaming={status === "running"}
      />
    </ThemeProvider>
  );
}

/**
 * Renders the OpenUI Lang produced by the ``generate_ui`` MCP tool as live,
 * interactive components. Mounted inline via ``ConversationPage``'s
 * ``renderToolCall`` override (the same lift-out seam AskUserQuestion and
 * submit_skill use).
 */
export function GenerativeUICard({
  openui,
  status,
  thinking,
}: GenerativeUICardProps) {
  const { t } = useI18n();
  const [fullscreenOpen, setFullscreenOpen] = useState(false);
  const body = extractContentText(openui);
  const cardTitle = t("genui.cardTitle" as Parameters<typeof t>[0]);
  const fullscreenLabel = t("genui.fullscreen" as Parameters<typeof t>[0]);
  // Reasoning is transient live progress: visible only while the tool runs
  // (after completion the rendered UI is the payload; the stream is
  // live-only and gone on history replay anyway).
  const showThinking = status === "running" && Boolean(thinking);
  const thinkingRef = useRef<HTMLDivElement | null>(null);

  // Follow the tail of the reasoning stream as it grows.
  useEffect(() => {
    const el = thinkingRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thinking]);

  return (
    <div
      data-slot="generative-ui-card"
      data-openui-scope="generative-ui"
      className="rounded-xl border border-surface-border bg-surface overflow-hidden"
    >
      <style>{GENERATIVE_UI_LAYOUT_CSS}</style>
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-surface-border">
        <span className="min-w-0 truncate text-sm font-medium text-ink-heading">
          {cardTitle}
        </span>
        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label={fullscreenLabel}
                title={fullscreenLabel}
                disabled={!body}
                onClick={() => setFullscreenOpen(true)}
                className="shrink-0 text-ink-muted hover:text-ink-heading"
              >
                <Maximize2 className="size-3.5" aria-hidden="true" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left">{fullscreenLabel}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
      {showThinking ? (
        <div className="px-3 py-2 border-b border-surface-border bg-surface-soft">
          <div className="flex items-center gap-2 text-xs text-ink-meta">
            <Spinner className="size-3" />
            {t("conversation.thinking" as Parameters<typeof t>[0])}
          </div>
          <div
            ref={thinkingRef}
            data-testid="genui-thinking"
            className="mt-1 max-h-24 overflow-y-auto whitespace-pre-wrap text-xs italic text-ink-meta"
          >
            {thinking}
          </div>
        </div>
      ) : null}
      {body || !showThinking ? (
        <div className="min-w-0 overflow-x-auto p-3 [&>*]:min-w-0 [&>*]:max-w-full">
          {body ? (
            <OpenUiBody body={body} status={status} />
          ) : (
            <div
              data-testid="genui-empty"
              className="flex items-center gap-2 text-sm text-ink-meta"
            >
              {status === "running" ? (
                <>
                  <Spinner className="size-3.5" />
                  {t("genui.generating" as Parameters<typeof t>[0])}
                </>
              ) : (
                t("genui.empty" as Parameters<typeof t>[0])
              )}
            </div>
          )}
        </div>
      ) : null}
      <Dialog open={fullscreenOpen} onOpenChange={setFullscreenOpen}>
        <DialogContent className="top-9 right-4 bottom-4 left-4 h-auto max-h-none w-auto max-w-none translate-x-0 translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-none">
          <DialogHeader className="border-b border-surface-border px-4 py-3 pr-12">
            <DialogTitle className="text-sm leading-5">{cardTitle}</DialogTitle>
            <DialogDescription className="sr-only">
              {t("genui.fullscreenDescription" as Parameters<typeof t>[0])}
            </DialogDescription>
          </DialogHeader>
          <div
            data-testid="genui-fullscreen"
            data-slot="generative-ui-fullscreen"
            data-openui-scope="generative-ui"
            className="min-h-0 flex-1 overflow-auto p-4 [&>*]:min-w-0 [&>*]:max-w-full"
          >
            {body ? <OpenUiBody body={body} status={status} /> : null}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
