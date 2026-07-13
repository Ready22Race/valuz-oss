/**
 * Mount-once provider for the unified notification ledger
 * (docs/design/notifications.md).
 *
 * Drives EVERY delivery channel from the ONE store — no parallel subscriptions:
 * - in-app ``toast.info`` per fresh notification (visible only when in-app);
 * - OS native notification + dock bounce + dock badge (the "强提醒" for when the
 *   window is hidden / tray-resident) — Electron only, no-op on web;
 * - navigation on notification click.
 *
 * A "fresh" notification (store ``freshIds`` — set on live ``added`` / a
 * reconnect snapshot for an unseen unread item) alerts exactly once, then is
 * marked alerted so a resolve→re-add or reconnect never re-alerts. Renders
 * ``null``.
 */

import { useEffect, type ReactElement } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { useNotificationInbox, useNotificationStore } from "@valuz/core";

import { notificationDisplay } from "./notification-display";

interface DesktopBridge {
  invoke: <T>(channel: string, args?: unknown) => Promise<T>;
  on: (event: string, handler: (payload: unknown) => void) => void;
  off: (event: string, handler: (payload: unknown) => void) => void;
}

function getBridge(): DesktopBridge | null {
  return (
    (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null
  );
}

export function NotificationProvider(): ReactElement | null {
  const navigate = useNavigate();
  // Singleton subscription (idempotent).
  useNotificationInbox();

  useEffect(() => {
    const bridge = getBridge();

    // Click on a native notification → navigate to its route.
    const onClick = (payload: unknown) => {
      const route = (payload as { route?: string } | null)?.route;
      if (typeof route === "string" && route) navigate(route);
    };
    bridge?.on("notification-clicked", onClick);

    const unsub = useNotificationStore.subscribe((state) => {
      // Badge = unread count (open && not read), always consistent with the list.
      const unread = Array.from(state.entries.values()).filter(
        (e) => e.read_at == null,
      ).length;
      void bridge?.invoke("desktop_set_badge_count", { count: unread });

      if (state.freshIds.size === 0) return;
      for (const id of state.freshIds) {
        const entry = state.entries.get(id);
        if (!entry) {
          state.markAlerted(id);
          continue;
        }
        const d = notificationDisplay(entry);
        // Mark first so a re-entrant subscribe (from markAlerted's own set())
        // doesn't double-fire.
        state.markAlerted(id);
        // In-app toast (seen when the window is focused).
        toast.info(d.title, { description: d.body || undefined });
        // OS notification + dock bounce (seen when it isn't). ``info`` urgency
        // (e.g. a future completion notice) stays in-app only.
        if (bridge && entry.urgency !== "info") {
          void bridge.invoke("desktop_notify", {
            title: d.title,
            body: d.body,
            route: d.route,
            tag: d.tag,
          });
          void bridge.invoke("desktop_bounce", {});
        }
      }
    });

    // Prime the badge from the current snapshot.
    const initial = Array.from(
      useNotificationStore.getState().entries.values(),
    ).filter((e) => e.read_at == null).length;
    void bridge?.invoke("desktop_set_badge_count", { count: initial });

    return () => {
      bridge?.off("notification-clicked", onClick);
      unsub();
    };
  }, [navigate]);

  return null;
}
