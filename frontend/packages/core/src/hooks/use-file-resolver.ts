import { useCallback } from "react";

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
 * Deliberately un-cached: a ``remote`` descriptor carries a short-lived presigned
 * URL, so callers resolve lazily at open time (a stale cache would hand back an
 * expired URL). See docs/design/file-address-resolution.md.
 */
export function useFileResolver(): UseFileResolverResult {
  const resolveOne = useCallback(
    async (ref: string): Promise<ResolvedFileDescriptor | null> => {
      try {
        return await filesApi.resolveOne(ref);
      } catch {
        return null;
      }
    },
    [],
  );

  const resolveMany = useCallback(
    async (refs: string[]): Promise<ResolvedFileDescriptor[]> => {
      if (refs.length === 0) return [];
      try {
        const res = await filesApi.resolve(refs);
        return res.results;
      } catch {
        return [];
      }
    },
    [],
  );

  return { resolveOne, resolveMany };
}
