/**
 * Cursor-paginated activity feed (``GET /v1/activity``) backing every history
 * list: the project-home tabs (``projectId`` set) and the global 动态 list
 * (``projectId`` omitted). "Head-poll + tail-paginate": a 4s poll refreshes the
 * first page in place while ``loadMore`` appends older pages via the keyset
 * cursor. See backend ``modules/activity``.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  activityApi,
  type ActivityItem,
  type ActivityTab,
} from "../api/activity-api";

export interface ActivityFeed {
  items: ActivityItem[];
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  loadMore: () => void;
  refresh: () => void;
}

export function useActivityFeed(opts: {
  projectId?: string | null;
  tab: ActivityTab;
  pageSize?: number;
  pollMs?: number;
  enabled?: boolean;
}): ActivityFeed {
  const {
    projectId = null,
    tab,
    pageSize = 20,
    pollMs = 4000,
    enabled = true,
  } = opts;

  const [items, setItems] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const cursorRef = useRef<string | null>(null);
  // Bumped on every project/tab switch so late responses from the old scope are
  // dropped instead of clobbering the new list.
  const genRef = useRef(0);

  const loadFirst = useCallback(async () => {
    const gen = ++genRef.current;
    setLoading(true);
    try {
      const page = await activityApi.list({ projectId, tab, limit: pageSize });
      if (gen !== genRef.current) return;
      setItems(page.items);
      cursorRef.current = page.next_cursor;
      setHasMore(Boolean(page.next_cursor));
    } catch {
      if (gen !== genRef.current) return;
      setItems([]);
      cursorRef.current = null;
      setHasMore(false);
    } finally {
      if (gen === genRef.current) setLoading(false);
    }
  }, [projectId, tab, pageSize]);

  useEffect(() => {
    if (!enabled) return;
    void loadFirst();
  }, [enabled, loadFirst]);

  const loadMore = useCallback(() => {
    const cursor = cursorRef.current;
    if (!cursor || loadingMore) return;
    const gen = genRef.current;
    setLoadingMore(true);
    activityApi
      .list({ projectId, tab, limit: pageSize, cursor })
      .then((page) => {
        if (gen !== genRef.current) return;
        setItems((prev) => {
          const seen = new Set(prev.map((i) => i.id));
          return [...prev, ...page.items.filter((i) => !seen.has(i.id))];
        });
        cursorRef.current = page.next_cursor;
        setHasMore(Boolean(page.next_cursor));
      })
      .catch(() => {
        /* keep the current list; the next loadMore retries */
      })
      .finally(() => {
        if (gen === genRef.current) setLoadingMore(false);
      });
  }, [projectId, tab, pageSize, loadingMore]);

  // Head-poll: pull the newest page and merge it over the loaded list — updates
  // in place, prepends new items, and keeps everything paged in below.
  useEffect(() => {
    if (!enabled || pollMs <= 0) return;
    const handle = window.setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      const gen = genRef.current;
      activityApi
        .list({ projectId, tab, limit: pageSize })
        .then((page) => {
          if (gen !== genRef.current) return;
          setItems((prev) => {
            const freshIds = new Set(page.items.map((i) => i.id));
            const tail = prev.filter((i) => !freshIds.has(i.id));
            return [...page.items, ...tail];
          });
        })
        .catch(() => {
          /* transient; the next tick retries */
        });
    }, pollMs);
    return () => window.clearInterval(handle);
  }, [enabled, projectId, tab, pageSize, pollMs]);

  return { items, loading, loadingMore, hasMore, loadMore, refresh: loadFirst };
}
