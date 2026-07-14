/**
 * Topbar notification badge (docs/design/notifications.md). Renders nothing at
 * zero — no "0" chip. Click opens the drawer.
 */

import { type ReactElement } from "react";
import { Bell } from "lucide-react";

import {
  useNotificationStore,
  useNotificationTotalCount,
  useNotificationUnreadCount,
  useTranslation,
} from "@valuz/core";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@valuz/ui";
import type { I18nKey } from "@valuz/shared";

export function NotificationBadge(): ReactElement | null {
  const { t } = useTranslation();
  const total = useNotificationTotalCount();
  const unread = useNotificationUnreadCount();
  const setOpen = useNotificationStore((s) => s.setOpen);
  const clearFresh = useNotificationStore((s) => s.clearFresh);

  if (total === 0) return null;

  const handleClick = () => {
    setOpen(true);
    clearFresh();
  };

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={handleClick}
            aria-label={t("notification.inboxTitle" as I18nKey)}
            className="relative flex h-[22px] items-center gap-1 rounded-[5px] px-1.5 text-ink-body transition-colors hover:bg-surface-muted"
          >
            <Bell className="h-3.5 w-3.5" />
            <span
              className={`min-w-[16px] rounded-full px-1 text-center text-2xs font-semibold leading-[16px] ${
                unread > 0
                  ? "bg-brand text-white"
                  : "bg-surface-soft text-ink-muted"
              }`}
            >
              {total}
            </span>
            {unread > 0 && (
              <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-brand" />
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {t("notification.badgeTooltip" as I18nKey).replace(
            "{count}",
            String(total),
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
