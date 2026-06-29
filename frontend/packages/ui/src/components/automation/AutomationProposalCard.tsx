/**
 * Card rendered in the conversation stream when the assistant calls the
 * ``automation`` tool with ``action: "create"``. Mirrors ``AgentProposalCard``:
 * ``create`` validates + previews but writes nothing — the user decides whether
 * to actually create the automation. The page wires the API call
 * (``automationsApi.confirmProposal``) via ``onConfirm``; Dismiss is client-side
 * only (nothing was persisted). State (pending → confirming → confirmed |
 * dismissed | error) is tracked by the parent so multiple cards behave
 * independently.
 *
 * ``validationError`` covers the case where the ``create`` tool itself rejected
 * the proposal (``ok === false`` — e.g. a bad cron / task-in-chat): the card
 * renders the message and offers no Confirm button.
 */
import { memo } from "react";
import { AlarmClock, Check, Loader2, Sparkles, User, X } from "lucide-react";
import { cn } from "@valuz/ui/lib/utils";
import { useI18n } from "../../hooks/use-i18n";

export type AutomationProposalState =
  | "pending"
  | "confirming"
  | "confirmed"
  | "dismissing"
  | "dismissed"
  | "error";

interface AutomationProposalCardProps {
  name: string;
  promptTemplate?: string;
  /** Localized schedule line ("every day at 9" / "every 5 minutes"). */
  triggerHuman?: string;
  /** Resolved executing agent — shown as the Lead in ``task`` mode. */
  agentName?: string | null;
  actionKind: "chat" | "task";
  state: AutomationProposalState;
  /** When ``state === "error"``, the confirm failure to display. */
  errorMessage?: string;
  /** When the ``create`` tool rejected the proposal (ok === false). */
  validationError?: string | null;
  onConfirm: () => void;
  onDismiss: () => void;
}

export const AutomationProposalCard = memo(function AutomationProposalCard({
  name,
  promptTemplate,
  triggerHuman,
  agentName,
  actionKind,
  state,
  errorMessage,
  validationError,
  onConfirm,
  onDismiss,
}: AutomationProposalCardProps) {
  const { t } = useI18n();

  // The create tool rejected the proposal outright — render a terminal error
  // with no actions (there's nothing to confirm).
  if (validationError) {
    return (
      <div className="rounded-lg border border-error/40 bg-error-light/40 px-4 py-3">
        <div className="flex items-center gap-1.5 text-xs font-medium text-error">
          <AlarmClock className="h-3.5 w-3.5" aria-hidden="true" />
          {t("automation.proposalFailed")}
        </div>
        <p className="mt-1 text-xs leading-snug text-ink-body">{validationError}</p>
      </div>
    );
  }

  const isBusy = state === "confirming" || state === "dismissing";
  const isTerminal = state === "confirmed" || state === "dismissed";
  const canConfirm = state === "pending" && name.trim().length > 0;
  const modeLabel =
    actionKind === "task"
      ? t("automation.actionKindTask")
      : t("automation.actionKindChat");

  return (
    <div
      className={cn(
        "rounded-lg border bg-surface-soft transition-colors",
        state === "confirmed" &&
          "border-[rgba(83,188,118,0.5)] bg-[rgba(83,188,118,0.06)]",
        state === "dismissed" && "border-surface-border bg-surface-2 opacity-80",
        state === "error" && "border-error/40 bg-error-light/40",
        state !== "confirmed" && state !== "dismissed" && state !== "error"
          ? "border-surface-border"
          : "",
      )}
    >
      <div className="flex items-start gap-3 px-4 py-3">
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand">
          {state === "confirmed" ? (
            <Check className="h-4 w-4" />
          ) : state === "dismissed" ? (
            <X className="h-4 w-4 text-ink-muted" />
          ) : (
            <AlarmClock className="h-4 w-4" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="truncate text-sm font-medium text-ink-heading">
              {name || t("automation.proposalUnnamed")}
            </span>
            <span className="shrink-0 text-2xs uppercase tracking-wider text-ink-label">
              {t("automation.proposalNew")}
            </span>
            <span className="shrink-0 rounded-full bg-surface-muted px-1.5 text-2xs font-medium text-ink-label">
              {modeLabel}
            </span>
          </div>

          {triggerHuman ? (
            <p className="mt-1 flex items-center gap-1 text-xs text-ink-body">
              <Sparkles className="h-3 w-3 shrink-0 text-ink-label" />
              {triggerHuman}
            </p>
          ) : null}

          {agentName ? (
            <p className="mt-1 flex items-center gap-1 text-[11px] text-ink-meta">
              <User className="h-3 w-3 shrink-0 text-ink-label" />
              {actionKind === "task"
                ? `${t("automation.proposalLead")}: ${agentName}`
                : `${t("automation.agentLabel")}: ${agentName}`}
            </p>
          ) : null}

          {promptTemplate ? (
            <p className="mt-2 line-clamp-3 text-xs leading-snug text-ink-body">
              {promptTemplate}
            </p>
          ) : null}

          {state === "confirmed" ? (
            <p className="mt-2 text-xs text-ink-body">
              {t("automation.proposalCreated")}
            </p>
          ) : null}
          {state === "dismissed" ? (
            <p className="mt-2 text-xs text-ink-meta">
              {t("automation.proposalDismissed")}
            </p>
          ) : null}
          {state === "error" && errorMessage ? (
            <p className="mt-2 text-xs text-error">
              {t("skill.operationFailed", { error: errorMessage })}
            </p>
          ) : null}
        </div>
      </div>

      {!isTerminal ? (
        <div className="flex items-center justify-end gap-2 border-t border-surface-border px-4 py-2">
          <button
            type="button"
            disabled={isBusy}
            onClick={onDismiss}
            className={cn(
              "inline-flex h-7 items-center rounded-md px-3 text-xs font-medium",
              "border border-surface-border text-ink-body hover:bg-surface-2",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {state === "dismissing" ? (
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
            ) : null}
            {t("common.cancel")}
          </button>
          <button
            type="button"
            disabled={!canConfirm || isBusy}
            onClick={onConfirm}
            className={cn(
              "inline-flex h-7 items-center rounded-md px-3 text-xs font-medium",
              "bg-brand text-white hover:bg-brand/90",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {state === "confirming" ? (
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
            ) : null}
            {t("automation.actionCreate")}
          </button>
        </div>
      ) : null}
    </div>
  );
});
