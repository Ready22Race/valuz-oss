/**
 * Right-side slide-over listing every open notification (questions + task
 * failures), newest first (docs/design/notifications.md). Store-driven open
 * state so the topbar badge toggles it. Renders one ``NotificationCard`` per
 * entry, dispatched by kind.
 */

import { type ReactElement } from "react";

import {
  useNotifications,
  useNotificationIsOpen,
  useNotificationStore,
  useTranslation,
} from "@valuz/core";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@valuz/ui";
import type { I18nKey } from "@valuz/shared";

import { NotificationCard } from "./NotificationCard";

export function NotificationDrawer(): ReactElement {
  const { t } = useTranslation();
  const isOpen = useNotificationIsOpen();
  const entries = useNotifications();
  const setOpen = useNotificationStore((s) => s.setOpen);

  return (
    <Sheet open={isOpen} onOpenChange={setOpen}>
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-md">
        <SheetHeader className="border-b border-surface-border px-4 py-3">
          <SheetTitle className="text-base">
            {t("notification.inboxTitle" as I18nKey)}
            {entries.length > 0 && (
              <span className="ml-2 text-sm font-normal text-ink-muted">
                · {entries.length}
              </span>
            )}
          </SheetTitle>
        </SheetHeader>

        {entries.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
            <span className="text-3xl opacity-40">🔔</span>
            <p className="text-sm font-medium text-ink-body">
              {t("notification.emptyTitle" as I18nKey)}
            </p>
            <p className="text-xs text-ink-muted">
              {t("notification.emptyHint" as I18nKey)}
            </p>
          </div>
        ) : (
          <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
            {entries.map((entry) => (
              <NotificationCard
                key={entry.id}
                entry={entry}
                onNavigateAway={() => setOpen(false)}
              />
            ))}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
