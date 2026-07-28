import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ExternalLink } from "lucide-react";
import { Button, FormDialog, Input, StatusPill } from "@valuz/ui";
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
      {/* Create — the path that avoids adding a bot to an existing group. */}
      <div className="flex flex-col gap-2">
        <div className="text-2xs font-medium tracking-wide text-ink-meta uppercase">
          {t("project.createChat" as Parameters<typeof t>[0])}
        </div>
        <div className="flex items-center gap-2">
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={t("project.createChatName" as Parameters<typeof t>[0])}
            className="h-9 flex-1"
            onKeyDown={(e) => {
              if (e.key === "Enter" && newName.trim() && !creating) {
                e.preventDefault();
                void create();
              }
            }}
          />
          <Button
            size="sm"
            variant="outline"
            disabled={creating || !newName.trim()}
            onClick={() => void create()}
          >
            {t("common.create")}
          </Button>
        </div>
        <p className="text-2xs text-ink-meta">
          {t("project.createChatHint" as Parameters<typeof t>[0])}
        </p>
        {created && (
          // The bot created the group, so nobody else is in it yet — the join
          // link is the next action, not a footnote.
          <div className="flex items-center justify-between gap-2 rounded-md bg-surface-muted px-2.5 py-2">
            <span className="min-w-0 truncate text-xs text-ink-body">
              {created.name}
            </span>
            {created.share_link ? (
              <Button size="sm" variant="outline" asChild>
                <a href={created.share_link} target="_blank" rel="noreferrer">
                  {t("project.createChatJoin" as Parameters<typeof t>[0])}
                  <ExternalLink />
                </a>
              </Button>
            ) : (
              <span className="text-2xs text-ink-meta">
                {t("project.createChatLinkMissing" as Parameters<typeof t>[0])}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Existing groups the bot has already joined. */}
      <div className="mt-4 flex flex-col gap-2">
        <div className="text-2xs font-medium tracking-wide text-ink-meta uppercase">
          {t("project.bindChatExistingTitle" as Parameters<typeof t>[0])}
        </div>
        {loading ? (
          <p className="py-6 text-center text-xs text-ink-meta">
            {t("common.loading")}
          </p>
        ) : chats.length === 0 ? (
          <p className="py-6 text-center text-xs text-ink-meta">
            {t("project.bindChatEmpty" as Parameters<typeof t>[0])}
          </p>
        ) : (
          <div className="max-h-[40vh] divide-y divide-surface-border overflow-y-auto rounded-md border border-surface-border">
            {chats.map((chat) => {
              const boundElsewhere =
                !!chat.bound_project_id && chat.bound_project_id !== projectId;
              const boundHere = chat.bound_project_id === projectId;
              return (
                <div
                  key={chat.external_chat_id}
                  className="flex items-center gap-2 px-2.5 py-2 transition-colors hover:bg-surface-muted"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs text-ink-heading">
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
                  {boundHere ? (
                    // State, not an action — the tag taxonomy carries it; a
                    // disabled button only looked like one you may not press.
                    <StatusPill
                      status="connected"
                      label={t(
                        "project.chatBindingSaved" as Parameters<typeof t>[0],
                      )}
                      className="shrink-0"
                    />
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={saving !== null}
                      onClick={() => void bind(chat)}
                    >
                      {t("project.bindChatShort" as Parameters<typeof t>[0])}
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </FormDialog>
  );
}
