import { useEffect, useRef, useState } from "react";
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
import { GenerativeUIRenderer } from "./GenerativeUIRenderer";
import { parseGenerativeUIPayload } from "./generative-ui-payload";

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

  ${OPENUI_SCOPE_SELECTOR} [data-slot="a2ui-renderer"] {
    min-width: 0;
    max-width: 100%;
  }

  ${OPENUI_SCOPE_SELECTOR} [data-a2ui-component="grid"] {
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 14rem), 1fr));
  }

  ${OPENUI_SCOPE_SELECTOR} [data-a2ui-component="row"] > * {
    flex: 1 1 14rem;
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

    ${OPENUI_SCOPE_SELECTOR} [data-a2ui-component="grid"] {
      grid-template-columns: 1fr;
    }

    ${OPENUI_SCOPE_SELECTOR} [data-a2ui-component="row"] > * {
      flex-basis: 100%;
    }
  }
`;

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
  const payload = parseGenerativeUIPayload(openui);
  const body = payload.body;
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
            <GenerativeUIRenderer payload={payload} status={status} />
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
            {body ? (
              <GenerativeUIRenderer payload={payload} status={status} />
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
