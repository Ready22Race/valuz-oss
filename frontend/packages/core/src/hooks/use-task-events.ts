import { useEffect, useRef, useState } from "react";
import { fetchEventSource } from "../api/fetch-event-source";
import { tasksApi, type TaskEvent } from "../api/tasks-api";

/**
 * Subscribe to a task's event log (VALUZ-CHATPLAN S3 + S5).
 *
 * Reads ``GET /v1/tasks/{taskId}/events/stream`` as fetch-based SSE (not
 * ``EventSource``, so the request carries auth headers) and surfaces each task
 * event to the caller as it arrives. Events arrive sequentially in append-only
 * order, indexed by ``sequence`` — the hook remembers the highest sequence it
 * has seen so a reconnect resumes exactly where it left off (the server emits
 * anything with ``sequence > after_seq``).
 *
 * Reconnect: ``fetchEventSource`` reconnects on transient drops and re-reads
 * the URL each time, so the latest ``?after_seq=`` cursor is threaded and no
 * events are missed.
 *
 * Terminal tasks: the server ends the stream of a finished task with a final
 * ``stream_end`` event (browsers allow only 6 concurrent connections per
 * host, so immortal streams starve every other request). On ``stream_end``
 * the hook closes for good instead of reconnect-looping. Pass
 * ``opts.keepAlive`` for subscribers that need a finished task's stream to
 * stay open (the completed-task follow-up chat).
 *
 * When ``taskId`` is ``null`` the hook is inert (no connection opened).
 * The hook does not emit error state — failures are silent and the reader
 * retries on its own.
 */
export function useTaskEvents(
  taskId: string | null,
  onEvent: (event: TaskEvent) => void,
  opts?: { keepAlive?: boolean },
): { lastSeq: number } {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const keepAlive = opts?.keepAlive ?? false;
  const [lastSeq, setLastSeq] = useState(0);
  const lastSeqRef = useRef(0);
  lastSeqRef.current = lastSeq;

  useEffect(() => {
    if (!taskId) return;
    const close = fetchEventSource(
      // Re-read on each (re)connect so the latest seq cursor is threaded.
      () => tasksApi.eventsStreamUrl(taskId, lastSeqRef.current, keepAlive),
      (frame) => {
        if (frame.event === "heartbeat") return; // keep-alive only
        if (frame.event === "stream_end") {
          // Task is terminal and drained — release the connection and stop
          // reconnecting (a retry would just be closed again).
          close();
          return;
        }
        try {
          const parsed = JSON.parse(frame.data) as TaskEvent;
          onEventRef.current(parsed);
          const seq = parsed.sequence ?? 0;
          if (seq > lastSeqRef.current) {
            setLastSeq(seq);
            lastSeqRef.current = seq;
          }
        } catch {
          // Malformed frame — ignore.
        }
      },
      { reconnectDelayMs: 500 }, // matches the server poll cadence
    );
    return close;
  }, [taskId, keepAlive]);

  return { lastSeq };
}
