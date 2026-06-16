import { useEffect, useState } from "react";
import {
  Badge,
  DeleteConfirmDialog,
  ProviderAddDialog,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  type ProviderOption,
} from "@valuz/ui";
import {
  providersApi,
  settingsApi,
  type ModelDefaults,
  type ProviderDescriptor,
  type ProviderListItem,
  type RuntimeId,
} from "@valuz/core";
import { useTranslation } from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";
import { Check, ChevronDown, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { OnboardingStep } from "./OnboardingShell";
import { StepFooter } from "./StepFooter";

/**
 * Step 2 · Connect a model channel — CHANNEL-FIRST. One card per configured
 * channel (provider): the Claude Code / OpenAI Codex subscription logins plus
 * every api-key channel the user adds (智谱 / DeepSeek / …). Picking a model on
 * a card makes it the global default. This step is about the CHANNEL, not the
 * runtime: a channel can run on several runtimes, so we never surface a runtime
 * name here. The runtime that backs the default is derived from the channel
 * (subscriptions pin their CLI's engine; api-key channels run on the Valuz
 * Agent runtime, which is the only one that drives an arbitrary api_key) and
 * sent transparently — the user picks it per-agent later, where it matters.
 */

/** A channel's recommended runtime, kept out of the UI. Subscriptions pin to
 *  their CLI; api-key / system channels are driven by the Valuz Agent
 *  (deepagents) runtime — the only one that authenticates to a raw api_key,
 *  regardless of whether the wire shape is OpenAI- or Anthropic-style. */
const runtimeForChannel = (p: ProviderListItem): RuntimeId => {
  if (p.provider_kind === "claude-subscription") return "claude_agent";
  if (p.provider_kind === "codex-subscription") return "codex";
  return "deepagents";
};

type ModelChoice = { modelId: string; modelLabel: string };

const modelsOf = (p: ProviderListItem): string[] =>
  p.model_options.length > 0
    ? p.model_options
    : p.default_model
      ? [p.default_model]
      : [];

export const ConnectStep = ({
  onContinue,
  onSkip,
}: {
  onContinue: () => void;
  onSkip: () => void;
}) => {
  const { t } = useTranslation();
  const [providers, setProviders] = useState<ProviderListItem[]>([]);
  const [defaults, setDefaults] = useState<ModelDefaults | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  // Provider descriptors (the channel kinds the user can add) drive the
  // "add API-key channel" dialog's provider dropdown.
  const [descriptors, setDescriptors] = useState<ProviderDescriptor[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  // The channel pending deletion (api-key channels only — subscriptions and
  // system channels aren't deletable). Drives the confirm dialog.
  const [deleteTarget, setDeleteTarget] = useState<ProviderListItem | null>(
    null,
  );
  // Accordion: only the expanded channel reveals its model picker. ``undefined``
  // = no user interaction yet (the default channel shows open); a string = the
  // channel the user opened; ``null`` = the user explicitly collapsed it.
  // Clicking a header toggles (open ↔ collapse), so it's a deliberate two-step
  // rather than a wall of open dropdowns.
  const [expandedId, setExpandedId] = useState<string | null | undefined>(
    undefined,
  );

  const refresh = async () => {
    const [list, md] = await Promise.all([
      providersApi.list(),
      settingsApi.getModelDefaults(),
    ]);
    setProviders(list.providers);
    setDefaults(md);
  };

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [, desc] = await Promise.all([
          refresh(),
          providersApi.listProviders(),
        ]);
        if (!cancelled) setDescriptors(desc.providers);
      } catch {
        /* surfaced as empty list — reload to retry */
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const defaultProviderId = defaults?.default_provider_id ?? null;
  const defaultModel = defaults?.default_model ?? null;
  const hasDefault = Boolean(defaultProviderId && defaultModel);
  // No explicit user choice yet → the default channel is the one shown open.
  const effectiveExpanded =
    expandedId === undefined ? defaultProviderId : expandedId;
  // Footer status line shows the human label — overlay system channels expose
  // ``model_labels[id]``; api_key channels' map is empty, so id stays as-is.
  const defaultModelLabel = (() => {
    if (!defaultModel) return null;
    const provider = providers.find((p) => p.id === defaultProviderId);
    return provider?.model_labels?.[defaultModel] || defaultModel;
  })();

  // One card per usable channel: subscription logins (OAuth), configured
  // api-key channels (credential present), and system-managed channels.
  // Subscriptions sort first. Channels with no selectable model are dropped.
  const channels = providers
    .filter(
      (p) =>
        p.enabled &&
        (p.auth_type === "oauth" ||
          p.credential_source !== "none" ||
          p.source === "system") &&
        modelsOf(p).length > 0,
    )
    .sort((a, b) => {
      const oauth = (p: ProviderListItem) => (p.auth_type === "oauth" ? 0 : 1);
      return oauth(a) - oauth(b);
    });

  // Disambiguate same-named channels (e.g. two 智谱 (GLM) added separately) by
  // numbering them, so they're not an indistinguishable wall of identical
  // cards. Display only — the stored channel name is untouched.
  const channelLabels: string[] = (() => {
    const total = new Map<string, number>();
    for (const p of channels) total.set(p.name, (total.get(p.name) ?? 0) + 1);
    const seen = new Map<string, number>();
    return channels.map((p) => {
      const n = (seen.get(p.name) ?? 0) + 1;
      seen.set(p.name, n);
      return (total.get(p.name) ?? 1) > 1 ? `${p.name} ${n}` : p.name;
    });
  })();

  const setAsDefault = async (provider: ProviderListItem, model: string) => {
    setBusy(provider.id);
    try {
      const next = await settingsApi.patchModelDefaults({
        // Runtime is derived from the channel and sent transparently — the
        // global default needs a coherent (runtime, provider, model) triple.
        default_runtime: runtimeForChannel(provider),
        default_provider_id: provider.id,
        default_model: model,
      });
      setDefaults(next);
      await refresh();
    } catch {
      toast.error(
        _t("onboarding.setDefaultFailed" as Parameters<typeof _t>[0]),
      );
    } finally {
      setBusy(null);
    }
  };

  const handleDelete = async (provider: ProviderListItem) => {
    setBusy(provider.id);
    try {
      await providersApi.delete(provider.id);
      await refresh();
    } catch {
      toast.error(_t("common.error" as Parameters<typeof _t>[0]));
    } finally {
      setBusy(null);
      setDeleteTarget(null);
    }
  };

  return (
    <OnboardingStep
      title={t("onboarding.connectTitle" as Parameters<typeof t>[0])}
      subtitle={t("onboarding.connectSubtitle" as Parameters<typeof t>[0])}
      footer={
        <StepFooter
          status={
            hasDefault
              ? t(
                  "onboarding.connectStatusHasDefault" as Parameters<
                    typeof t
                  >[0],
                  { model: defaultModelLabel ?? "" },
                )
              : t(
                  "onboarding.connectStatusNoDefault" as Parameters<
                    typeof t
                  >[0],
                )
          }
          onSkip={onSkip}
          skipLabel={t("onboarding.skip" as Parameters<typeof t>[0])}
          primaryLabel={t("onboarding.continue" as Parameters<typeof t>[0])}
          onPrimary={onContinue}
          primaryDisabled={!hasDefault}
        />
      }
    >
      {loading ? (
        <div className="flex items-center gap-3 rounded-xl border border-surface-border bg-surface-soft/60 px-4 py-4 text-sm text-ink-meta">
          <Loader2 className="h-4 w-4 animate-spin text-brand" />
          {t("onboarding.connectLoading" as Parameters<typeof t>[0])}
        </div>
      ) : (
        <div className="space-y-2.5">
          {channels.map((p, i) => {
            const models: ModelChoice[] = modelsOf(p).map((m) => ({
              modelId: m,
              modelLabel: p.model_labels?.[m] || m,
            }));
            const isDefault = defaultProviderId === p.id;
            return (
              <ChannelCardRow
                key={p.id}
                name={channelLabels[i]}
                models={models}
                value={isDefault ? (defaultModel ?? "") : ""}
                isDefault={isDefault}
                deletable={p.deletable}
                onDelete={() => setDeleteTarget(p)}
                expanded={effectiveExpanded === p.id}
                onToggle={() =>
                  setExpandedId((prev) => {
                    const cur =
                      prev === undefined ? defaultProviderId : prev;
                    return cur === p.id ? null : p.id;
                  })
                }
                busy={busy === p.id}
                onPick={(model) => void setAsDefault(p, model)}
              />
            );
          })}

          {/* Add an API-key channel (DeepSeek / OpenRouter / any compatible
              API). Opens the same dialog Settings → Models uses; on save the
              new channel appears as a card above. */}
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="flex w-full items-center gap-3 rounded-xl border border-dashed border-surface-border bg-surface px-4 py-3.5 text-left transition-colors hover:border-surface-border-hover hover:bg-surface-soft"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-soft">
              <Plus className="h-4 w-4 text-ink-meta" />
            </span>
            <span className="text-sm font-medium text-ink-heading">
              {t("onboarding.addApiKeyChannel" as Parameters<typeof t>[0])}
            </span>
            <span className="ml-auto text-2xs text-ink-muted">
              {t("onboarding.addApiKeyHint" as Parameters<typeof t>[0])}
            </span>
          </button>
        </div>
      )}
      <ProviderAddDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        providers={descriptors
          // OAuth subscription channels (Claude / Codex) are reached through
          // their own cards above; the api_key form would ask them for a key
          // that makes no sense, so drop them from the dropdown.
          .filter((p) => p.auth_type !== "oauth")
          .map((p): ProviderOption => {
            const displayNames: Record<string, string> = {
              anthropic: "Anthropic (Claude)",
              openai: "OpenAI (ChatGPT)",
              deepseek: "DeepSeek",
              openrouter: "OpenRouter",
              moonshot: "Moonshot (Kimi)",
              minimax: "MiniMax",
              compatible: t("common.custom" as Parameters<typeof t>[0]),
            };
            return {
              kind: p.kind,
              display_name: displayNames[p.kind] ?? p.display_name,
              supports_custom_base_url: p.supports_custom_base_url,
              supports_protocol_selection: p.supports_protocol_selection,
              default_base_url: p.default_base_url,
              anthropic_base_url: p.anthropic_base_url,
              default_model: p.default_model,
              model_options: p.model_options,
              docs_url: p.docs_url,
            };
          })}
        onProbeModels={(payload) => providersApi.probeModels(payload)}
        onPing={(payload) =>
          providersApi.ping({
            base_url: payload.base_url,
            api_key: payload.api_key,
            protocol: payload.protocol ?? null,
            models: payload.models,
          })
        }
        onCreate={async (payload) => {
          await providersApi.create(payload);
          // Reload providers so the new channel shows up as a card immediately.
          await refresh();
        }}
      />
      <DeleteConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(v) => {
          if (!v) setDeleteTarget(null);
        }}
        itemName={deleteTarget?.name}
        loading={busy === deleteTarget?.id}
        onConfirm={() => {
          if (deleteTarget) void handleDelete(deleteTarget);
        }}
      />
    </OnboardingStep>
  );
};

/* -------------------------------------------------------------------------- */

const ChannelCardRow = ({
  name,
  models,
  value,
  isDefault,
  deletable,
  onDelete,
  expanded,
  onToggle,
  busy,
  onPick,
}: {
  name: string;
  models: ModelChoice[];
  value: string;
  isDefault: boolean;
  deletable: boolean;
  onDelete: () => void;
  expanded: boolean;
  onToggle: () => void;
  busy: boolean;
  onPick: (model: string) => void;
}) => {
  const { t } = useTranslation();
  return (
    <div
      className={`rounded-xl border transition-all ${
        isDefault
          ? "border-brand/60 bg-brand-light/20 ring-1 ring-brand/40"
          : "border-surface-border bg-surface"
      }`}
    >
      {/* Header is a div (not a button) so the delete control can be a real
          nested button — clicking it deletes, not toggles. */}
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        className="flex w-full cursor-pointer items-center gap-3.5 px-4 py-3.5 text-left"
      >
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[13px] font-bold ${
            isDefault
              ? "bg-brand/10 text-brand"
              : "bg-surface-soft text-ink-meta"
          }`}
        >
          {name.slice(0, 1)}
        </div>
        <div className="min-w-0 flex-1">
          <span className="text-sm font-medium text-ink-heading">{name}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {busy && <Loader2 className="h-4 w-4 animate-spin text-brand" />}
          {isDefault && (
            <Badge variant="success" className="gap-1 text-2xs">
              <Check className="h-2.5 w-2.5" />
              {t("onboarding.defaultBadge" as Parameters<typeof t>[0])}
            </Badge>
          )}
          {deletable && (
            <button
              type="button"
              aria-label={t("common.delete")}
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
              className="flex h-7 w-7 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-surface-soft hover:text-[#f54b4b]"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
          <ChevronDown
            className={`h-4 w-4 text-ink-muted transition-transform ${
              expanded ? "rotate-180" : ""
            }`}
          />
        </div>
      </div>
      {expanded && (
        <div className="border-t border-surface-border/70 px-4 pb-4 pt-3">
          <Select value={value} onValueChange={onPick} disabled={busy}>
            <SelectTrigger className="w-full font-mono text-xs">
              <SelectValue
                placeholder={t(
                  "onboarding.pickModelHint" as Parameters<typeof t>[0],
                )}
              />
            </SelectTrigger>
            <SelectContent>
              {models.map((m) => (
                <SelectItem
                  key={m.modelId}
                  value={m.modelId}
                  className="font-mono text-xs"
                >
                  {m.modelLabel}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
};
