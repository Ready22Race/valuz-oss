import { useState, useEffect } from "react";
import { toast } from "sonner";
import {
  Check,
  Loader2,
  Monitor,
  Radio,
  CheckCircle2,
  AlertTriangle,
  XCircle,
} from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  Input,
  SettingsRow,
  SettingsSection,
  cn,
} from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import {
  settingsApi,
  type KernelEndpointConfig,
  type KernelEndpointTestResult,
} from "@valuz/core";

type Mode = "inprocess" | "http";

/**
 * Settings → Gateway: configure where the agent KERNEL runs — in-process
 * (default) or on a remote host / cloud sandbox over HTTP ("configure sandbox
 * address"). Modelled on Hermes Desktop's gateway UX (local/remote cards +
 * Test), but this is the host→kernel link (not a shell→backend gateway), and
 * the Test probes BOTH directions so a remote kernel that can't reach the host
 * back (the ④ loopback trap) doesn't show a false green.
 */
export const GatewaySection = () => {
  const { t } = useTranslation();
  const [mode, setMode] = useState<Mode>("inprocess");
  const [url, setUrl] = useState("");
  const [externalUrl, setExternalUrl] = useState("");
  const [token, setToken] = useState(""); // empty = keep the stored token
  const [tokenPresent, setTokenPresent] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<KernelEndpointTestResult | null>(null);

  const apply = (c: KernelEndpointConfig) => {
    setMode(c.mode);
    setUrl(c.url);
    setExternalUrl(c.host_external_url ?? "");
    setTokenPresent(c.token_present);
  };

  useEffect(() => {
    // Soft-fail: a backend that doesn't yet expose the endpoint leaves the
    // defaults (in-process) rather than erroring the whole settings page.
    settingsApi
      .getKernelEndpoint()
      .then(apply)
      .catch(() => undefined);
  }, []);

  const onTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      setResult(
        await settingsApi.testKernelEndpoint({
          url: url || undefined,
          token: token || undefined,
          host_external_url: externalUrl || undefined,
        }),
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("settings.gateway.testFailed"));
    } finally {
      setTesting(false);
    }
  };

  const onSave = async () => {
    setSaving(true);
    try {
      const c = await settingsApi.patchKernelEndpoint(
        mode === "http"
          ? {
              mode,
              url,
              host_external_url: externalUrl,
              ...(token ? { token } : {}),
            }
          : { mode },
      );
      apply(c);
      setToken("");
      toast.success(t("settings.gateway.saved"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("settings.gateway.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const modes: { id: Mode; icon: typeof Monitor; title: string; desc: string }[] = [
    {
      id: "inprocess",
      icon: Monitor,
      title: t("settings.gateway.localTitle"),
      desc: t("settings.gateway.localDesc"),
    },
    {
      id: "http",
      icon: Radio,
      title: t("settings.gateway.remoteTitle"),
      desc: t("settings.gateway.remoteDesc"),
    },
  ];

  return (
    <SettingsSection
      title={t("settings.tab.gateway.label")}
      desc={t("settings.gateway.desc")}
    >
      <div className="mb-2 mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {modes.map((m) => {
          const selected = mode === m.id;
          const Icon = m.icon;
          return (
            <button
              key={m.id}
              type="button"
              onClick={() => {
                setMode(m.id);
                setResult(null);
              }}
              className={cn(
                "relative flex flex-col gap-1 rounded-xl border p-4 text-left transition",
                selected
                  ? "border-brand bg-brand-light"
                  : "border-surface-border bg-surface hover:border-surface-border-hover",
              )}
            >
              {selected && (
                <Check className="absolute right-3 top-3 h-4 w-4 text-brand" />
              )}
              <Icon className="h-4 w-4 text-ink-body" />
              <span className="text-sm font-medium text-ink-heading">{m.title}</span>
              <span className="text-xs text-ink-body">{m.desc}</span>
            </button>
          );
        })}
      </div>

      {mode === "http" && (
        <Card className="mt-3 rounded-xl shadow-xs">
          <CardContent className="py-5">
            <SettingsRow
              className="px-0 py-0"
              label={t("settings.gateway.urlLabel")}
              desc={t("settings.gateway.urlDesc")}
            >
              <Input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://&lt;sandbox-id&gt;:8000"
                className="w-[280px]"
              />
            </SettingsRow>
            <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />
            <SettingsRow
              className="px-0 py-0"
              label={t("settings.gateway.externalUrlLabel")}
              desc={t("settings.gateway.externalUrlDesc")}
            >
              <Input
                value={externalUrl}
                onChange={(e) => setExternalUrl(e.target.value)}
                placeholder="https://&lt;host-reachable&gt;:8000"
                className="w-[280px]"
              />
            </SettingsRow>
            <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />
            <SettingsRow
              className="px-0 py-0"
              label={t("settings.gateway.tokenLabel")}
              desc={t("settings.gateway.tokenDesc")}
            >
              <Input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={
                  tokenPresent
                    ? t("settings.gateway.tokenStored")
                    : t("settings.gateway.tokenPlaceholder")
                }
                className="w-[280px]"
              />
            </SettingsRow>

            {result && <TestBanner result={result} />}

            <div className="mt-5 flex items-center justify-between">
              <span className="text-xs text-ink-meta">
                {t("settings.gateway.restartNote")}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void onTest()}
                  disabled={testing || !url}
                >
                  {testing && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  {t("settings.gateway.testButton")}
                </Button>
                <Button size="sm" onClick={() => void onSave()} disabled={saving || !url}>
                  {t("settings.gateway.saveButton")}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {mode === "inprocess" && (
        <div className="mt-3 flex items-center justify-end">
          <Button size="sm" onClick={() => void onSave()} disabled={saving}>
            {t("settings.gateway.saveButton")}
          </Button>
        </div>
      )}
    </SettingsSection>
  );
};

/**
 * Inline Test result. The load-bearing case: kernel reachable + token OK but
 * the ④ callback base is loopback/unset → WARNING, not success, because a
 * remote kernel still can't call back (memory/docs/task tools would fail).
 */
const TestBanner = ({ result }: { result: KernelEndpointTestResult }) => {
  const { t } = useTranslation();
  const callbackBad = result.callback_hint !== "ok";
  const tone: "error" | "warning" | "success" = !result.ok
    ? "error"
    : callbackBad
      ? "warning"
      : "success";
  const Icon =
    tone === "success" ? CheckCircle2 : tone === "warning" ? AlertTriangle : XCircle;
  const callbackMsg =
    result.callback_hint === "loopback"
      ? t("settings.gateway.callbackLoopback")
      : result.callback_hint === "unset"
        ? t("settings.gateway.callbackUnset")
        : null;
  return (
    <div
      className={cn(
        "mt-4 flex items-start gap-2 rounded-lg px-3 py-2.5 text-xs",
        tone === "success" && "bg-success-light text-success-text",
        tone === "warning" && "bg-warning-light text-warning-text",
        tone === "error" && "bg-error-light text-error-text",
      )}
    >
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div className="space-y-0.5">
        <div>{result.detail}</div>
        {result.ok && callbackMsg && <div>{callbackMsg}</div>}
      </div>
    </div>
  );
};
