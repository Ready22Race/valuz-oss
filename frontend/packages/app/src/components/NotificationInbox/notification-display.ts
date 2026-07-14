/**
 * Pure display helpers for notifications (docs/design/notifications.md).
 *
 * The backend stores DATA snapshots (agent_slug for a question, task_title for
 * a failure); the FRONTEND composes the localized display line per kind — so
 * localization lives here, on the surface that renders, and the OS notification
 * / drawer / toast all read one consistent title. Kept React-free for testing.
 */

import { t as _t } from "@valuz/shared/i18n";
import type { I18nKey } from "@valuz/shared";
import type { NotificationEntry } from "@valuz/core";

export interface NotificationDisplay {
  title: string;
  body: string;
  route: string;
  /** Collapses OS-level repeats for the same subject. */
  tag: string;
}

export function notificationDisplay(entry: NotificationEntry): NotificationDisplay {
  const route =
    entry.route ??
    (entry.task_id ? `/tasks/${entry.task_id}` : `/conversation/${entry.session_id ?? ""}`);

  if (entry.kind === "question") {
    return {
      title: _t("notification.notifQuestionTitle" as I18nKey).replace(
        "{agent}",
        entry.title,
      ),
      body: entry.body,
      route,
      tag: `question:${entry.pending_id ?? entry.id}`,
    };
  }
  if (entry.kind === "task_failed") {
    return {
      title: _t("notification.notifFailureTitle" as I18nKey),
      body:
        entry.body ||
        _t("notification.notifFailureBody" as I18nKey).replace(
          "{task}",
          entry.title,
        ),
      route,
      // Per-task tag so repeat failures of one task collapse in the OS.
      tag: `failure:${entry.task_id ?? entry.id}`,
    };
  }
  if (entry.kind === "run_failed") {
    return {
      title: _t("notification.notifRunFailedTitle" as I18nKey).replace(
        "{agent}",
        entry.title || "",
      ),
      body: entry.body,
      route,
      // Per-session tag so repeat failures of one conversation collapse in the OS.
      tag: `run_failed:${entry.session_id ?? entry.id}`,
    };
  }
  // Unknown kind — render whatever the backend snapshotted.
  return { title: entry.title, body: entry.body, route, tag: `notif:${entry.id}` };
}
