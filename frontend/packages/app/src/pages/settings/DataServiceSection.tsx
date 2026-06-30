import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, XCircle } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
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
  const [mode, setMode] = useState<KernelStoreMode>("local");
  const [durableUrl, setDurableUrl] = useState("");
  const [apiUrl, setApiUrl] = useState("");
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [health, setHealth] = useState<DataServiceHealthResponse | null>(null);
  const [checking, setChecking] = useState(false);

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
        setMode(c.kernel_store);
        setDurableUrl(c.durable_database_url);
        setApiUrl(c.data_api_url);
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
      if (mode === "remote") {
        payload.data_api_url = apiUrl.trim();
        if (token.trim()) payload.data_api_token = token.trim();
      }
      const next = await settingsApi.patchDataService(payload);
      setCfg(next);
      setToken("");
      toast.success(t("settings.dataService.saved"));
      void checkHealth();
    } catch {
      toast.error(t("settings.dataService.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection
      title={t("settings.dataService.title")}
      desc={t("settings.dataService.desc")}
    >
      {cfg?.restart_required ? (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-warning-light bg-warning-light px-3 py-2 text-xs text-warning-text">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{t("settings.dataService.restartRequired")}</span>
        </div>
      ) : null}

      {/* Backend health */}
      <div className="mb-4 flex items-center gap-2">
        <span className="text-xs text-ink-meta">
          {t("settings.dataService.healthLabel")}
        </span>
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
            {health.backend} ·{" "}
            {t(
              health.status === "ok"
                ? "settings.dataService.healthOk"
                : "settings.dataService.healthError",
            )}
          </Badge>
        ) : null}
        {health?.detail ? (
          <span className="truncate text-xs text-ink-meta" title={health.detail}>
            {health.detail}
          </span>
        ) : null}
        <Button
          variant="ghost"
          size="xs"
          className="ml-auto"
          onClick={() => void checkHealth()}
          disabled={checking}
        >
          <RefreshCw className="mr-1 h-3 w-3" />
          {t("settings.dataService.healthRefresh")}
        </Button>
      </div>

      <Card className="rounded-xl shadow-xs">
        <CardContent className="space-y-5 py-5">
          {/* Mode */}
          <div className="space-y-1.5">
            <Label>{t("settings.dataService.modeLabel")}</Label>
            <p className="text-xs text-ink-body">
              {t("settings.dataService.modeDesc")}
            </p>
            <Select
              value={mode}
              onValueChange={(v) => setMode(v as KernelStoreMode)}
            >
              <SelectTrigger className="w-full max-w-md">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local">
                  {t("settings.dataService.modeLocal")}
                </SelectItem>
                <SelectItem value="pg">
                  {t("settings.dataService.modePg")}
                </SelectItem>
                <SelectItem value="remote">
                  {t("settings.dataService.modeRemote")}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* pg: Postgres DSN */}
          {mode === "pg" ? (
            <div className="space-y-1.5">
              <Label>{t("settings.dataService.pgUrlLabel")}</Label>
              <p className="text-xs text-ink-body">
                {t("settings.dataService.pgUrlDesc")}
              </p>
              <Input
                value={durableUrl}
                onChange={(e) => setDurableUrl(e.target.value)}
                placeholder="postgresql+asyncpg://user:pass@host:5432/db"
                className="max-w-xl font-mono text-xs"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
          ) : null}

          {/* remote: data-service URL + token */}
          {mode === "remote" ? (
            <>
              <div className="space-y-1.5">
                <Label>{t("settings.dataService.remoteUrlLabel")}</Label>
                <Input
                  value={apiUrl}
                  onChange={(e) => setApiUrl(e.target.value)}
                  placeholder="http://127.0.0.1:8400"
                  className="max-w-xl font-mono text-xs"
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
              <div className="space-y-1.5">
                <Label>{t("settings.dataService.tokenLabel")}</Label>
                <p className="text-xs text-ink-body">
                  {cfg?.token_set
                    ? t("settings.dataService.tokenSet")
                    : t("settings.dataService.tokenDesc")}
                </p>
                <Input
                  type="password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder={cfg?.token_set ? "••••••••" : ""}
                  className="max-w-xl font-mono text-xs"
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
            </>
          ) : null}

          <div className="flex justify-end pt-1">
            <Button size="sm" onClick={() => void save()} loading={saving}>
              {t("settings.dataService.save")}
            </Button>
          </div>
        </CardContent>
      </Card>
    </SettingsSection>
  );
};
