/**
 * Client for /v1/sessions/{id}/queue/* endpoints — the session input queue
 * (docs/design/session-input-queue.md). Submit follow-up inputs while a turn is
 * running; they drain FIFO after the active turn. Hand-written like the other
 * ``*-api`` modules (no OpenAPI codegen is wired today).
 */

import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setQueueApiBase = (url: string): void => {
  _apiBase = url;
};

export interface QueuedInput {
  id: string;
  /** ``queued`` (waiting) | ``blocked`` (could not run, see error_message). */
  status: string;
  position: number;
  text: string;
  attachment_count: number;
  provider_id: string | null;
  model_id: string | null;
  error_message: string | null;
  created_at: number;
  updated_at: number | null;
}

export interface QueuedInputList {
  session_id: string;
  items: QueuedInput[];
  /** True when an interrupt soft-paused auto-drain; awaiting explicit resume. */
  paused: boolean;
}

const fetchJson = createFetchJson(() => _apiBase);

const enc = encodeURIComponent;
const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
});

export const queueApi = {
  list(sessionId: string): Promise<QueuedInputList> {
    return fetchJson<QueuedInputList>(`/v1/sessions/${enc(sessionId)}/queue`);
  },

  enqueue(
    sessionId: string,
    prompt: string,
    opts: { providerId?: string | null; modelId?: string | null } = {},
  ): Promise<QueuedInputList> {
    const body: { prompt: string; provider_id?: string; model_id?: string } = {
      prompt,
    };
    if (opts.providerId) body.provider_id = opts.providerId;
    if (opts.modelId) body.model_id = opts.modelId;
    return fetchJson<QueuedInputList>(
      `/v1/sessions/${enc(sessionId)}/queue`,
      jsonInit("POST", body),
    );
  },

  edit(
    sessionId: string,
    queueId: string,
    prompt: string,
  ): Promise<QueuedInputList> {
    return fetchJson<QueuedInputList>(
      `/v1/sessions/${enc(sessionId)}/queue/${enc(queueId)}`,
      jsonInit("PATCH", { prompt }),
    );
  },

  remove(sessionId: string, queueId: string): Promise<QueuedInputList> {
    return fetchJson<QueuedInputList>(
      `/v1/sessions/${enc(sessionId)}/queue/${enc(queueId)}`,
      jsonInit("DELETE"),
    );
  },

  resume(sessionId: string): Promise<QueuedInputList> {
    return fetchJson<QueuedInputList>(
      `/v1/sessions/${enc(sessionId)}/queue/resume`,
      jsonInit("POST"),
    );
  },
};
