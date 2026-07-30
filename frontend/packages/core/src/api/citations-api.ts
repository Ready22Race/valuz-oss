import type { ResolveCitationResult } from "@valuz/shared";

import { createFetchJson, type RequestOptions } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setCitationsApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

export const citationsApi = {
  resolve(
    input: {
      sessionId: string;
      messageId: string;
      citationId: string;
    },
    options: Pick<RequestOptions, "signal"> = {},
  ): Promise<ResolveCitationResult> {
    return fetchJson("/v1/citations/resolve", {
      ...options,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: input.sessionId,
        message_id: input.messageId,
        citation_id: input.citationId,
      }),
    });
  },
};
