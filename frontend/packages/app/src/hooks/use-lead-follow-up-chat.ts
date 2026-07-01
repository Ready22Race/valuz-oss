import { useCallback, useEffect, useMemo, useState } from "react";
import {
  buildTurns,
  useStableTurns,
  sessionsApi,
  type SessionEventDTO,
} from "@valuz/core";
import type { ConversationTurn } from "@valuz/shared";

export interface LeadFollowUpChat {
  turns: ConversationTurn[];
  sending: boolean;
  send: (text: string) => Promise<void>;
  /** Raw lead-session events (list + live SSE) — drives tool-card renderers
   *  such as ``useAskUserQuestionCards`` that need the event stream. */
  events: SessionEventDTO[];
}

/**
 * Minimal follow-up chat over a completed task's lead session. Loads history
 * once and subscribes to the SSE stream, then renders the conversation starting
 * from the user's FIRST follow-up message (the first ``message.user`` event
 * after ``sinceTs``) so neither the orchestration history nor the lead's closing
 * summary leaks into the user-facing follow-up conversation.
 *
 * Why anchor on the first user message rather than a raw ``timestamp > sinceTs``
 * cutoff: the lead's finish turn emits its wrap-up ``assistant_message`` AFTER
 * the ``finish_task`` tool result — i.e. a beat *after* the ``task_completed``
 * timestamp. A pure timestamp filter keeps that summary and it surfaces at the
 * top of the chat, duplicating the deliverable card. The first post-completion
 * user message is the true start of the follow-up dialogue; everything above it
 * (the leaked summary included) is dropped.
 */
export function useLeadFollowUpChat(params: {
  leadSessionId: string | null;
  sinceTs: number | null;
}): LeadFollowUpChat {
  const { leadSessionId, sinceTs } = params;
  const [events, setEvents] = useState<SessionEventDTO[]>([]);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    setEvents([]);
    if (!leadSessionId) return;
    const ac = new AbortController();
    let cancelled = false;
    void (async () => {
      try {
        const { items } = await sessionsApi.listEvents(leadSessionId);
        if (cancelled) return;
        setEvents(items);
        const lastSeq = items.length ? items[items.length - 1].seq : 0;
        await sessionsApi.subscribeEvents(
          leadSessionId,
          (ev) => {
            if (!cancelled) setEvents((prev) => [...prev, ev]);
          },
          lastSeq,
          ac.signal,
        );
      } catch {
        /* listEvents failure or SSE drop/abort — no recovery in this minimal hook */
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [leadSessionId]);

  const followUpEvents = useMemo(() => {
    if (sinceTs == null) return [];
    // Anchor on the first ``message.user`` event after completion — that is the
    // user's opening follow-up message. Everything before it (the orchestration
    // history and the lead's leaked closing summary) is sliced off. ``> sinceTs``
    // skips the original task goal (and any mid-run user turns), which carry an
    // earlier timestamp.
    const firstUserIdx = events.findIndex(
      (e) =>
        e.event.event_type === "message.user" && (e.timestamp ?? 0) > sinceTs,
    );
    return firstUserIdx === -1 ? [] : events.slice(firstUserIdx);
  }, [events, sinceTs]);
  const rawTurns = useMemo(() => buildTurns(followUpEvents), [followUpEvents]);
  const turns = useStableTurns(rawTurns);

  // A run is in flight from a user send until the next ``session.idle`` /
  // ``run.failed`` — mirrors the chat view's ``isStreaming``. Derived from the
  // live event stream (last ``message.user`` newer than the last terminal
  // event) so the latest turn shows the streaming indicator (logo loader) and
  // hides its copy button until the turn actually completes, exactly like a
  // normal conversation.
  const streaming = useMemo(() => {
    let lastTerminal = -1;
    let lastUser = -1;
    events.forEach((e, i) => {
      const type = e.event.event_type;
      if (type === "session.idle" || type === "run.failed") lastTerminal = i;
      else if (type === "message.user") lastUser = i;
    });
    return lastUser > lastTerminal;
  }, [events]);

  // The caller is expected to gate on ``sending`` (disable the composer while a
  // turn is in flight); this hook does not itself guard against concurrent sends.
  const send = useCallback(
    async (text: string) => {
      if (!leadSessionId || !text.trim()) return;
      // Optimistic: hold the streaming state through the send-HTTP window too,
      // before the ``message.user`` event echoes back over SSE — otherwise the
      // indicator flickers off between send and echo.
      setSending(true);
      try {
        await sessionsApi.sendMessage(leadSessionId, text);
      } finally {
        setSending(false);
      }
    },
    [leadSessionId],
  );

  return { turns, sending: sending || streaming, send, events };
}
