/**
 * Client for /v1/memory/* endpoints (memory-system-design §11 / P2 #6).
 *
 * Inspect stored memory, prune individual entries or clear a scope, and
 * toggle the memory system on/off. Mirrors the hand-written client pattern
 * used by the other ``*-api`` modules (no OpenAPI codegen is wired today).
 */

import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setMemoryApiBase = (url: string): void => {
  _apiBase = url;
};

export type MemoryTarget = "user" | "global" | "project";

export interface MemoryView {
  enabled: boolean;
  auto_extract: boolean;
  /** Entries per scope, keyed by target (user / global / project-when-bound). */
  entries: Record<string, string[]>;
}

export interface MemorySettings {
  enabled: boolean;
  auto_extract: boolean;
}

export interface MemorySettingsPatch {
  enabled?: boolean;
  auto_extract?: boolean;
}

const fetchJson = createFetchJson(() => _apiBase);

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const memoryApi = {
  getMemory(projectId?: string): Promise<MemoryView> {
    const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    return fetchJson<MemoryView>(`/v1/memory${q}`);
  },

  patchSettings(payload: MemorySettingsPatch): Promise<MemorySettings> {
    return fetchJson<MemorySettings>(
      "/v1/memory/settings",
      jsonInit("PATCH", payload),
    );
  },

  deleteEntry(payload: {
    target: MemoryTarget;
    old_text: string;
    project_id?: string;
  }): Promise<MemoryView> {
    return fetchJson<MemoryView>("/v1/memory/entry", jsonInit("DELETE", payload));
  },

  clearScope(payload: {
    target: MemoryTarget;
    project_id?: string;
  }): Promise<MemoryView> {
    return fetchJson<MemoryView>("/v1/memory/scope", jsonInit("DELETE", payload));
  },
};
