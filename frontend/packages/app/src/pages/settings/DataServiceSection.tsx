import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, XCircle } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  SettingsRow,
  SettingsSection,
} from "@valuz/ui";
import { settingsApi, useTranslation } from "@valuz/core";
import type {
  DataServiceHealthResponse,
  DataServicePatchPayload,
  DataServiceResponse,
  KernelStoreMode,
} from "@valuz/core";

export const DataServiceSection = () => {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<DataServiceResponse | null>(null);
  // OSS exposes only local + pg (a PG-backed data service). The "remote"
  // (HTTP+JWT) reach is a SaaS/cloud-sandbox deployment binding, not a
  // user-facing OSS choice — so a persisted "remote" collapses to "pg" here.
  const [mode, setMode] = useState<Exclude<KernelStoreMode, "remote">>("local");
  const [durableUrl, setDurableUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [health, setHealth] = useState<DataServiceHealthResponse | null>(null);
  const [checking, setChecking] = useState(false);
  const [ops, setOps] = useState<string[] | null>(null);
  const [showOps, setShowOps] = useState(false);

  const toggleOps = useCallback(async () => {
    if (showOps) {
      setShowOps(false);
      return;
    }
    setShowOps(true);
    if (ops) return;
    try {
      const schema = await settingsApi.getDataServiceOpenApi();
      setOps(
        Object.keys(schema.paths ?? {})
          .filter((p) => p.startsWith("/rpc/"))
          .map((p) => p.replace("/rpc/", ""))
          .sort(),
      );
    } catch {
      setOps([]);
      toast.error(t("settings.dataService.openApiError"));
    }
  }, [showOps, ops, t]);

  const checkHealth = useCallback(async () => {
    setChecking(true);
    try {
      setHealth(await settingsApi.getDataServiceHealth());
    } catch {
      setHealth({ status: "error", backend: "local", detail: "health check failed" });
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void settingsApi
      .getDataService()
      .then((c) => {
        if (cancelled) return;
        setCfg(c);
        // A SaaS-bound "remote" maps to the user-facing "pg" choice.
        setMode(c.kernel_store === "local" ? "local" : "pg");
        setDurableUrl(c.durable_database_url);
      })
      .catch(() => {
        // leave defaults; the form still works
      });
    void checkHealth();
    return () => {
      cancelled = true;
    };
  }, [checkHealth]);

  const save = async () => {
    setSaving(true);
    try {
      const payload: DataServicePatchPayload = { kernel_store: mode };
      if (mode === "pg") {
        payload.durable_database_url = durableUrl.trim();
      }
      const next = await settingsApi.patchDataService(payload);
      setCfg(next);
      toast.success(t("settings.dataService.saved"));
      void checkHealth();
    } catch {
      toast.error(t("settings.dataService.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const backendName = (b: KernelStoreMode) =>
    t(
      b === "pg"
        ? "settings.dataService.backendPg"
        : b === "remote"
          ? "settings.dataService.backendRemote"
          : "settings.dataService.backendLocal",
    );

  const divider = <div className="my-5 h-px bg-surface-muted dark:bg-surface-border" />;

  return (
    <SettingsSection
      title={t("settings.dataService.title")}
      desc={t("settings.dataService.desc")}
    >
      {cfg?.restart_required ? (
        <div className="mb-5 mt-5 flex items-start gap-2 rounded-lg border border-warning-light bg-warning-light px-3 py-2.5 text-xs text-warning-text">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{t("settings.dataService.restartRequired")}</span>
        </div>
      ) : null}

      {/* ── Configuration ── */}
      <div className="mb-2 mt-5 text-sm font-medium text-ink-heading">
        {t("settings.dataService.configTitle")}
      </div>
      <Card className="mb-5 rounded-xl border-0 bg-card shadow-sm">
        <CardContent className="py-5">
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.dataService.modeLabel")}
          >
            <Select value={mode} onValueChange={(v) => setMode(v as "local" | "pg")}>
              <SelectTrigger size="sm" className="h-8 w-auto min-w-[180px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local">
                  {t("settings.dataService.modeLocal")}
                </SelectItem>
                <SelectItem value="pg">{t("settings.dataService.modePg")}</SelectItem>
              </SelectContent>
            </Select>
          </SettingsRow>

          {mode === "pg" ? (
            <>
              {divider}
              <div className="px-0">
                <div className="text-sm font-medium text-ink-heading">
                  {t("settings.dataService.pgUrlLabel")}
                </div>
                <Input
                  value={durableUrl}
                  onChange={(e) => setDurableUrl(e.target.value)}
                  placeholder="postgresql+asyncpg://user:pass@host:5432/db"
                  className="mt-2.5 max-w-xl font-mono text-xs"
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
            </>
          ) : null}

          <div className="mt-5 flex justify-end">
            <Button size="sm" onClick={() => void save()} loading={saving}>
              {t("settings.dataService.save")}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* ── Status ── */}
      <div className="mb-2 text-sm font-medium text-ink-heading">
        {t("settings.dataService.statusTitle")}
      </div>
      <Card className="mb-5 rounded-xl border-0 bg-card shadow-sm">
        <CardContent className="py-5">
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.dataService.healthLabel")}
            desc={health?.detail || undefined}
          >
            <div className="flex items-center gap-2">
              {checking ? (
                <Badge variant="secondary" className="gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                </Badge>
              ) : health ? (
                <Badge
                  variant={health.status === "ok" ? "success" : "error"}
                  className="gap-1"
                >
                  {health.status === "ok" ? (
                    <CheckCircle2 className="h-3 w-3" />
                  ) : (
                    <XCircle className="h-3 w-3" />
                  )}
                  {backendName(health.backend)}{" "}
                  {t(
                    health.status === "ok"
                      ? "settings.dataService.healthOk"
                      : "settings.dataService.healthError",
                  )}
                </Badge>
              ) : null}
              <Button
                variant="ghost"
                size="xs"
                onClick={() => void checkHealth()}
                disabled={checking}
              >
                <RefreshCw className="mr-1 h-3 w-3" />
                {t("settings.dataService.healthRefresh")}
              </Button>
            </div>
          </SettingsRow>

          {divider}

          <SettingsRow
            className="px-0 py-0"
            label={t("settings.dataService.openApiLabel")}
          >
            <Button variant="ghost" size="xs" onClick={() => void toggleOps()}>
              {showOps
                ? t("settings.dataService.openApiHide")
                : t("settings.dataService.openApiView")}
            </Button>
          </SettingsRow>
          {showOps && ops ? (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {ops.map((op) => (
                <code
                  key={op}
                  className="rounded-md bg-surface-soft px-1.5 py-0.5 font-mono text-[11px] text-ink-body"
                >
                  /rpc/{op}
                </code>
              ))}
            </div>
          ) : null}
        </CardContent>
      </Card>
    </SettingsSection>
  );
};
