import { resolveApiBase } from "./base-resolver";
import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setTasksApiBase = (url: string): void => {
  _apiBase = url;
};

/** Resolved "who/what spawned this task" — drives the task-list "由 … 触发" line.
 * The source_* ids let the UI deep-link to the parent task / automation /
 * conversation; the resolved names spare a second lookup. */
export interface TaskTrigger {
  /** user | chat | agent | automation */
  type: string;
  source_task_id?: string | null;
  source_task_title?: string | null;
  source_agent_slug?: string | null;
  source_automation_id?: string | null;
  source_automation_name?: string | null;
  source_session_id?: string | null;
}

/** Durable header for a lead-dispatch task. */
export interface Task {
  id: string;
  project_id: string;
  title: string;
  goal: string;
  /** active | paused | stopped | completed | blocked */
  status: string;
  created_by: string;
  lead_agent_slug: string;
  current_holder: string;
  file_path: string;
  /** Unix epoch milliseconds (UTC); format via ``new Date(ms)``.
   * ``updated_at`` powers the sidebar TASKS section ordering
   * ("active just now" vs "completed yesterday"). */
  created_at: number;
  updated_at: number;
  /** Trigger provenance, resolved server-side. ``null`` for legacy tasks. */
  trigger?: TaskTrigger | null;
}

/** One kernel session that belongs to a task (lead or dispatched subtask). */
export interface TaskRun {
  id: string;
  session_id: string;
  agent_slug: string;
  sequence: number;
  /** lead | subtask */
  kind: string;
  status: string;
  label: string | null;
  goal: string | null;
  dispatched_by: string | null;
  project_mode: string;
  run_dir: string | null;
  /** {summary, artifacts, status} — populated when the run completes. */
  result_manifest: Record<string, unknown> | null;
}

/** One entry in the task's append-only event log. */
export interface TaskEvent {
  id: string;
  sequence: number;
  /** kickoff | subtask_spawned | subtask_completed | subtask_failed |
   *  subtask_message | user_note | goal_revised | paused | resumed |
   *  stopped | task_completed */
  type: string;
  /** user | <agent_slug> | system */
  actor: string;
  session_id: string | null;
  payload: Record<string, unknown>;
  created_at: number;
}

export interface TaskDetail {
  task: Task;
  runs: TaskRun[];
  events: TaskEvent[];
}


export interface KickoffTaskPayload {
  goal: string;
  lead_agent_slug: string;
  refs?: string[] | null;
  title?: string | null;
  created_by?: string;
  /** Dispatch architecture (M10): "sync" (v1) or "async" (v2 actor). */
  dispatch_mode?: "sync" | "async";
  /**
   * Task-level worktree isolation: the whole task (lead + every member)
   * runs in ONE git worktree of the project repo. A clean worktree is
   * removed when the task finishes; one with work left surfaces in the
   * project's worktrees panel. Requires a git-repo project (422 otherwise).
   */
  worktree?: boolean;
}

export interface IntervenePayload {
  action: "note" | "revise_goal" | "pause" | "resume" | "stop";
  text?: string | null;
  goal?: string | null;
}

// ---- VALUZ-CHATPLAN S3 — draft / commit / abandon / inject / plan -----

/** One subtask as rendered by the Plan Card and Todo panel — matches
 * ``TaskPlan.to_panel()`` on the backend. */
export interface PlanSubtask {
  key: string;
  label: string;
  agent: string;
  /** "planned" | "in_progress" | "in_review" | "rework" | "done" | "failed" */
  status: string;
  depends_on: string[];
  parallel_group: string | null;
  goal: string | null;
  attempts: number;
  review_criteria: string | null;
  review_feedback: string | null;
}

export interface PlanResponse {
  subtasks: PlanSubtask[];
  ready: string[];
  counts?: Record<string, number>;
  all_done?: boolean;
  current_version: number;
}

export interface DraftTaskPayload {
  goal: string;
  lead_agent_slug: string;
  originating_session_id: string;
  refs?: string[] | null;
  title?: string | null;
}

export interface DraftTaskResponse {
  task_id: string;
  status: string;
  plan_version: number;
  title: string;
  lead_agent_slug: string;
}

export interface CommitTaskPayload {
  caller_session_id: string;
  lead_agent_slug?: string | null;
}

export interface CommitTaskResponse {
  task_id: string;
  lead_session_id: string;
  status: string;
  committed_at: number;
}

export interface AbandonTaskPayload {
  caller_session_id: string;
  reason?: string | null;
}

export interface InjectTaskPayload {
  text: string;
  from_session_id: string;
}

export interface InjectTaskResponse {
  delivered: boolean;
  lead_session_id?: string | null;
  reason?: string | null;
}

export interface PlanWritePayload {
  lead_session_id: string;
  /** Initial plan creation (POST /plan). */
  subtasks?: Array<Record<string, unknown>>;
  /** Patch operations (PATCH /plan). */
  add?: Array<Record<string, unknown>>;
  update?: Array<Record<string, unknown>>;
  /** CAS token from the last ``current_version`` read. Required for
   * multi-writer (chat) callers; optional for single-writer (lead). */
  expected_version?: number;
}

const fetchJson = createFetchJson(() => _apiBase);

const projectBase = (projectId: string): string =>
  resolveApiBase({ projectId }, _apiBase);
const taskBase = (taskId: string): string =>
  resolveApiBase({ taskId }, _apiBase);

export const tasksApi = {
  kickoff(projectId: string, payload: KickoffTaskPayload): Promise<Task> {
    return fetchJson(`/v1/projects/${encodeURIComponent(projectId)}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: projectBase(projectId),
    });
  },

  listTasks(
    projectId: string,
    init?: { signal?: AbortSignal },
  ): Promise<{ tasks: Task[] }> {
    // ``init`` (e.g. an ``AbortSignal`` for the project-detail auto-refresh
    // poller) is forwarded to ``fetchJson`` → ``fetch``. Existing callers pass
    // nothing, so their behaviour is unchanged.
    return fetchJson(`/v1/projects/${encodeURIComponent(projectId)}/tasks`, {
      ...init,
      baseUrl: projectBase(projectId),
    });
  },

  /** Global cross-project task list, newest activity first. Backs the
   * sidebar TASKS section so users see what's running regardless of
   * which project page they're on. */
  listAllTasks(limit = 50): Promise<{ tasks: Task[] }> {
    return fetchJson(`/v1/tasks?limit=${encodeURIComponent(limit)}`);
  },

  getTask(taskId: string): Promise<TaskDetail> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}`, {
      baseUrl: taskBase(taskId),
    });
  },

  listEvents(taskId: string): Promise<{ events: TaskEvent[] }> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/events`, {
      baseUrl: taskBase(taskId),
    });
  },

  /** SSE endpoint URL for a task's event timeline. Subscribers connect with
   * ``fetchEventSource(() => eventsStreamUrl(id, lastSeq), …)`` and remember the
   * last received ``sequence`` so a reconnect resumes from the cursor (cursor
   * is monotonic per task — no gaps possible). */
  eventsStreamUrl(taskId: string, afterSeq = 0): string {
    const cursor = afterSeq > 0 ? `?after_seq=${afterSeq}` : "";
    return `${taskBase(taskId)}/v1/tasks/${encodeURIComponent(taskId)}/events/stream${cursor}`;
  },

  intervene(taskId: string, payload: IntervenePayload): Promise<Task> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}:intervene`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  // ---- VALUZ-CHATPLAN S3 ----

  /** Open a draft task (status=draft, plan_version=0). No lead session is
   * created; the originating chat session becomes the plan writer. */
  draft(
    projectId: string,
    payload: DraftTaskPayload,
  ): Promise<DraftTaskResponse> {
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/tasks:draft`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        baseUrl: projectBase(projectId),
      },
    );
  },

  /** Promote a draft task to active by spawning its lead session. */
  commit(
    taskId: string,
    payload: CommitTaskPayload,
  ): Promise<CommitTaskResponse> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}:commit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  /** Discard a draft task (terminal — cannot be resurrected). */
  abandon(
    taskId: string,
    payload: AbandonTaskPayload,
  ): Promise<{ task_id: string; status: string }> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}:abandon`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  /** Push a user instruction into the running lead's mailbox. The lead
   * reads it at the next turn boundary. */
  inject(
    taskId: string,
    payload: InjectTaskPayload,
  ): Promise<InjectTaskResponse> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}:inject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  /** Lay down the initial subtask plan. Fails when a plan with progress
   * already exists — call ``modifyPlan`` instead. */
  plan(taskId: string, payload: PlanWritePayload): Promise<PlanResponse> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  /** Patch the plan: add nodes / update existing nodes. CAS-protected via
   * ``expected_version`` — the server returns 409 on conflict. */
  modifyPlan(taskId: string, payload: PlanWritePayload): Promise<PlanResponse> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/plan`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      baseUrl: taskBase(taskId),
    });
  },

  /** Read the plan snapshot + ready keys + counts + current_version. */
  getPlan(taskId: string): Promise<PlanResponse> {
    return fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/plan`, {
      baseUrl: taskBase(taskId),
    });
  },
};
