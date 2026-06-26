import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Clock3, FilePenLine, Pause, Play, Power, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  BackLink,
  Button,
  DeleteConfirmDialog,
  EmptyState,
  ExecutionLog,
  PageLoader,
} from "@valuz/ui";
import type { ExecutionLogRow } from "@valuz/ui";
import {
  agentsApi,
  automationsApi,
  useTranslation,
  type ActionKind,
  type AutomationDetail,
  type AutomationRunItem,
  type MemberWithAgent,
  type Trigger,
} from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";
import {
  CreateAutomationDialog,
  type AutomationAgentChoice,
} from "@valuz/app/components";

type I18nKey = Parameters<ReturnType<typeof useTranslation>["t"]>[0];
const k = (key: string) => key as I18nKey;

// ── Helpers (mirrors AutomationPage) ────────────────────────────────

function runStatusToLogStatus(
  status: AutomationRunItem["status"],
): ExecutionLogRow["status"] {
  if (status === "success") return "ok";
  if (status === "failed") return "err";
  if (status === "queued" || status === "running") return "pending";
  return "skip";
}

function runToLogStatus(run: AutomationRunItem): ExecutionLogRow["status"] {
  if (run.task_status) {
    if (run.task_status === "completed") return "ok";
    if (run.task_status === "failed") return "err";
    return "pending";
  }
  return runStatusToLogStatus(run.status);
}

function formatRunTime(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m${s % 60 > 0 ? `${s % 60}s` : ""}`;
}

// ── Page ────────────────────────────────────────────────────────────

export const AutomationDetailPage = () => {
  const { automationId = "" } = useParams<{ automationId: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { setHeader, setHeaderClassName, setContentInnerClassName } =
    useProjectOutlet();

  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<AutomationDetail | null>(null);
  const [runs, setRuns] = useState<AutomationRunItem[]>([]);
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [editMembers, setEditMembers] = useState<MemberWithAgent[] | null>(null);

  const refreshRuns = useCallback(async () => {
    try {
      const res = await automationsApi.listRuns(automationId, 50);
      setRuns(res.runs);
    } catch {
      /* silent poll */
    }
  }, [automationId]);

  const loadAll = useCallback(async () => {
    try {
      const [det, runsRes] = await Promise.all([
        automationsApi.get(automationId),
        automationsApi.listRuns(automationId, 50),
      ]);
      setDetail(det);
      setRuns(runsRes.runs);
      // Pre-load members for the edit dialog
      const membersRes = await agentsApi
        .listMembers(det.project_id)
        .catch(() => ({ agents: [] as MemberWithAgent[] }));
      setEditMembers(membersRes.agents);
    } catch (error) {
      toast.error(String(error));
    } finally {
      setLoading(false);
    }
  }, [automationId]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // Poll runs every 5s for live task-status updates
  useEffect(() => {
    if (!detail) return;
    const id = setInterval(() => void refreshRuns(), 5000);
    return () => clearInterval(id);
  }, [detail, refreshRuns]);

  // ── Header ─────────────────────────────────────────────────────────

  const pageHeader = useMemo(
    () =>
      detail ? (
        <div className="flex items-center justify-between px-5 py-4">
          <div className="flex min-w-0 items-center gap-2">
            <BackLink
              label={t(k("automation.title"))}
              onClick={() => navigate("/automations")}
            />
            <span className="text-ink-meta">/</span>
            <span className="truncate text-sm text-ink-body">{detail.name}</span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <Button variant="ghost" size="icon" onClick={() => setEditOpen(true)}>
              <FilePenLine className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="text-destructive hover:text-destructive"
              onClick={() => setDeleteOpen(true)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
            <Button size="sm" onClick={() => void handleRunNow()}>
              <Play className="h-3.5 w-3.5" />
              {t(k("cron.runNow"))}
            </Button>
          </div>
        </div>
      ) : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [detail, navigate, t],
  );

  useEffect(() => {
    setHeader(pageHeader);
    setHeaderClassName("h-auto border-b border-surface-border");
    setContentInnerClassName("p-0");
    return () => {
      setHeader(null);
      setHeaderClassName(undefined);
      setContentInnerClassName(undefined);
    };
  }, [pageHeader, setHeader, setHeaderClassName, setContentInnerClassName]);

  // ── Mutations ───────────────────────────────────────────────────────

  const handleToggle = async () => {
    if (!detail) return;
    try {
      if (detail.status === "enabled") {
        await automationsApi.pause(automationId);
        toast.success(t(k("automation.pauseSuccess"), { name: detail.name }));
      } else {
        await automationsApi.resume(automationId);
        toast.success(t(k("automation.resumeSuccess"), { name: detail.name }));
      }
      await loadAll();
    } catch (error) {
      toast.error(t(k("automation.toggleFailed"), { error: String(error) }));
    }
  };

  const handleRunNow = async () => {
    try {
      await automationsApi.runNow(automationId);
      toast.success(t(k("automation.runQueued")));
      void refreshRuns();
    } catch (error) {
      toast.error(t(k("automation.runFailed"), { error: String(error) }));
    }
  };

  const handleDelete = async () => {
    if (!detail) return;
    try {
      await automationsApi.delete(automationId);
      toast.success(t(k("automation.deleteSuccess"), { name: detail.name }));
      navigate("/automations");
    } catch (error) {
      toast.error(t(k("automation.deleteFailed"), { error: String(error) }));
    }
  };

  const handleEditSubmit = async (data: {
    name: string;
    prompt_template: string;
    agent_slug: string;
    trigger: Trigger;
    action_kind: ActionKind;
  }) => {
    try {
      await automationsApi.update(automationId, data);
      toast.success(t(k("automation.updateSuccess"), { name: data.name }));
      await loadAll();
    } catch (error) {
      toast.error(t(k("automation.updateFailed"), { error: String(error) }));
      throw error;
    }
  };

  const agentChoices: AutomationAgentChoice[] = useMemo(
    () =>
      (editMembers ?? []).map((entry) => ({
        slug: entry.member.agent_slug,
        name: entry.agent?.name ?? entry.member.agent_slug,
      })),
    [editMembers],
  );

  // ── Execution log rows ──────────────────────────────────────────────

  const executionRows: ExecutionLogRow[] = runs.map((run) => ({
    id: run.run_id,
    time: formatRunTime(run.triggered_at),
    status: runToLogStatus(run),
    duration: formatDuration(run.duration_ms),
    output:
      (run.error_message_key
        ? t(run.error_message_key as I18nKey)
        : null) ??
      run.result_summary ??
      run.error_message ??
      (run.error_code ? `${run.error_code}` : ""),
    triggerType:
      run.trigger_type === "cron" ||
      run.trigger_type === "interval" ||
      run.trigger_type === "manual" ||
      run.trigger_type === "agent" ||
      run.trigger_type === "recovered_skip"
        ? run.trigger_type
        : undefined,
    taskName: detail?.name ?? "",
    sessionId: run.session_id,
  }));

  // ── Render ─────────────────────────────────────────────────────────

  if (loading) return <PageLoader />;
  if (!detail) return null;

  const triggerExpr =
    detail.trigger.kind === "cron"
      ? detail.trigger.cron_expr
      : detail.trigger.kind === "interval"
        ? `${detail.trigger.seconds}s`
        : "—";

  return (
    <div className="relative h-full min-h-0 overflow-y-auto bg-card">
      {/* Title + status section */}
      <div className="px-8 pt-6 pb-5">
        <h1 className="text-2xl font-semibold text-ink-heading">{detail.name}</h1>
        {detail.agent_name && (
          <p className="mt-1 text-sm text-ink-meta">{detail.agent_name}</p>
        )}
        <div className="mt-3 flex items-center gap-2">
          <button
            onClick={() => void handleToggle()}
            className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 ${
              detail.status === "enabled"
                ? "bg-primary"
                : "bg-input"
            }`}
          >
            <span
              className={`pointer-events-none inline-block h-4 w-4 translate-x-0.5 rounded-full bg-white shadow-sm transition-transform ${
                detail.status === "enabled" ? "translate-x-4" : ""
              }`}
            />
          </button>
          <span className="text-sm text-ink-meta flex items-center gap-1">
            {detail.status === "enabled" ? (
              t(k("cron.statusOn"))
            ) : (
              <>
                <Pause className="h-3 w-3" />
                {t(k("cron.paused"))}
              </>
            )}
          </span>
        </div>
      </div>

      <div className="mx-8 border-t border-surface-border" />

      {/* Two-column layout */}
      <div className="grid grid-cols-[1fr_380px] gap-0 px-8 py-6">
        {/* Left: execution history */}
        <div className="min-w-0 border-r border-surface-border pr-8">
          <h2 className="mb-4 text-sm font-medium text-ink-meta">
            {t(k("cron.executionHistory"))}
          </h2>
          {executionRows.length > 0 ? (
            <ExecutionLog
              rows={executionRows}
              onSessionClick={(sessionId) =>
                navigate(`/conversation/${sessionId}`)
              }
            />
          ) : (
            <div className="flex justify-center py-8">
              <EmptyState
                variant="plain"
                title={t(k("automation.noExecutions"))}
                icon={<Clock3 className="h-5 w-5" />}
              />
            </div>
          )}
        </div>

        {/* Right: instructions + trigger */}
        <div className="pl-8 text-sm">
          <h2 className="mb-3 text-sm font-medium text-ink-meta">
            {t(k("cron.instruction"))}
          </h2>
          <p className="whitespace-pre-wrap text-ink-body leading-relaxed">
            {detail.prompt_template}
          </p>

          <div className="mt-6">
            <h2 className="mb-2 text-sm font-medium text-ink-meta">
              {t(k("cron.triggerColumn"))}
            </h2>
            <p className="font-medium text-ink-heading">
              {detail.trigger_human_readable}
            </p>
            {triggerExpr !== "—" && (
              <p className="mt-0.5 font-mono text-xs text-ink-meta">{triggerExpr}</p>
            )}
          </div>
        </div>
      </div>

      <CreateAutomationDialog
        open={editOpen}
        onOpenChange={(open) => setEditOpen(open)}
        onSubmit={handleEditSubmit}
        agents={agentChoices}
        allowTaskMode={detail.project_kind === "project"}
        fixedTargetName={detail.project_name}
        initial={{
          name: detail.name,
          prompt_template: detail.prompt_template,
          agent_slug: detail.agent_slug,
          trigger: detail.trigger,
          action_kind: (detail.action_kind as ActionKind) ?? "chat",
        }}
        title={t(k("automation.dialogTitleEditNamed"), { name: detail.name })}
      />

      <DeleteConfirmDialog
        open={deleteOpen}
        onOpenChange={(open) => setDeleteOpen(open)}
        title={t(k("automation.deleteTitle"), { name: detail.name })}
        description={t(k("automation.deleteConfirmDesc"))}
        confirmLabel={t(k("common.delete"))}
        onConfirm={handleDelete}
      />
    </div>
  );
};
