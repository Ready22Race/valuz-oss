import { useState, useEffect } from "react";
import { toast } from "sonner";
import { Check, Loader2, Monitor, Cloud, UploadCloud } from "lucide-react";
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
import { settingsApi, type SandboxConfig } from "@valuz/core";

type Driver = "inprocess" | "ags";

const tk = (k: string) => k as Parameters<ReturnType<typeof useTranslation>["t"]>[0];

/**
 * Settings → Cloud Sandbox: configure WHERE the agent kernel runs — in-process
 * (default) or provisioned in a remote AGS cloud sandbox with a Tencent COS
 * mount. UI-driven, no env, no scripts: the config is persisted (secrets to the
 * OS secret store) and the host provisions AGS from it at the next (re)start.
 * The env-driven counterpart is `make dev-ags`.
 */
export const SandboxSection = () => {
  const { t } = useTranslation();
  const [driver, setDriver] = useState<Driver>("inprocess");
  // Non-secret fields round-trip verbatim.
  const [agsDomain, setAgsDomain] = useState("");
  const [agsTemplate, setAgsTemplate] = useState("");
  const [agsMount, setAgsMount] = useState("/workspace");
  const [hostUrl, setHostUrl] = useState("");
  const [cosBucket, setCosBucket] = useState("");
  const [cosRegion, setCosRegion] = useState("ap-beijing");
  const [cosEndpoint, setCosEndpoint] = useState("");
  // Secrets: empty input = keep the stored value; presence shown in placeholder.
  const [agsApiKey, setAgsApiKey] = useState("");
  const [agsToken, setAgsToken] = useState("");
  const [cosId, setCosId] = useState("");
  const [cosKey, setCosKey] = useState("");
  const [present, setPresent] = useState({
    apiKey: false,
    token: false,
    cosId: false,
    cosKey: false,
  });
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const apply = (c: SandboxConfig) => {
    setDriver(c.driver);
    setAgsDomain(c.ags_domain);
    setAgsTemplate(c.ags_template);
    setAgsMount(c.ags_mount_path || "/workspace");
    setHostUrl(c.host_external_url);
    setCosBucket(c.cos_bucket);
    setCosRegion(c.cos_region || "ap-beijing");
    setCosEndpoint(c.cos_endpoint);
    setPresent({
      apiKey: c.ags_api_key_present,
      token: c.ags_kernel_token_present,
      cosId: c.cos_secret_id_present,
      cosKey: c.cos_secret_key_present,
    });
  };

  useEffect(() => {
    // Soft-fail: a backend without the endpoint leaves the in-process default.
    settingsApi.getSandbox().then(apply).catch(() => undefined);
  }, []);

  const onSave = async () => {
    setSaving(true);
    try {
      const c = await settingsApi.patchSandbox(
        driver === "ags"
          ? {
              driver,
              ags_domain: agsDomain,
              ags_template: agsTemplate,
              ags_mount_path: agsMount,
              host_external_url: hostUrl,
              cos_bucket: cosBucket,
              cos_region: cosRegion,
              cos_endpoint: cosEndpoint,
              // Only send a secret the user actually typed (empty = keep).
              ...(agsApiKey ? { ags_api_key: agsApiKey } : {}),
              ...(agsToken ? { ags_kernel_token: agsToken } : {}),
              ...(cosId ? { cos_secret_id: cosId } : {}),
              ...(cosKey ? { cos_secret_key: cosKey } : {}),
            }
          : { driver },
      );
      apply(c);
      setAgsApiKey("");
      setAgsToken("");
      setCosId("");
      setCosKey("");
      toast.success(t(tk("settings.sandbox.saved")));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t(tk("settings.sandbox.saveFailed")));
    } finally {
      setSaving(false);
    }
  };

  const onSync = async () => {
    setSyncing(true);
    try {
      const r = await settingsApi.syncWorkspace();
      toast.success(`${t(tk("settings.sandbox.syncDone"))} (${r.total_files})`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t(tk("settings.sandbox.syncFailed")));
    } finally {
      setSyncing(false);
    }
  };

  const drivers: { id: Driver; icon: typeof Monitor; title: string; desc: string }[] = [
    {
      id: "inprocess",
      icon: Monitor,
      title: t(tk("settings.sandbox.localTitle")),
      desc: t(tk("settings.sandbox.localDesc")),
    },
    {
      id: "ags",
      icon: Cloud,
      title: t(tk("settings.sandbox.agsTitle")),
      desc: t(tk("settings.sandbox.agsDesc")),
    },
  ];

  const secretPlaceholder = (stored: boolean) =>
    stored
      ? t(tk("settings.sandbox.secretStored"))
      : t(tk("settings.sandbox.secretPlaceholder"));

  return (
    <SettingsSection
      title={t(tk("settings.tab.sandbox.label"))}
      desc={t(tk("settings.sandbox.desc"))}
    >
      <div className="mb-2 mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {drivers.map((d) => {
          const selected = driver === d.id;
          const Icon = d.icon;
          return (
            <button
              key={d.id}
              type="button"
              onClick={() => setDriver(d.id)}
              className={cn(
                "relative flex flex-col gap-1 rounded-xl border p-4 text-left transition",
                selected
                  ? "border-brand bg-brand-light"
                  : "border-surface-border bg-surface hover:border-surface-border-hover",
              )}
            >
              {selected && <Check className="absolute right-3 top-3 h-4 w-4 text-brand" />}
              <Icon className="h-4 w-4 text-ink-body" />
              <span className="text-sm font-medium text-ink-heading">{d.title}</span>
              <span className="text-xs text-ink-body">{d.desc}</span>
            </button>
          );
        })}
      </div>

      {driver === "ags" && (
        <>
          {/* AGS sandbox */}
          <Card className="mt-3 rounded-xl shadow-xs">
            <CardContent className="py-5">
              <p className="mb-4 text-xs font-medium text-ink-label">
                {t(tk("settings.sandbox.agsGroupTitle"))}
              </p>
              <Field
                label={t(tk("settings.sandbox.apiKeyLabel"))}
                desc={t(tk("settings.sandbox.apiKeyDesc"))}
              >
                <Input
                  type="password"
                  value={agsApiKey}
                  onChange={(e) => setAgsApiKey(e.target.value)}
                  placeholder={secretPlaceholder(present.apiKey)}
                  className="w-[280px]"
                />
              </Field>
              <Divider />
              <Field
                label={t(tk("settings.sandbox.domainLabel"))}
                desc={t(tk("settings.sandbox.domainDesc"))}
              >
                <Input
                  value={agsDomain}
                  onChange={(e) => setAgsDomain(e.target.value)}
                  placeholder="ap-beijing.tencentags.com"
                  className="w-[280px]"
                />
              </Field>
              <Divider />
              <Field
                label={t(tk("settings.sandbox.templateLabel"))}
                desc={t(tk("settings.sandbox.templateDesc"))}
              >
                <Input
                  value={agsTemplate}
                  onChange={(e) => setAgsTemplate(e.target.value)}
                  placeholder="valuz-kernel"
                  className="w-[280px]"
                />
              </Field>
              <Divider />
              <Field
                label={t(tk("settings.sandbox.tokenLabel"))}
                desc={t(tk("settings.sandbox.tokenDesc"))}
              >
                <Input
                  type="password"
                  value={agsToken}
                  onChange={(e) => setAgsToken(e.target.value)}
                  placeholder={secretPlaceholder(present.token)}
                  className="w-[280px]"
                />
              </Field>
              <Divider />
              <Field
                label={t(tk("settings.sandbox.mountLabel"))}
                desc={t(tk("settings.sandbox.mountDesc"))}
              >
                <Input
                  value={agsMount}
                  onChange={(e) => setAgsMount(e.target.value)}
                  placeholder="/workspace"
                  className="w-[280px]"
                />
              </Field>
              <Divider />
              <Field
                label={t(tk("settings.sandbox.hostUrlLabel"))}
                desc={t(tk("settings.sandbox.hostUrlDesc"))}
              >
                <Input
                  value={hostUrl}
                  onChange={(e) => setHostUrl(e.target.value)}
                  placeholder="https://<host-reachable>:8000"
                  className="w-[280px]"
                />
              </Field>
            </CardContent>
          </Card>

          {/* COS object storage */}
          <Card className="mt-3 rounded-xl shadow-xs">
            <CardContent className="py-5">
              <p className="mb-4 text-xs font-medium text-ink-label">
                {t(tk("settings.sandbox.cosGroupTitle"))}
              </p>
              <Field
                label={t(tk("settings.sandbox.bucketLabel"))}
                desc={t(tk("settings.sandbox.bucketDesc"))}
              >
                <Input
                  value={cosBucket}
                  onChange={(e) => setCosBucket(e.target.value)}
                  placeholder="valuz-xxx-1250000000"
                  className="w-[280px]"
                />
              </Field>
              <Divider />
              <Field label={t(tk("settings.sandbox.regionLabel"))}>
                <Input
                  value={cosRegion}
                  onChange={(e) => setCosRegion(e.target.value)}
                  placeholder="ap-beijing"
                  className="w-[280px]"
                />
              </Field>
              <Divider />
              <Field
                label={t(tk("settings.sandbox.endpointLabel"))}
                desc={t(tk("settings.sandbox.endpointDesc"))}
              >
                <Input
                  value={cosEndpoint}
                  onChange={(e) => setCosEndpoint(e.target.value)}
                  placeholder="https://cos.ap-beijing.myqcloud.com"
                  className="w-[280px]"
                />
              </Field>
              <Divider />
              <Field label={t(tk("settings.sandbox.cosIdLabel"))}>
                <Input
                  type="password"
                  value={cosId}
                  onChange={(e) => setCosId(e.target.value)}
                  placeholder={secretPlaceholder(present.cosId)}
                  className="w-[280px]"
                />
              </Field>
              <Divider />
              <Field label={t(tk("settings.sandbox.cosKeyLabel"))}>
                <Input
                  type="password"
                  value={cosKey}
                  onChange={(e) => setCosKey(e.target.value)}
                  placeholder={secretPlaceholder(present.cosKey)}
                  className="w-[280px]"
                />
              </Field>
            </CardContent>
          </Card>

          <p className="mt-3 text-xs text-ink-meta">{t(tk("settings.sandbox.cloudHint"))}</p>
        </>
      )}

      <div className="mt-5 flex items-center justify-between">
        <span className="text-xs text-ink-meta">{t(tk("settings.sandbox.restartNote"))}</span>
        <div className="flex items-center gap-2">
          {driver === "ags" && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void onSync()}
              disabled={syncing}
            >
              {syncing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <UploadCloud className="h-3.5 w-3.5" />
              )}
              {t(tk("settings.sandbox.syncButton"))}
            </Button>
          )}
          <Button size="sm" onClick={() => void onSave()} disabled={saving}>
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {t(tk("settings.sandbox.saveButton"))}
          </Button>
        </div>
      </div>
    </SettingsSection>
  );
};

const Field = ({
  label,
  desc,
  children,
}: {
  label: string;
  desc?: string;
  children: React.ReactNode;
}) => (
  <SettingsRow className="px-0 py-0" label={label} desc={desc}>
    {children}
  </SettingsRow>
);

const Divider = () => <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />;
