import { useCallback, useMemo } from "react";

import type { ApiBaseRef } from "../api/base-resolver";
import { filesApi, type ResolvedFileDescriptor } from "../api/files-api";

export interface UseFileResolverResult {
  /** Resolve one ``valuz-file://`` ref to an access-address descriptor (null on error). */
  resolveOne: (ref: string) => Promise<ResolvedFileDescriptor | null>;
  /** Resolve a batch in one round-trip. */
  resolveMany: (refs: string[]) => Promise<ResolvedFileDescriptor[]>;
}

/**
 * Exchange ``valuz-file://`` refs for access addresses via ``POST /v1/files/resolve``.
 *
 * ``baseRef`` names the entity the files belong to so multi-target editions
 * route the call to the backend that owns them (see ``FileResolveOptions``).
 *
 * Deliberately un-cached: a ``remote`` descriptor carries a short-lived presigned
 * URL, so callers resolve lazily at open time (a stale cache would hand back an
 * expired URL). See docs/design/file-address-resolution.md.
 */
export function useFileResolver(baseRef?: ApiBaseRef): UseFileResolverResult {
  // Spread into the deps so a caller passing an inline object literal doesn't
  // rebuild the callbacks on every render.
  const { sessionId, projectId, taskId, automationId, kbId } = baseRef ?? {};
  const ref: ApiBaseRef = useMemo(
    () => ({ sessionId, projectId, taskId, automationId, kbId }),
    [sessionId, projectId, taskId, automationId, kbId],
  );

  const resolveOne = useCallback(
    async (fileRef: string): Promise<ResolvedFileDescriptor | null> => {
      try {
        return await filesApi.resolveOne(fileRef, { baseRef: ref });
      } catch {
        return null;
      }
    },
    [ref],
  );

  const resolveMany = useCallback(
    async (refs: string[]): Promise<ResolvedFileDescriptor[]> => {
      if (refs.length === 0) return [];
      try {
        const res = await filesApi.resolve(refs, { baseRef: ref });
        return res.results;
      } catch {
        return [];
      }
    },
    [ref],
  );

  return { resolveOne, resolveMany };
}
