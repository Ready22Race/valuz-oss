import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Input,
  Textarea,
  ProjectCard,
  DirectoryPicker,
  DeleteConfirmDialog,
  FormField,
  Button,
  PageLoader,
  EmptyState,
  FormDialog,
  PageHeader,
} from "@valuz/ui";
import { toast } from "sonner";
import { FolderKanban, Plus } from "lucide-react";
import { projectsApi, type ProjectListItem } from "@valuz/core";
import { usePlatform } from "@valuz/app/platform";
import { useTranslation } from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";

export const ProjectsPage = () => {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { selectDirectory } = usePlatform();
  const { setHeader, setHeaderClassName } = useProjectOutlet();
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newRootPath, setNewRootPath] = useState("");
  const [createError, setCreateError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<ProjectListItem | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  const fetchProjects = useCallback(async () => {
    try {
      const data = await projectsApi.list();
      setProjects(data.projects.filter((w) => w.kind === "project"));
    } catch {
      toast.error(t("project.loadFailed" as Parameters<typeof t>[0]));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void Promise.resolve().then(fetchProjects);
  }, [fetchProjects]);

  useEffect(() => {
    if (searchParams.get("create") !== "1") return;
    void Promise.resolve().then(() => setCreateOpen(true));
    setSearchParams((next) => {
      next.delete("create");
      return next;
    }, { replace: true });
  }, [searchParams, setSearchParams]);

  const pageHeader = useMemo(
    () => (
      <PageHeader
        title={t("sidebar.projects" as Parameters<typeof t>[0])}
        description={t("project.createDesc" as Parameters<typeof t>[0])}
        action={
          <Button
            variant="default"
            size="sm"
            onClick={() => setCreateOpen(true)}
          >
            <Plus className="h-3.5 w-3.5" />
            {t("project.create" as Parameters<typeof t>[0])}
          </Button>
        }
      />
    ),
    [t],
  );

  useEffect(() => {
    setHeader(pageHeader);
    setHeaderClassName("h-auto px-5 py-5");
    return () => {
      setHeader(null);
      setHeaderClassName(undefined);
    };
  }, [pageHeader, setHeader, setHeaderClassName]);

  const handleSelectDirectory = async () => {
    const path = await selectDirectory();
    if (path) {
      setNewRootPath(path);
      setCreateError("");
    }
  };

  const handleCreate = async () => {
    const trimmedName = newName.trim();
    const trimmedPath = newRootPath.trim();
    if (!trimmedName || !trimmedPath) return;
    setCreateError("");
    setBusy(true);
    try {
      await projectsApi.create({ name: trimmedName, root_path: trimmedPath });
      toast.success(
        t("project.created" as Parameters<typeof t>[0], { name: trimmedName }),
      );
      setNewName("");
      setNewDesc("");
      setNewRootPath("");
      setCreateOpen(false);
      void fetchProjects();
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : t("common.failed" as Parameters<typeof t>[0]);
      if (message.includes("409")) {
        setCreateError(t("project.dirAlreadyBound" as Parameters<typeof t>[0]));
      } else {
        setCreateError(message);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await projectsApi.delete(deleteTarget.id);
      toast.success(
        t("project.deleted" as Parameters<typeof t>[0], {
          name: deleteTarget.name,
        }),
      );
      setDeleteTarget(null);
      void fetchProjects();
    } catch {
      toast.error(t("common.deleteFailed" as Parameters<typeof t>[0]));
    }
  };

  const renderContent = () => {
    if (loading) {
      return <PageLoader />;
    }

    if (projects.length === 0) {
      return (
        <div className="flex flex-1 justify-center pt-[160px]">
          <EmptyState
            variant="plain"
            title={t("project.createTitle" as Parameters<typeof t>[0])}
            description={t("project.emptyState" as Parameters<typeof t>[0])}
            icon={<FolderKanban className="h-5 w-5" />}
            action={
              <Button
                variant="default"
                size="sm"
                onClick={() => setCreateOpen(true)}
              >
                <Plus className="h-3 w-3" />
                {t("project.create" as Parameters<typeof t>[0])}
              </Button>
            }
          />
        </div>
      );
    }

    return (
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {projects.map((project) => (
          <ProjectCard
            key={project.id}
            name={project.name}
            note={project.root_path || ""}
            href={`/projects/${project.id}`}
            onDelete={() => setDeleteTarget(project)}
            LinkComponent={Link}
          />
        ))}
      </div>
    );
  };

  return (
    <div className="relative -m-6 h-[calc(100%+48px)] overflow-y-auto bg-card sm:-m-7 sm:h-[calc(100%+56px)]">
      <div className="flex min-h-full flex-col px-5 pb-5">
        {renderContent()}
      </div>

      {/* Create Project Dialog */}
      <FormDialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) setCreateError("");
        }}
        title={t("common.create" as Parameters<typeof t>[0])}
        description={t("project.instruction" as Parameters<typeof t>[0])}
        onSubmit={() => void handleCreate()}
        submitLabel={t("common.create" as Parameters<typeof t>[0])}
        cancelLabel={t("common.cancel" as Parameters<typeof t>[0])}
        loading={busy}
      >
        <FormField label={t("common.name" as Parameters<typeof t>[0])}>
          <Input
            placeholder="my-project"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
        </FormField>
        <FormField
          label={t("project.fileTree" as Parameters<typeof t>[0])}
          error={createError || undefined}
        >
          <DirectoryPicker
            value={newRootPath}
            placeholder={t(
              "knowledge.selectDir" as Parameters<typeof t>[0],
            )}
            onBrowse={() => void handleSelectDirectory()}
          />
          <p className="text-xs text-muted-foreground">
            {t("project.fileTree" as Parameters<typeof t>[0])}
          </p>
        </FormField>
        <FormField
          label={t("common.description" as Parameters<typeof t>[0])}
        >
          <Textarea
            placeholder={t(
              "project.instructionPlaceholder" as Parameters<typeof t>[0],
            )}
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
          />
        </FormField>
      </FormDialog>

      {/* Delete Confirmation Dialog */}
      <DeleteConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        itemName={deleteTarget?.name}
        onConfirm={() => void handleDelete()}
      />
    </div>
  );
};
