import { useState } from "react";
import { Button, Textarea } from "@valuz/ui";
import { useTranslation, type QueuedInput } from "@valuz/core";
import { Check, Paperclip, Pencil, Play, Trash2, X } from "lucide-react";

interface QueuedInputsBarProps {
  queue: QueuedInput[];
  paused: boolean;
  onEdit: (queueId: string, text: string) => void | Promise<void>;
  onDelete: (queueId: string) => void | Promise<void>;
  onResume: () => void | Promise<void>;
}

/**
 * Pending follow-up inputs queued while a turn is running, rendered above the
 * Composer (docs/design/session-input-queue.md). Each item drains FIFO after
 * the active turn; queued items can be edited or deleted. After an interrupt
 * the queue is soft-paused and shows a "Continue" affordance.
 */
export const QueuedInputsBar = ({
  queue,
  paused,
  onEdit,
  onDelete,
  onResume,
}: QueuedInputsBarProps) => {
  const { t } = useTranslation();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

  if (queue.length === 0) return null;

  const startEdit = (id: string, text: string) => {
    setEditingId(id);
    setEditText(text);
  };

  const saveEdit = async (id: string) => {
    const text = editText.trim();
    if (text) await onEdit(id, text);
    setEditingId(null);
  };

  return (
    // Match the Composer's outer box (``mx-auto max-w-[760px]``) so the queue
    // lines up with the input below it instead of spanning the full column.
    <div className="mx-auto mb-1.5 w-full max-w-[760px] space-y-1.5">
      <div className="flex items-center justify-between px-1">
        <span className="text-[11px] text-ink-meta">
          {t("common.queueRunsAfter")} ({queue.length})
        </span>
        {paused && (
          <Button
            type="button"
            size="xs"
            variant="outline"
            className="gap-1"
            onClick={() => void onResume()}
          >
            <Play className="size-3" />
            {t("common.queueContinue")}
          </Button>
        )}
      </div>

      {queue.map((item) => (
        <div
          key={item.id}
          className="rounded-lg border border-surface-border bg-surface-soft px-3 py-2"
        >
          {editingId === item.id ? (
            <div className="space-y-1.5">
              <Textarea
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                rows={2}
                className="resize-none text-[13px]"
              />
              <div className="flex justify-end gap-1.5">
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  onClick={() => setEditingId(null)}
                  aria-label={t("common.cancel")}
                >
                  <X className="size-3" />
                </Button>
                <Button
                  type="button"
                  size="xs"
                  onClick={() => void saveEdit(item.id)}
                  aria-label={t("common.save")}
                >
                  <Check className="size-3" />
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <p className="line-clamp-2 text-[13px] text-ink-body">
                  {item.text}
                </p>
                {item.status === "blocked" && (
                  <p className="mt-0.5 text-[11px] text-error-text">
                    {item.error_message || t("common.queueBlocked")}
                  </p>
                )}
                {item.attachment_count > 0 && (
                  <p className="mt-0.5 flex items-center gap-1 text-[11px] text-ink-meta">
                    <Paperclip className="size-3" />
                    {item.attachment_count}
                  </p>
                )}
              </div>
              <div className="flex shrink-0 gap-0.5">
                {item.status === "queued" && (
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="size-6"
                    onClick={() => startEdit(item.id, item.text)}
                    aria-label={t("common.edit")}
                  >
                    <Pencil className="size-3" />
                  </Button>
                )}
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="size-6"
                  onClick={() => void onDelete(item.id)}
                  aria-label={t("common.delete")}
                >
                  <Trash2 className="size-3" />
                </Button>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
};
