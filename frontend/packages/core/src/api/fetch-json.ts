/**
 * Shared ``fetchJson`` factory — eliminates the 14 duplicate copies that
 * were scattered across the API modules.
 *
 * Each API module calls ``createFetchJson(() => _apiBase)`` once at
 * module scope and uses the returned function for all requests.
 * Call-sites stay ``fetchJson(path, init)`` — no changes needed.
 */

import { safeLocalGet } from "@valuz/shared";

/**
 * Error thrown for any non-2xx response. Carries the HTTP ``status`` so callers
 * can branch on it (e.g. ``402`` → insufficient balance) instead of string-
 * matching the message. ``message`` is the user-facing detail extracted from
 * the FastAPI ``{detail}`` body; ``body`` keeps the raw response text.
 *
 * Extends ``Error`` so existing ``err instanceof Error`` / ``err.message``
 * handling keeps working unchanged.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly body?: string;
  /** Optional i18n key the backend asked the client to render (structured
   *  ``detail.message_key``). When present, callers should prefer ``t(i18nKey,
   *  i18nParams)`` over the raw ``message``. */
  readonly i18nKey?: string;
  readonly i18nParams?: Record<string, unknown>;
  constructor(
    message: string,
    status: number,
    body?: string,
    i18nKey?: string,
    i18nParams?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.i18nKey = i18nKey;
    this.i18nParams = i18nParams;
  }
}

interface ExtractedError {
  message: string | null;
  i18nKey?: string;
  i18nParams?: Record<string, unknown>;
}

/**
 * Pull error info from FastAPI's ``{detail: ...}`` body. Supports three shapes:
 *  - ``detail: "text"``                        → plain message
 *  - ``detail: { reason: "text" }``            → legacy reason message
 *  - ``detail: { message_key, message_params?, message? }`` → an i18n key the
 *    client renders via ``t(key, params)``; ``message`` is the fallback.
 */
function extractError(text: string): ExtractedError {
  try {
    const parsed = JSON.parse(text);
    const detail = parsed?.detail;
    if (typeof detail === "string") return { message: detail };
    if (detail && typeof detail === "object") {
      const i18nKey =
        typeof detail.message_key === "string" ? detail.message_key : undefined;
      const i18nParams =
        detail.message_params && typeof detail.message_params === "object"
          ? (detail.message_params as Record<string, unknown>)
          : undefined;
      const message =
        typeof detail.message === "string"
          ? detail.message
          : typeof detail.reason === "string"
            ? detail.reason
            : null;
      return { message, i18nKey, i18nParams };
    }
  } catch {
    // not JSON
  }
  return { message: null };
}

/** Read the current UI locale from localStorage. Mirrors
 * ``readStoredLocale`` in ``@valuz/shared/i18n``. Used to attach
 * ``Accept-Language`` to every backend request so server-side
 * resources (e.g. ``GET /v1/connectors/recommended``) can pick the
 * right copy from i18n-shaped catalog entries. */
function currentLocale(): "zh-CN" | "en-US" {
  const v = safeLocalGet("valuz-locale");
  return v === "en-US" || v === "zh-CN" ? v : "zh-CN";
}

export function createFetchJson(getBase: () => string) {
  return async function fetchJson<T>(
    path: string,
    init?: RequestInit,
  ): Promise<T> {
    // Merge Accept-Language into any caller-supplied headers without
    // clobbering them. Locale is read per-request, so locale flips
    // mid-session don't require a transport rebuild.
    const headers = new Headers(init?.headers);
    if (!headers.has("Accept-Language")) {
      headers.set("Accept-Language", currentLocale());
    }
    let res: Response;
    try {
      res = await fetch(`${getBase()}${path}`, { ...init, headers });
    } catch (err) {
      throw new Error(
        err instanceof TypeError
          ? "无法连接到后端服务，请检查应用进程是否运行"
          : err instanceof Error
            ? err.message
            : "网络请求失败",
      );
    }
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      const { message, i18nKey, i18nParams } = extractError(text);
      throw new ApiError(
        message ?? `API ${res.status}: ${text}`,
        res.status,
        text,
        i18nKey,
        i18nParams,
      );
    }
    if (res.status === 204) return undefined as T;
    return res.json() as Promise<T>;
  };
}
