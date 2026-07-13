/**
 * Singleton hook that maintains the global notification subscription
 * (docs/design/notifications.md).
 *
 * Mount-once (idempotent via the store's ``_inited`` flag): one SSE stream +
 * one snapshot per process regardless of how many components call it. Mounted
 * at the AppShell level (``NotificationProvider``).
 *
 * Wire protocol (``GET /v1/notifications/stream``):
 * - ``snapshot`` ({entries,unread}) → store.reset
 * - ``added``   ({entry})           → store.add
 * - ``updated`` ({entry})           → store.update  (read-state change)
 * - ``resolved`` ({id})             → store.remove
 * Reads over ``fetch`` (not EventSource) so the request carries auth; a stuck
 * stream is bounded by a low-frequency REST poll backstop.
 */

import { useEffect } from "react";

import {
  notificationsApi,
  type NotificationEntry,
} from "../api/notifications-api";
import { fetchEventSource } from "../api/fetch-event-source";
import { useNotificationStore } from "../store/notification-store";

let _closeStream: (() => void) | null = null;
let _pollTimer: ReturnType<typeof setInterval> | null = null;

const POLL_BACKSTOP_MS = 60_000;

async function _init(): Promise<void> {
  const store = useNotificationStore.getState();
  if (store._inited) return;
  store.setInited();

  try {
    const res = await notificationsApi.fetchOpen();
    useNotificationStore.getState().reset(res.entries);
  } catch {
    // Non-fatal — the SSE snapshot frame will populate the store.
  }

  if (_closeStream) return;
  _closeStream = fetchEventSource(
    () => notificationsApi.streamUrl(),
    (frame) => {
      try {
        if (frame.event === "snapshot") {
          const data = JSON.parse(frame.data) as {
            payload?: { entries?: NotificationEntry[] };
          };
          useNotificationStore.getState().reset(data.payload?.entries ?? []);
        } else if (frame.event === "added") {
          const data = JSON.parse(frame.data) as {
            payload?: { entry?: NotificationEntry };
          };
          if (data.payload?.entry) {
            useNotificationStore.getState().add(data.payload.entry);
          }
        } else if (frame.event === "updated") {
          const data = JSON.parse(frame.data) as {
            payload?: { entry?: NotificationEntry };
          };
          if (data.payload?.entry) {
            useNotificationStore.getState().update(data.payload.entry);
          }
        } else if (frame.event === "resolved") {
          const data = JSON.parse(frame.data) as { payload?: { id?: string } };
          if (data.payload?.id) {
            useNotificationStore.getState().remove(data.payload.id);
          }
        }
        // "heartbeat"/"ping" and anything else: ignore.
      } catch {
        // Malformed frame — ignore.
      }
    },
  );

  _pollTimer ??= setInterval(() => {
    notificationsApi
      .fetchOpen()
      .then((res) => useNotificationStore.getState().reset(res.entries))
      .catch(() => {
        // Non-fatal — next tick / SSE snapshot retries.
      });
  }, POLL_BACKSTOP_MS);
}

/** Idempotent mount hook. Shares the singleton subscription. (Named distinctly
 *  from the ``useNotifications`` store selector.) */
export function useNotificationInbox(): void {
  useEffect(() => {
    void _init();
    // No teardown — the subscription lives for the app's whole lifetime.
  }, []);
}
