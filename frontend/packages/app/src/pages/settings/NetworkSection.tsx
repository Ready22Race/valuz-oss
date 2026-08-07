import { useCallback, useEffect, useMemo, useState } from "react";
import { Copy, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import {
  Button,
  Card,
  CardContent,
  SettingsRow,
  SettingsSection,
} from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import { buildEgressDiagnosticsExport } from "./network-diagnostics";

type EgressMode = "auto" | "direct" | "off";
type Health = "unknown" | "healthy" | "degraded" | "failed";

interface EgressStatus {
  mode: EgressMode;
  enabled: boolean;
  started: boolean;
  emergencyOverride: boolean;
  snapshotCount: number;
  diagnosticEventCount: number;
  lastErrorCode?: string;
}

interface EgressSnapshot {
  runtime: string;
  frontend: string;
  targetOrigin: string;
  mode: EgressMode;
  route: string;
  health: Health;
  source?: string;
  redactedProxy?: string;
  resolveMs?: number;
  connectMs?: number;
  fallbackCount: number;
  lastErrorCode?: string;
  updatedAt: number;
}

interface DesktopBridge {
  invoke<T>(channel: string, payload?: Record<string, unknown>): Promise<T>;
  on(event: string, handler: (payload: unknown) => void): void;
  off(event: string, handler: (payload: unknown) => void): void;
}

const bridge = (): DesktopBridge | null =>
  (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null;

const overallHealth = (snapshots: EgressSnapshot[]): Health => {
  if (snapshots.some((item) => item.health === "failed")) return "failed";
  if (snapshots.some((item) => item.health === "degraded")) return "degraded";
  if (snapshots.some((item) => item.health === "healthy")) return "healthy";
  return "unknown";
};

export const NetworkSection = () => {
  const { t } = useTranslation();
  const [status, setStatus] = useState<EgressStatus | null>(null);
  const [snapshots, setSnapshots] = useState<EgressSnapshot[]>([]);
  const [diagnostics, setDiagnostics] = useState<unknown[]>([]);
  const [runtimePhases, setRuntimePhases] = useState<unknown[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyMode, setBusyMode] = useState<EgressMode | null>(null);

  const load = useCallback(async (silent = false) => {
    const desktop = bridge();
    if (!desktop) {
      setLoading(false);
      return;
    }
    try {
      const [nextStatus, nextSnapshots, nextDiagnostics, nextPhases] =
        await Promise.all([
          desktop.invoke<EgressStatus>("egress_get_status"),
          desktop.invoke<EgressSnapshot[]>("egress_get_snapshots"),
          desktop.invoke<unknown[]>("egress_get_diagnostics"),
          desktop.invoke<unknown[]>("egress_get_runtime_phases"),
        ]);
      setStatus(nextStatus);
      setSnapshots(nextSnapshots);
      setDiagnostics(nextDiagnostics);
      setRuntimePhases(nextPhases);
    } catch {
      if (!silent) toast.error(t("settings.network.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
    const desktop = bridge();
    if (!desktop) return;
    const onChange = () => void load(true);
    desktop.on("egress-status-changed", onChange);
    const poll = window.setInterval(() => void load(true), 3_000);
    return () => {
      window.clearInterval(poll);
      desktop.off("egress-status-changed", onChange);
    };
  }, [load]);

  const health = useMemo(
    () => (status?.lastErrorCode ? "failed" : overallHealth(snapshots)),
    [snapshots, status?.lastErrorCode],
  );
  const healthLabel = t(`settings.network.health.${health}`);

  const changeMode = async (mode: EgressMode) => {
    const desktop = bridge();
    if (!desktop || !status || status.mode === mode) return;
    if (mode === "off" && !window.confirm(t("settings.network.offConfirm"))) {
      return;
    }
    setBusyMode(mode);
    try {
      const next = await desktop.invoke<EgressStatus>("egress_set_mode", {
        mode,
      });
      setStatus(next);
      toast.success(
        mode === "direct"
          ? t("settings.network.directEnabled")
          : mode === "off"
            ? t("settings.network.offEnabled")
            : t("settings.network.autoEnabled"),
      );
      await load();
    } catch (error) {
      toast.error(
        error instanceof Error && error.message.includes("locked_by_environment")
          ? t("settings.network.environmentLocked")
          : t("settings.network.changeFailed"),
      );
    } finally {
      setBusyMode(null);
    }
  };

  const copyDiagnostics = async () => {
    const payload = JSON.stringify(
      buildEgressDiagnosticsExport(
        status,
        snapshots,
        diagnostics,
        runtimePhases,
      ),
      null,
      2,
    );
    try {
      await navigator.clipboard.writeText(payload);
      toast.success(t("settings.network.copied"));
    } catch {
      toast.error(t("settings.network.copyFailed"));
    }
  };

  if (!bridge()) {
    return (
      <SettingsSection
        title={t("settings.network.title")}
        desc={t("settings.network.desc")}
      >
        <p className="text-sm text-ink-meta">
          {t("settings.network.desktopOnly")}
        </p>
      </SettingsSection>
    );
  }

  return (
    <SettingsSection
      title={t("settings.network.title")}
      desc={t("settings.network.desc")}
    >
      <Card className="mb-5 rounded-xl shadow-xs">
        <CardContent className="py-5">
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.network.statusLabel")}
            desc={
              loading
                ? t("settings.network.loading")
                : `${healthLabel} · ${t(`settings.network.mode.${status?.mode ?? "off"}`)}`
            }
          >
            <Button
              variant="outline"
              size="sm"
              disabled={loading}
              onClick={() => void load()}
            >
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              {t("settings.network.refresh")}
            </Button>
          </SettingsRow>
        </CardContent>
      </Card>

      {status && !status.enabled ? (
        <Card className="mb-5 rounded-xl shadow-xs">
          <CardContent className="py-4 text-sm text-ink-meta">
            {t("settings.network.canaryDisabled")}
          </CardContent>
        </Card>
      ) : (
        <Card className="mb-5 rounded-xl shadow-xs">
          <CardContent className="divide-y divide-surface-border py-1">
            <SettingsRow
              className="px-0"
              label={t("settings.network.autoLabel")}
              desc={t("settings.network.autoDesc")}
            >
              <Button
                size="sm"
                variant={status?.mode === "auto" ? "default" : "outline"}
                disabled={status?.emergencyOverride || busyMode !== null}
                loading={busyMode === "auto"}
                onClick={() => void changeMode("auto")}
              >
                {status?.mode === "auto"
                  ? t("settings.network.current")
                  : t("settings.network.use")}
              </Button>
            </SettingsRow>
            <SettingsRow
              className="px-0"
              label={t("settings.network.directLabel")}
              desc={t("settings.network.directDesc")}
            >
              <Button
                size="sm"
                variant={status?.mode === "direct" ? "default" : "outline"}
                disabled={status?.emergencyOverride || busyMode !== null}
                loading={busyMode === "direct"}
                onClick={() => void changeMode("direct")}
              >
                {status?.mode === "direct"
                  ? t("settings.network.current")
                  : t("settings.network.useTemporarily")}
              </Button>
            </SettingsRow>
            <SettingsRow
              className="px-0"
              label={t("settings.network.offLabel")}
              desc={t("settings.network.offDesc")}
            >
              <Button
                size="sm"
                variant={status?.mode === "off" ? "default" : "outline"}
                disabled={busyMode !== null}
                loading={busyMode === "off"}
                onClick={() => void changeMode("off")}
              >
                {status?.mode === "off"
                  ? t("settings.network.current")
                  : t("settings.network.enableCompatibility")}
              </Button>
            </SettingsRow>
          </CardContent>
        </Card>
      )}

      {status?.emergencyOverride && (
        <p className="mb-5 text-xs text-warning-text">
          {t("settings.network.environmentLocked")}
        </p>
      )}

      <details className="rounded-xl border border-surface-border bg-card px-4 py-3">
        <summary className="cursor-pointer text-sm font-medium text-ink-heading">
          {t("settings.network.advanced")}
        </summary>
        <div className="mt-3 space-y-3 text-xs text-ink-meta">
          <p>
            {t("settings.network.samples", {
              snapshots: String(snapshots.length),
              events: String(diagnostics.length),
            })}
          </p>
          {snapshots.slice(-10).map((item, index) => (
            <div
              key={`${item.runtime}-${item.targetOrigin}-${index}`}
              className="rounded-lg bg-surface-muted/50 px-3 py-2"
            >
              <div className="font-medium text-ink-heading">
                {item.runtime} · {item.health} · {item.route}
              </div>
              <div className="mt-0.5 break-all">{item.targetOrigin}</div>
              {(item.resolveMs !== undefined || item.connectMs !== undefined) && (
                <div className="mt-0.5">
                  resolve {item.resolveMs ?? "—"} ms · connect{" "}
                  {item.connectMs ?? "—"} ms
                </div>
              )}
            </div>
          ))}
          <Button variant="outline" size="sm" onClick={() => void copyDiagnostics()}>
            <Copy className="mr-1.5 h-3.5 w-3.5" />
            {t("settings.network.copyDiagnostics")}
          </Button>
        </div>
      </details>
    </SettingsSection>
  );
};
