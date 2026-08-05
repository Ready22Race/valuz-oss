import { useCallback, useEffect, useRef, useState } from "react";

import {
  sessionsApi,
  type SessionAttachmentItem,
} from "../api/sessions-api";

/**
 * Mints (or returns) the session a freshly-attached file belongs to.
 *
 * Attachments upload **on attach**, not on send, so a session must already
 * exist before the first upload. For an ongoing conversation this just returns
 * the live session; for a brand-new / project conversation it eagerly creates
 * one (which, per ADR-006, freezes the model/agent/runtime — the composer
 * reflects that lock once a file is attached).
 */
export type EnsureSession = () => Promise<{ id: string }>;

export interface UseSessionAttachmentsResult {
  /** Every attachment row for the active session (pending + consumed). */
  attachments: SessionAttachmentItem[];
  /**
   * True while any *pending* (not-yet-sent) attachment is still parsing.
   * Callers gate Send on this to surface the "submit anyway?" confirm.
   */
  hasParsing: boolean;
  /** Upload local files immediately; eager-creates the session if needed. */
  attachLocalFiles: (files: File[], ensureSession: EnsureSession) => Promise<void>;
  /** Attach KB documents immediately; eager-creates the session if needed. */
  attachKbDocs: (docIds: string[], ensureSession: EnsureSession) => Promise<void>;
  /** Remove one attachment (optimistic drop + server delete). */
  remove: (attachmentId: string) => Promise<void>;
  /**
   * Optimistically stamp every pending row consumed — call right after a send
   * so the staging chips clear immediately (the turn marks them consumed
   * server-side only after it runs).
   */
  markPendingConsumed: () => void;
  /** Escape hatch for callers that need to splice optimistic state directly. */
  setAttachments: React.Dispatch<React.SetStateAction<SessionAttachmentItem[]>>;
}

const POLL_INTERVAL_MS = 1000;

/**
 * Owns a session's attachment staging set: load-on-session-change, eager
 * upload-on-attach for local files + KB docs, and polling of the async parse
 * status (`parsing → ready | failed`).
 *
 * The backend parses uploads off the event loop in a background task and the
 * upload returns immediately as `parse_status="parsing"`; this hook polls
 * `GET /v1/sessions/{id}/attachments` until every row settles so the composer
 * and context panel can render live progress. A turn sent while a file is
 * still `parsing` ships only the raw file reference — the backend's path
 * picker / additional-context builder already degrade gracefully.
 *
 * Shared by the conversation, new-chat, and project-conversation composers.
 */
export function useSessionAttachments(
  sessionId: string | null,
): UseSessionAttachmentsResult {
  const [attachments, setAttachments] = useState<SessionAttachmentItem[]>([]);

  // Mirror of ``sessionId`` for callbacks that must read it at CALL time.
  // ``markPendingConsumed`` fires from a closure captured before the
  // project-send handoff promoted ``sessionId`` (null → real id), so reading
  // the prop directly would miss the session the send just consumed. (Synced
  // in an effect — the promote commits long before the send's POST resolves.)
  const sessionIdRef = useRef(sessionId);
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  // Last optimistic-consume moment, per session. ``markPendingConsumed`` can
  // only stamp rows that are ALREADY in local state — but on the project-send
  // handoff the send races the very first attachments load: the GET was
  // issued before the server stamped ``consumed_at`` (that happens once the
  // turn runs), so its still-pending rows land AFTER the local stamp — either
  // replacing stamped rows or arriving into a state the stamp never saw — and
  // the composer chips resurrect with nothing left to clear them. Recording
  // the consume moment lets every server-row application re-assert it.
  const consumeWatermarkRef = useRef<{ sessionId: string; ts: number } | null>(
    null,
  );

  // Re-assert the optimistic consume on a server row the server hasn't caught
  // up with yet: a still-pending row of the watermarked session uploaded
  // BEFORE the consume moment belongs to the already-sent turn (uploads
  // happen on attach, before send). A row attached after the send stays
  // pending — it ships with the next turn.
  const applyConsumeWatermark = useCallback(
    (row: SessionAttachmentItem): SessionAttachmentItem => {
      const wm = consumeWatermarkRef.current;
      if (!wm || row.consumed_at || row.session_id !== wm.sessionId) return row;
      if (row.created_at > wm.ts) return row;
      return { ...row, consumed_at: wm.ts };
    },
    [],
  );

  // Merge fresh server rows into local state WITHOUT clobbering an optimistic
  // ``consumed_at`` we stamped on send. The turn marks rows consumed
  // server-side only after it finishes running, so a poll fired between send
  // and turn-completion would otherwise un-consume the just-sent chips and
  // flash them back into the composer's staging row.
  const mergeServer = useCallback(
    (serverRows: SessionAttachmentItem[]) => {
      setAttachments((prev) => {
        const localById = new Map(prev.map((a) => [a.id, a]));
        return serverRows.map((s) => {
          const local = localById.get(s.id);
          if (local?.consumed_at && !s.consumed_at) {
            return { ...s, consumed_at: local.consumed_at };
          }
          return applyConsumeWatermark(s);
        });
      });
    },
    [applyConsumeWatermark],
  );

  // Load on session change. CRITICAL: this races with ``attachLocalFiles`` in
  // the eager-create flow (the new-conversation composer sets ``sessionId`` to
  // the freshly-minted session, which fires this load *before* the upload has
  // committed its row — so the server returns an empty/stale list). We must NOT
  // let that stale result clobber a row the upload optimistically appended, or
  // the just-attached file silently vanishes from the composer + panel. So the
  // load MERGES: server rows win, but any optimistic row for THIS session that
  // the server hasn't returned yet is preserved. Rows from a previously-active
  // session (different ``session_id``) are dropped — this still behaves like a
  // replace across a real session switch.
  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<SessionAttachmentItem[]> => {
      if (!sessionId) return [];
      try {
        const res = await sessionsApi.listAttachments(sessionId);
        return res.items;
      } catch {
        return [];
      }
    };
    void load().then((items) => {
      if (cancelled) return;
      setAttachments((prev) => {
        if (!sessionId) return [];
        const localById = new Map(prev.map((a) => [a.id, a]));
        const serverIds = new Set(items.map((r) => r.id));
        // Server rows win, but never backwards on ``consumed_at``: a send
        // that already stamped a row optimistically must survive a load that
        // was issued before the server caught up (project-send handoff), and
        // the consume watermark covers rows the stamp never saw.
        const merged = items.map((s) => {
          const local = localById.get(s.id);
          if (local?.consumed_at && !s.consumed_at) {
            return { ...s, consumed_at: local.consumed_at };
          }
          return applyConsumeWatermark(s);
        });
        const optimistic = prev.filter(
          (a) => a.session_id === sessionId && !serverIds.has(a.id),
        );
        return [...merged, ...optimistic];
      });
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId, applyConsumeWatermark]);

  const hasParsing = attachments.some(
    (a) => !a.consumed_at && a.parse_status === "parsing",
  );

  // Poll while ANY row is still parsing. The optimistic append (attachLocalFiles)
  // puts the freshly-uploaded ``parsing`` row into local state, so this fires
  // immediately on attach and keeps the composer/panel progress live until every
  // row settles. (No grace-poll needed: the new-conversation composer stays on
  // /conversation/new instead of navigating, so the optimistic row is never
  // dropped by a navigate→bootstrap reset.)
  const anyParsing = attachments.some((a) => a.parse_status === "parsing");
  useEffect(() => {
    if (!sessionId || !anyParsing) return;
    let cancelled = false;
    const handle = setInterval(() => {
      sessionsApi
        .listAttachments(sessionId)
        .then((res) => {
          if (!cancelled) mergeServer(res.items);
        })
        .catch(() => {
          /* transient — next tick retries */
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [sessionId, anyParsing, mergeServer]);

  const attachLocalFiles = useCallback(
    async (files: File[], ensureSession: EnsureSession) => {
      if (files.length === 0) return;
      const session = await ensureSession();
      for (const file of files) {
        try {
          const item = await sessionsApi.uploadAttachment(session.id, file);
          setAttachments((prev) =>
            prev.some((a) => a.id === item.id) ? prev : [...prev, item],
          );
        } catch {
          /* best-effort; the caller surfaces an upload-failed toast */
        }
      }
    },
    [],
  );

  const attachKbDocs = useCallback(
    async (docIds: string[], ensureSession: EnsureSession) => {
      if (docIds.length === 0) return;
      const session = await ensureSession();
      await sessionsApi.addKbAttachments(session.id, docIds);
      // Re-read the full list (the KB endpoint returns pending-only); merge so
      // panel history + optimistic consume survive.
      const res = await sessionsApi.listAttachments(session.id);
      mergeServer(res.items);
    },
    [mergeServer],
  );

  const remove = useCallback(
    async (attachmentId: string) => {
      setAttachments((prev) => prev.filter((a) => a.id !== attachmentId));
      if (!sessionId) return;
      try {
        await sessionsApi.deleteAttachment(sessionId, attachmentId);
      } catch {
        /* best-effort */
      }
    },
    [sessionId],
  );

  const markPendingConsumed = useCallback(() => {
    const ts = Date.now();
    const sid = sessionIdRef.current;
    if (sid) consumeWatermarkRef.current = { sessionId: sid, ts };
    setAttachments((prev) =>
      prev.map((a) => (a.consumed_at ? a : { ...a, consumed_at: ts })),
    );
  }, []);

  return {
    attachments,
    hasParsing,
    attachLocalFiles,
    attachKbDocs,
    remove,
    markPendingConsumed,
    setAttachments,
  };
}
