import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setProjectsApiBase = (url: string): void => {
  _apiBase = url;
};

export interface ProjectListItem {
  id: string;
  name: string;
  kind: "chat" | "project";
  root_path: string | null;
  icon: string | null;
  /** Resolved working directory the kernel runs sessions in.
   * Project projects: equals ``root_path``.
   * Chat projects: managed dir under ``data_dir/projects/{id}/``. */
  cwd: string | null;
}

export interface ProjectDetail extends ProjectListItem {
  instructions_md: string | null;
}

export interface ProjectDeletePreview {
  session_count: number;
  doc_binding_count: number;
  schedule_count: number;
  skill_config_count: number;
}

export interface ProjectFileNode {
  name: string;
  type: "file" | "directory";
  size: number | null;
  modified: string | null;
  children?: ProjectFileNode[];
}

export type ArtifactPreviewKind =
  | "markdown"
  | "code"
  | "image"
  | "pdf"
  | "html"
  | "docx"
  | "media"
  | "spreadsheet"
  | "plain"
  | "unsupported";

export interface ArtifactDescriptor {
  id: string;
  kind:
    | "project_file"
    | "session_output"
    | "document_artifact"
    | "slides_artifact"
    | "webpage_artifact";
  projectId?: string;
  path?: string;
  name: string;
  mimeType?: string | null;
  extension?: string | null;
  size?: number | null;
  modifiedAt?: string | null;
  previewKind: ArtifactPreviewKind;
  capabilities: {
    canPreview: boolean;
    canEdit: boolean;
    canOpenExternal: boolean;
    canCopyContent: boolean;
    canDownload: boolean;
  };
}

export type ArtifactContent =
  | {
      kind: "text";
      encoding: "utf-8";
      content: string;
      truncated: boolean;
      etag?: string | null;
      modifiedAt?: string | null;
    }
  | {
      kind: "binary";
      openUrl: string;
      mimeType: string;
      size?: number | null;
      reason?: string | null;
    }
  | {
      kind: "external";
      openUrl?: string | null;
      reason: string;
    };

export interface ArtifactFileResponse {
  artifact: ArtifactDescriptor;
  content: ArtifactContent;
}

export interface LastSessionPick {
  runtime_provider: string | null;
  provider_id: string | null;
  model_id: string | null;
  /** The last chat conversation's agent — seeds the composer's Chat mode. */
  agent_slug?: string | null;
  /** The Lead of the last task — seeds the composer's Task mode, remembered
   *  separately from the chat agent so each mode keeps its own role. */
  task_agent_slug?: string | null;
}

export interface ProjectCreateRequest {
  name: string;
  root_path: string;
}

const fetchJson = createFetchJson(() => _apiBase);

function absolutizeApiUrl(url: string): string {
  if (/^https?:\/\//i.test(url) || url.startsWith("data:") || url.startsWith("blob:")) {
    return url;
  }
  if (!url.startsWith("/")) return url;
  return `${_apiBase.replace(/\/$/, "")}${url}`;
}

function normalizeArtifactFileResponse(
  response: ArtifactFileResponse,
): ArtifactFileResponse {
  if (response.content.kind !== "binary") return response;
  return {
    ...response,
    content: {
      ...response.content,
      openUrl: absolutizeApiUrl(response.content.openUrl),
    },
  };
}

export const projectsApi = {
  list(): Promise<{ projects: ProjectListItem[] }> {
    return fetchJson("/v1/projects");
  },

  get(projectId: string): Promise<ProjectDetail> {
    return fetchJson(`/v1/projects/${encodeURIComponent(projectId)}`);
  },

  /**
   * Most-recent (runtime, provider, model) picked in this project.
   * Used by the project composer to seed pickers with the user's last
   * choice instead of the global Settings default. All three fields
   * are ``null`` when the project has no prior session.
   */
  getLastSessionPick(projectId: string): Promise<LastSessionPick> {
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/last-session-pick`,
    );
  },

  create(payload: ProjectCreateRequest): Promise<ProjectDetail> {
    return fetchJson("/v1/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  rename(projectId: string, name: string): Promise<ProjectDetail> {
    const qs = new URLSearchParams({ name });
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}?${qs}`,
      {
        method: "PATCH",
      },
    );
  },

  updateInstructions(
    projectId: string,
    instructionsMd: string,
  ): Promise<{ ok: boolean }> {
    const qs = new URLSearchParams({ instructions_md: instructionsMd });
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/instructions?${qs}`,
      {
        method: "PUT",
      },
    );
  },

  listFiles(
    projectId: string,
    opts?: { depth?: number; includeHidden?: boolean },
  ): Promise<{ files: ProjectFileNode[] }> {
    const qs = new URLSearchParams();
    if (opts?.depth !== undefined) qs.set("depth", String(opts.depth));
    if (opts?.includeHidden) qs.set("include_hidden", "true");
    const suffix = qs.toString() ? `?${qs}` : "";
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/files${suffix}`,
    );
  },

  readFile(projectId: string, filePath: string): Promise<ArtifactFileResponse> {
    const encodedPath = filePath
      .split("/")
      .map((part) => encodeURIComponent(part))
      .join("/");
    return fetchJson<ArtifactFileResponse>(
      `/v1/projects/${encodeURIComponent(projectId)}/files/${encodedPath}`,
    ).then(normalizeArtifactFileResponse);
  },

  deletePreview(projectId: string): Promise<ProjectDeletePreview> {
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/delete-preview`,
    );
  },

  delete(projectId: string): Promise<void> {
    return fetchJson(`/v1/projects/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    });
  },

  getMcpServers(projectId: string): Promise<{ slugs: string[] }> {
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/connectors`,
    );
  },

  setMcpServers(
    projectId: string,
    slugs: string[],
  ): Promise<{ ok: boolean }> {
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/connectors`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slugs }),
      },
    );
  },
};
