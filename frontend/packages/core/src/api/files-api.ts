import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setFilesApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

/** The ``valuz-file://`` URI scheme — a file's identity is its absolute path. */
export const FILE_URI_SCHEME = "valuz-file:";
const FILE_URI_PREFIX = `${FILE_URI_SCHEME}//`;

/** Build a ``valuz-file://<abs>`` ref from an absolute path (POSIX or Windows). */
export function buildFileRef(absPath: string): string {
  let p = absPath.replace(/\\/g, "/");
  if (!p.startsWith("/")) p = `/${p}`; // C:/x -> /C:/x so the result is three-slash
  // encode each segment but keep the slashes
  const encoded = p
    .split("/")
    .map((seg) => (seg ? encodeURIComponent(seg) : seg))
    .join("/");
  return `${FILE_URI_SCHEME}/${encoded}`;
}

/** True when ``ref`` is a ``valuz-file://`` URI. */
export function isFileRef(ref: string): boolean {
  return ref.startsWith(FILE_URI_PREFIX);
}

/** Extract the absolute path from a ``valuz-file://<abs>`` ref, or null. */
export function parseFileRef(ref: string): string | null {
  if (!isFileRef(ref)) return null;
  let path = decodeURIComponent(ref.slice(FILE_URI_SCHEME.length + 2));
  // /C:/x -> C:/x
  if (/^\/[A-Za-z]:\//.test(path)) path = path.slice(1);
  return path || null;
}

/**
 * How the client should reach a file. ``kind==="local"`` carries ``absPath``
 * (read it via the desktop ``valuz-local://`` protocol / IPC); ``kind==="remote"``
 * carries a presigned ``url`` the client fetches directly. See
 * docs/design/file-address-resolution.md.
 */
export interface FileCapabilities {
  canPreview: boolean;
  canDownload: boolean;
  /** Only ``true`` for local files; the client further gates by whether it is Electron. */
  canOpenExternal: boolean;
  canCopyContent: boolean;
}

export interface ResolvedFileDescriptor {
  ref: string;
  kind: "local" | "remote" | "";
  absPath: string | null;
  url: string | null;
  expiresAt: number | null;
  name: string;
  mimeType: string | null;
  size: number | null;
  exists: boolean;
  previewKind: string;
  capabilities: FileCapabilities;
  /** ``"invalid_ref" | "forbidden" | "not_found"`` when the ref could not be resolved. */
  error: string | null;
}

const MAX_REFS = 256;

export const filesApi = {
  /**
   * Resolve a batch of ``valuz-file://`` refs into access-address descriptors.
   * The backend never returns file bytes — the client fetches from the returned
   * address (desktop ``valuz-local://`` for local, presigned URL for remote).
   */
  resolve(refs: string[]): Promise<{ results: ResolvedFileDescriptor[] }> {
    return fetchJson("/v1/files/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refs: refs.slice(0, MAX_REFS) }),
    });
  },

  /** Convenience: resolve a single ref (returns null on error/empty). */
  async resolveOne(ref: string): Promise<ResolvedFileDescriptor | null> {
    const res = await filesApi.resolve([ref]);
    return res.results[0] ?? null;
  },
};
