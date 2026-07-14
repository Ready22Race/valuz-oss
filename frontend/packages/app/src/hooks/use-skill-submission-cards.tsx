/**
 * Renders the ``submit_skill`` tool call as the interactive
 * ``SkillSubmissionCard`` (skill-creator proposal → save / dismiss), matching
 * the main conversation view — for surfaces that don't own ConversationPage's
 * full tool-card machinery (the task-detail Lead follow-up chat).
 *
 * Self-contained, mirroring ``useAskUserQuestionCards``: it owns the per-tool
 * submission state, polls the session's staging dir so the card shows the real
 * staged file tree (and gates its save button on actual file presence), and
 * exposes a single ``renderToolCall`` to compose into the turn list. The lead
 * follow-up can call a skill-creator during a post-completion tweak, so the
 * card has to work there too — not just in ConversationPage.
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { skillsApi, useTranslation } from "@valuz/core";
import type { ConversationTurn } from "@valuz/shared";
import { SkillSubmissionCard, type SkillSubmissionState } from "@valuz/ui";
import { toast } from "sonner";

type ToolLike = {
  id: string;
  title: string;
  input?: string;
  output?: string;
};

/** Live per-``submit_skill`` tool_use state, keyed by tool_use id so multiple
 *  submissions in one conversation render independently. */
interface SubmissionEntry {
  state: SkillSubmissionState;
  errorMessage?: string;
  boundToProjectLabel?: string | null;
  stagedFiles?: { path: string; type: "file" | "directory"; size?: number | null }[];
  stagingPath?: string;
}

/** Match every MCP namespacing the runtimes produce for ``submit_skill``:
 *  bare, Claude-style (``mcp__harness__submit_skill``), or slash-style
 *  (``harness/submit_skill`` — the codex runtime). Mirrors ConversationPage's
 *  ``isToolNamed`` without depending on that page. */
function isSubmitSkillName(name: string | undefined): boolean {
  return (
    name === "submit_skill" ||
    (typeof name === "string" &&
      (name.endsWith("__submit_skill") || name.endsWith("/submit_skill")))
  );
}

/** Parse a ``submit_skill`` tool input into the fields the card + confirm flow
 *  need. Tolerant of malformed args (card renders with placeholder slug). */
function parseSubmitInput(input?: string): {
  slug: string;
  summary?: string;
  changeKind: "create" | "update";
  filesTouched: string[];
} {
  let parsed: {
    slug?: string;
    summary?: string;
    change_kind?: "create" | "update";
    files_touched?: string[];
  } = {};
  if (input) {
    try {
      parsed = typeof input === "string" ? JSON.parse(input) : input;
    } catch {
      /* malformed — fall through to defaults */
    }
  }
  return {
    slug: parsed.slug || "(unknown-slug)",
    summary: parsed.summary,
    changeKind: parsed.change_kind === "update" ? "update" : "create",
    filesTouched: Array.isArray(parsed.files_touched) ? parsed.files_touched : [],
  };
}

export function useSkillSubmissionCards({
  sessionId,
  turns,
  sending,
  projectLabel,
}: {
  sessionId: string | null;
  turns: ConversationTurn[];
  /** Poll the staging dir faster while a turn is streaming. */
  sending: boolean;
  /** Human-readable project label, shown as "已绑定到 X" on a project-bound
   *  save. Omit for non-project surfaces. */
  projectLabel?: string | null;
}): { renderToolCall: (tool: ToolLike) => ReactNode | null } {
  const { t } = useTranslation();
  const [submissionStates, setSubmissionStates] = useState<
    Record<string, SubmissionEntry>
  >({});

  // Scan staging for every ``submit_skill`` tool_use we've seen, so the card
  // renders the real file tree (not just the agent's ``files_touched`` claim)
  // and gates save on actual file presence. While the agent is still
  // streaming, the staging dir may be empty — poll and show "waiting for AI"
  // until SKILL.md appears, then flip to "pending" with save enabled.
  useEffect(() => {
    if (!sessionId) return;
    const submitTools: { id: string; slug: string }[] = [];
    for (const turn of turns) {
      for (const block of turn.blocks) {
        if (block.kind !== "tool") continue;
        if (!isSubmitSkillName(block.tool.title)) continue;
        const { slug } = parseSubmitInput(block.tool.input);
        if (slug && slug !== "(unknown-slug)") {
          submitTools.push({ id: block.tool.id, slug });
        }
      }
    }
    if (submitTools.length === 0) return;

    let cancelled = false;
    const sid = sessionId;
    const tick = async () => {
      if (cancelled) return;
      try {
        const res = await skillsApi.scanStaging(sid);
        const slugViewMap = new Map(res.slugs.map((s) => [s.slug, s]));
        setSubmissionStates((prev) => {
          let changed = false;
          const next: typeof prev = { ...prev };
          for (const { id, slug } of submitTools) {
            const current = next[id];
            // Never stomp terminal / in-flight user-driven transitions.
            if (
              current?.state === "confirmed" ||
              current?.state === "dismissed" ||
              current?.state === "confirming" ||
              current?.state === "dismissing"
            ) {
              continue;
            }
            const stagingPath = `${res.staging_path}/${slug}`;
            const view = slugViewMap.get(slug);
            if (view && view.files.length > 0) {
              const stagedFiles = view.files.map((f) => ({
                path: f.path,
                type: f.type,
                size: f.size ?? null,
              }));
              if (
                !current ||
                current.state !== "pending" ||
                current.stagedFiles?.length !== stagedFiles.length
              ) {
                next[id] = { state: "pending", stagedFiles, stagingPath };
                changed = true;
              }
            } else if (current?.state !== "awaiting_files") {
              next[id] = { state: "awaiting_files", stagedFiles: [], stagingPath };
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      } catch {
        // Non-fatal — scan can 404 transiently before staging is initialised.
      }
    };

    void tick();
    const interval = window.setInterval(() => void tick(), sending ? 1500 : 5000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [sessionId, turns, sending]);

  const handleConfirm = useCallback(
    async (
      toolId: string,
      slug: string,
      summary: string | undefined,
      changeKind: "create" | "update",
      filesTouched: string[],
    ) => {
      if (!sessionId) return;
      setSubmissionStates((prev) => ({
        ...prev,
        [toolId]: { ...(prev[toolId] || { state: "pending" }), state: "confirming" },
      }));
      try {
        const res = await skillsApi.confirmSubmission(sessionId, slug, {
          summary: summary ?? null,
          change_kind: changeKind,
          files_touched: filesTouched,
        });
        const boundLabel =
          res.bound_to_project_id && res.creation_context.kind === "project"
            ? (projectLabel ?? null)
            : null;
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: { state: "confirmed", boundToProjectLabel: boundLabel },
        }));
        toast.success(t("skill.savedToLib" as Parameters<typeof t>[0]));
      } catch (cause) {
        const msg =
          cause instanceof Error
            ? cause.message
            : t("common.saveFailed" as Parameters<typeof t>[0]);
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: { state: "error", errorMessage: msg },
        }));
        toast.error(msg);
      }
    },
    [sessionId, projectLabel, t],
  );

  const handleDismiss = useCallback(
    async (toolId: string, slug: string) => {
      if (!sessionId) return;
      setSubmissionStates((prev) => ({
        ...prev,
        [toolId]: { ...(prev[toolId] || { state: "pending" }), state: "dismissing" },
      }));
      try {
        await skillsApi.dismissSubmission(sessionId, slug);
        setSubmissionStates((prev) => ({ ...prev, [toolId]: { state: "dismissed" } }));
      } catch (cause) {
        const msg =
          cause instanceof Error
            ? cause.message
            : t("conversation.cancelFailed" as Parameters<typeof t>[0]);
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: { state: "error", errorMessage: msg },
        }));
        toast.error(msg);
      }
    },
    [sessionId, t],
  );

  const renderToolCall = useCallback(
    (tool: ToolLike): ReactNode | null => {
      if (!isSubmitSkillName(tool.title)) return null;
      const { slug, summary, changeKind, filesTouched } = parseSubmitInput(tool.input);
      // Initial state is "awaiting_files"; the scan effect flips it to
      // "pending" once SKILL.md exists. User interactions take precedence.
      const entry = submissionStates[tool.id] || { state: "awaiting_files" as const };
      return (
        <SkillSubmissionCard
          slug={slug}
          summary={summary}
          changeKind={changeKind}
          filesTouched={filesTouched}
          state={entry.state}
          errorMessage={entry.errorMessage}
          boundToProjectLabel={entry.boundToProjectLabel}
          stagedFiles={entry.stagedFiles}
          stagingPath={entry.stagingPath}
          onConfirm={() =>
            void handleConfirm(tool.id, slug, summary, changeKind, filesTouched)
          }
          onDismiss={() => void handleDismiss(tool.id, slug)}
        />
      );
    },
    [submissionStates, handleConfirm, handleDismiss],
  );

  return { renderToolCall };
}
