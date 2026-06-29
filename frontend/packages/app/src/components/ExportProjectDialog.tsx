import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Download, PackagePlus } from "lucide-react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Input,
} from "@valuz/ui";
import { projectsApi, useTranslation } from "@valuz/core";

type Tx = ReturnType<typeof useTranslation>["t"];
const k = (key: string) => key as Parameters<Tx>[0];

/** Turn the user's filename input into a ``<stem>.valuzpack`` download name —
 *  trims, drops a pack extension the user may have typed (so it isn't doubled),
 *  and falls back to the project name when empty. */
function toValuzpackFilename(input: string, fallback: string): string {
  let stem = input.trim() || fallback.trim() || "project";
  stem = stem.replace(/\.(valuzpack|valuz-project|zip)$/i, "").trim();
  return `${stem || "project"}.valuzpack`;
}

/**
 * Name-and-download flow for a single project export into a ``.valuzpack``
 * bundle (the unified pack format, project target). The user picks the export
 * file name (defaults to the project name); the downloaded file is
 * ``<name>.valuzpack``. On confirm we fetch the blob and anchor-click download.
 */
export function ExportProjectDialog({
  projectId,
  projectName,
  open,
  onOpenChange,
}: {
  projectId: string;
  projectName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState(false);

  // Default the file name to the project name each time the dialog opens
  // (deferred off the synchronous effect body to satisfy the cascading-render
  // lint, mirroring ExportPackDialog).
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (!cancelled) setFileName(projectName);
    });
    return () => {
      cancelled = true;
    };
  }, [open, projectName]);

  const handleExport = async () => {
    setBusy(true);
    try {
      const { blob } = await projectsApi.exportProject(projectId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = toValuzpackFilename(fileName, projectName);
      a.click();
      URL.revokeObjectURL(url);
      toast.success(t(k("project.exportDone"), { name: projectName }));
      onOpenChange(false);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t(k("project.exportFailed")),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <PackagePlus className="h-4 w-4 text-brand" />
            {t(k("project.exportTitle"))}
          </DialogTitle>
          <DialogDescription>
            {t(k("project.exportSub"), { name: projectName })}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-1">
          <div className="space-y-1.5">
            <label className="text-xs text-ink-meta">
              {t(k("project.exportFileName"))}
            </label>
            <div className="flex items-center gap-2">
              <Input
                value={fileName}
                onChange={(e) => setFileName(e.target.value)}
                autoFocus
              />
              <span className="shrink-0 text-xs text-ink-meta">.valuzpack</span>
            </div>
          </div>
          <p className="text-xs leading-5 text-ink-meta">
            {t(k("project.exportNote"))}
          </p>
        </div>

        <div className="flex justify-end gap-2">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            {t(k("project.cancel"))}
          </Button>
          <Button
            onClick={() => void handleExport()}
            disabled={busy}
            loading={busy}
          >
            <Download className="h-3.5 w-3.5" />
            {busy ? t(k("project.exporting")) : t(k("project.export"))}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
