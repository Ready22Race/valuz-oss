import { useState } from "react";
import { toast } from "sonner";
import { Download, PackagePlus } from "lucide-react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@valuz/ui";
import { projectsApi, useTranslation } from "@valuz/core";

type Tx = ReturnType<typeof useTranslation>["t"];
const k = (key: string) => key as Parameters<Tx>[0];

/**
 * Confirm-and-download flow for a single project export into a
 * ``.valuz-project`` bundle. The project's name is already known (no
 * collection header to fill), so this is a one-button confirmation.
 * On confirm we fetch the blob and trigger an anchor-click download.
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
  const [busy, setBusy] = useState(false);

  const handleExport = async () => {
    setBusy(true);
    try {
      const { blob, filename } = await projectsApi.exportProject(projectId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
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

        <div className="space-y-2 py-1">
          <div className="text-sm font-medium text-ink-body">
            {projectName}
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
