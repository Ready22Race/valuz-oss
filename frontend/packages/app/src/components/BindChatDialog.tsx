import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button, FormDialog, Input } from "@valuz/ui";
import {
  channelsApi,
  useTranslation,
  type ChannelChatItem,
  type CreatedChat,
} from "@valuz/core";

interface BindChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  onBound: () => void;
}

/**
 * Flow A of the group ↔ project binding, in two shapes:
 *
 * - pick an existing group the bot has already joined, or
 * - create one here, which sidesteps adding a bot to an existing group — that
 *   depends on a client menu missing or disabled in plenty of setups.
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
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<CreatedChat | null>(null);

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

  useEffect(() => {
    if (!open) {
      setNewName("");
      setCreated(null);
    }
  }, [open]);

  const create = async () => {
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    try {
      const result = await channelsApi.createFeishuChat({
        name,
        project_id: projectId,
      });
      setCreated(result);
      setNewName("");
      toast.success(t("project.createChatDone" as Parameters<typeof t>[0]));
      onBound();
    } catch (err) {
      toast.error(
        `${t("project.createChat" as Parameters<typeof t>[0])}: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    } finally {
      setCreating(false);
    }
  };

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
      <div className="mb-3 flex flex-col gap-2 rounded-lg border border-surface-border p-3">
        <div className="text-xs text-ink-body">
          {t("project.createChatHint" as Parameters<typeof t>[0])}
        </div>
        <div className="flex items-center gap-2">
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={t("project.createChatName" as Parameters<typeof t>[0])}
            className="h-8 flex-1 text-xs"
          />
          <Button
            size="sm"
            disabled={creating || !newName.trim()}
            onClick={() => void create()}
          >
            {t("project.createChat" as Parameters<typeof t>[0])}
          </Button>
        </div>
        {created && (
          <div className="text-xs text-ink-body">
            {created.share_link ? (
              <a
                href={created.share_link}
                target="_blank"
                rel="noreferrer"
                className="text-brand underline"
              >
                {t("project.createChatJoin" as Parameters<typeof t>[0])} ·{" "}
                {created.name}
              </a>
            ) : (
              t("project.createChatLinkMissing" as Parameters<typeof t>[0])
            )}
          </div>
        )}
      </div>

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
