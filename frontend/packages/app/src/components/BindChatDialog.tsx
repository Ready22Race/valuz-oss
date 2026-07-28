import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button, FormDialog } from "@valuz/ui";
import { channelsApi, useTranslation, type ChannelChatItem } from "@valuz/core";

interface BindChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  onBound: () => void;
}

/**
 * Flow A of the group ↔ project binding: pick, from the groups the bot has
 * already joined, the one this project stands for. Adding the bot to a group is
 * the half only an IM client can do; Valuz owns which project it means.
 */
export function BindChatDialog({
  open,
  onOpenChange,
  projectId,
  onBound,
}: BindChatDialogProps) {
  const { t } = useTranslation();
  const [chats, setChats] = useState<ChannelChatItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    channelsApi
      .listFeishuChats()
      .then((rows) => {
        if (!cancelled) setChats(rows);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setChats([]);
        toast.error(
          `${t("project.bindChat" as Parameters<typeof t>[0])}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, t]);

  const bind = async (chat: ChannelChatItem) => {
    setSaving(chat.external_chat_id);
    try {
      await channelsApi.bindChatToProject({
        external_chat_id: chat.external_chat_id,
        project_id: projectId,
        external_chat_name: chat.name,
      });
      toast.success(t("project.chatBindingSaved" as Parameters<typeof t>[0]));
      onBound();
      onOpenChange(false);
    } catch (err) {
      toast.error(
        `${t("project.bindChat" as Parameters<typeof t>[0])}: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    } finally {
      setSaving(null);
    }
  };

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("project.bindChatDialogTitle" as Parameters<typeof t>[0])}
      description={t("project.bindChatDialogDesc" as Parameters<typeof t>[0])}
      cancelLabel={t("common.cancel")}
    >
      {loading ? (
        <p className="py-4 text-center text-xs text-ink-meta">
          {t("common.loading")}
        </p>
      ) : chats.length === 0 ? (
        <p className="py-4 text-center text-xs text-ink-meta">
          {t("project.bindChatEmpty" as Parameters<typeof t>[0])}
        </p>
      ) : (
        <div className="flex max-h-[50vh] flex-col gap-1 overflow-y-auto">
          {chats.map((chat) => {
            const boundElsewhere =
              !!chat.bound_project_id && chat.bound_project_id !== projectId;
            const boundHere = chat.bound_project_id === projectId;
            return (
              <div
                key={chat.external_chat_id}
                className="flex items-center gap-2 rounded-lg px-2 py-2 hover:bg-surface-muted"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-ink-heading">
                    {chat.name}
                  </div>
                  {boundElsewhere && (
                    <div className="truncate text-2xs text-ink-meta">
                      {t(
                        "project.bindChatBoundElsewhere" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </div>
                  )}
                </div>
                <Button
                  size="sm"
                  variant={boundHere ? "outline" : "default"}
                  disabled={boundHere || saving !== null}
                  onClick={() => void bind(chat)}
                >
                  {boundHere
                    ? t("project.chatBindingSaved" as Parameters<typeof t>[0])
                    : t("project.bindChat" as Parameters<typeof t>[0])}
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </FormDialog>
  );
}
