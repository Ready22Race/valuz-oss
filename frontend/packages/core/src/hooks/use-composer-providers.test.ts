/** @vitest-environment jsdom */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LLMModel } from "@valuz/shared";

import type { LLMChannelDetail } from "../api/providers-api";
import { clearRequestCacheForTests } from "../api/request";
import {
  useComposerProviderChannels,
  useComposerProviders,
  type RuntimeProvider,
} from "./use-composer-providers";

/** Wrap bare model ids into ADR-011 ``LLMModel`` rows carrying the
 *  server-resolved ``runtimes`` the picker filters on. */
const mdl = (ids: string[], runtimes: RuntimeProvider[]): LLMModel[] =>
  ids.map((id) => ({ id, label: null, runtimes }));

// Common runtime sets (what the backend ``runtimes_for`` stamps):
const ANTHROPIC: RuntimeProvider[] = ["claude_agent", "deepagents"];
const OPENAI_COMPLETION: RuntimeProvider[] = ["deepagents"];
const OPENAI_RESPONSE: RuntimeProvider[] = ["codex"];
const CODEX_SUB: RuntimeProvider[] = ["codex"];
const CLAUDE_SUB: RuntimeProvider[] = ["claude_agent"];

const provider = (
  overrides: Partial<LLMChannelDetail> & Pick<LLMChannelDetail, "id" | "name">,
): LLMChannelDetail => ({
  provider_kind: "anthropic",
  source: "managed",
  enabled: true,
  is_default: false,
  deletable: true,
  default_model: null,
  test_status: "never",
  credential_source: "secret_ref",
  auth_type: "api_key",
  base_url: null,
  models: [],
  group: "api_key",
  group_rank: 40,
  unavailable_reason: null,
  supports_custom_base_url: false,
  supports_connection_test: true,
  protocol: null,
  effective_protocol: "anthropic",
  compatible_protocols: ["anthropic"],
  ...overrides,
});

afterEach(() => {
  clearRequestCacheForTests();
  vi.unstubAllGlobals();
});

describe("useComposerProviderChannels", () => {
  it("reloads the gated model list from each selected execution target", async () => {
    const localProvider = provider({ id: "local", name: "Local" });
    const cloudProvider = provider({ id: "cloud", name: "Cloud" });
    const fetchSpy = vi.fn().mockImplementation((input: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            providers: input.includes("cloud.example.test")
              ? [cloudProvider]
              : [localProvider],
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchSpy);

    const { result, rerender } = renderHook(
      ({ baseUrl }) => useComposerProviderChannels(baseUrl),
      { initialProps: { baseUrl: "http://localhost:8000" } },
    );

    await waitFor(() => expect(result.current).toEqual([localProvider]));
    rerender({ baseUrl: "https://cloud.example.test" });
    await waitFor(() => expect(result.current).toEqual([cloudProvider]));
    rerender({ baseUrl: "http://localhost:8000" });
    await waitFor(() => expect(result.current).toEqual([localProvider]));

    expect(fetchSpy.mock.calls.map(([url]) => url)).toEqual([
      "http://localhost:8000/v1/providers?gated=1",
      "https://cloud.example.test/v1/providers?gated=1",
      "http://localhost:8000/v1/providers?gated=1",
    ]);
  });

  it("clears the old list and ignores its response after switching targets", async () => {
    let resolveLocal!: (value: Response) => void;
    let resolveCloud!: (value: Response) => void;
    const localRequest = new Promise<Response>((resolve) => {
      resolveLocal = resolve;
    });
    const cloudRequest = new Promise<Response>((resolve) => {
      resolveCloud = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockReturnValueOnce(localRequest)
        .mockReturnValueOnce(cloudRequest),
    );

    const { result, rerender } = renderHook(
      ({ baseUrl }) => useComposerProviderChannels(baseUrl),
      { initialProps: { baseUrl: "http://localhost:8000" } },
    );

    rerender({ baseUrl: "https://cloud.example.test" });
    expect(result.current).toEqual([]);

    await act(async () => {
      resolveLocal(
        new Response(
          JSON.stringify({
            providers: [provider({ id: "local", name: "Local" })],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
      await localRequest;
    });
    expect(result.current).toEqual([]);

    const cloudProvider = provider({ id: "cloud", name: "Cloud" });
    await act(async () => {
      resolveCloud(
        new Response(JSON.stringify({ providers: [cloudProvider] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      await cloudRequest;
    });
    expect(result.current).toEqual([cloudProvider]);
  });
});

describe("useComposerProviders", () => {
  it("flattens enabled providers into one entry per (provider, model)", () => {
    const providers = [
      provider({
        id: "ch-anthropic",
        name: "Anthropic",
        models: mdl(["claude-sonnet-4-6", "claude-opus-4-7"], ANTHROPIC),
      }),
      provider({
        id: "ch-openai",
        name: "OpenAI",
        models: mdl(["gpt-4o"], OPENAI_COMPLETION),
      }),
    ];

    const { result } = renderHook(() => useComposerProviders(providers));
    expect(result.current.map((m) => `${m.providerId}:${m.modelId}`)).toEqual([
      "ch-anthropic:claude-sonnet-4-6",
      "ch-anthropic:claude-opus-4-7",
      "ch-openai:gpt-4o",
    ]);
  });

  it("filters out disabled providers", () => {
    const providers = [
      provider({ id: "ch-on", name: "On", models: mdl(["m1"], ANTHROPIC) }),
      provider({
        id: "ch-off",
        name: "Off",
        enabled: false,
        models: mdl(["m2"], ANTHROPIC),
      }),
    ];

    const { result } = renderHook(() => useComposerProviders(providers));
    expect(result.current.map((m) => m.providerId)).toEqual(["ch-on"]);
  });

  it("drops credential-less api_key providers", () => {
    const providers = [
      provider({
        id: "ch-unconfigured",
        name: "Unconfigured",
        credential_source: "none",
        auth_type: "api_key",
        models: mdl(["m1"], ANTHROPIC),
      }),
    ];
    const { result } = renderHook(() => useComposerProviders(providers));
    expect(result.current).toEqual([]);
  });

  it("for runtimeFilter=deepagents keeps models whose runtimes include deepagents", () => {
    const providers = [
      provider({
        id: "ch-anthropic",
        name: "Anthropic",
        models: mdl(["claude-sonnet-4-6"], ANTHROPIC),
      }),
      provider({
        id: "ch-openai",
        name: "OpenAI",
        provider_kind: "openai",
        models: mdl(["gpt-4o"], OPENAI_COMPLETION),
      }),
      // Subscriptions don't run on deepagents (their runtimes omit it).
      provider({
        id: "ch-claude-subscription",
        name: "Claude (订阅)",
        provider_kind: "claude-subscription",
        credential_source: "none",
        auth_type: "oauth",
        models: mdl(["claude-sonnet-4-6"], CLAUDE_SUB),
      }),
      provider({
        id: "ch-codex-subscription",
        name: "Codex (订阅)",
        provider_kind: "codex-subscription",
        credential_source: "none",
        auth_type: "oauth",
        models: mdl(["gpt-5-codex"], CODEX_SUB),
      }),
    ];

    const { result } = renderHook(() =>
      useComposerProviders(providers, "deepagents"),
    );
    expect(result.current.map((m) => m.providerId)).toEqual([
      "ch-anthropic",
      "ch-openai",
    ]);
  });

  it("for runtimeFilter=claude_agent keeps models whose runtimes include claude_agent", () => {
    const providers = [
      provider({
        id: "ch-anthropic",
        name: "Anthropic",
        models: mdl(["claude-sonnet-4-6"], ANTHROPIC),
      }),
      provider({
        id: "ch-claude-subscription",
        name: "Claude (订阅)",
        provider_kind: "claude-subscription",
        credential_source: "none",
        auth_type: "oauth",
        models: mdl(["claude-sonnet-4-6"], CLAUDE_SUB),
      }),
      // DeepSeek exposing the anthropic wire → claude_agent + deepagents.
      provider({
        id: "ch-deepseek-dual",
        name: "DeepSeek",
        provider_kind: "deepseek",
        models: mdl(["deepseek-v4"], ANTHROPIC),
      }),
      // openai-completion only → not claude_agent.
      provider({
        id: "ch-openai",
        name: "OpenAI",
        provider_kind: "openai",
        models: mdl(["gpt-4o"], OPENAI_COMPLETION),
      }),
      // openai-response only (codex) → not claude_agent.
      provider({
        id: "ch-codex",
        name: "Codex (订阅)",
        provider_kind: "codex-subscription",
        credential_source: "none",
        auth_type: "oauth",
        models: mdl(["gpt-5-codex"], CODEX_SUB),
      }),
    ];

    const { result } = renderHook(() =>
      useComposerProviders(providers, "claude_agent"),
    );
    expect(result.current.map((m) => m.providerId)).toEqual([
      "ch-anthropic",
      "ch-claude-subscription",
      "ch-deepseek-dual",
    ]);
  });

  it("for runtimeFilter=codex keeps openai-response models (subscription + custom/system)", () => {
    const providers = [
      provider({
        id: "ch-codex-subscription",
        name: "Codex (订阅)",
        provider_kind: "codex-subscription",
        credential_source: "none",
        auth_type: "oauth",
        models: mdl(["gpt-5-codex"], CODEX_SUB),
      }),
      // Custom openai-response channel (e.g. Volcengine Ark) → codex too.
      provider({
        id: "ch-ark",
        name: "Custom (Response)",
        provider_kind: "compatible",
        protocol: "openai-response",
        compatible_protocols: ["openai-response"],
        models: mdl(["doubao-seed"], OPENAI_RESPONSE),
      }),
      // Claude subscription — codex can't drive it.
      provider({
        id: "ch-claude-subscription",
        name: "Claude (订阅)",
        provider_kind: "claude-subscription",
        credential_source: "none",
        auth_type: "oauth",
        models: mdl(["claude-sonnet-4-6"], CLAUDE_SUB),
      }),
      // openai-completion api_key → not codex.
      provider({
        id: "ch-openai",
        name: "OpenAI",
        provider_kind: "openai",
        models: mdl(["gpt-4o"], OPENAI_COMPLETION),
      }),
    ];

    const { result } = renderHook(() =>
      useComposerProviders(providers, "codex"),
    );
    expect(result.current.map((m) => m.providerId)).toEqual([
      "ch-codex-subscription",
      "ch-ark",
    ]);
  });

  it("for runtimeFilter=codex surfaces system openai-response channels", () => {
    const providers = [
      provider({
        id: "valuz-channel-codex",
        name: "Valuz 系统模型",
        provider_kind: "system",
        source: "system",
        credential_source: "system_managed",
        auth_type: "oauth",
        compatible_protocols: ["openai-response"],
        models: mdl(["gpt-5.4-nano"], OPENAI_RESPONSE),
      }),
      // Anthropic-only system provider — must NOT leak into the codex card.
      provider({
        id: "valuz-channel",
        name: "Valuz 系统模型",
        provider_kind: "system",
        source: "system",
        credential_source: "system_managed",
        auth_type: "oauth",
        compatible_protocols: ["anthropic"],
        models: mdl(["sys-reportify-pro"], ANTHROPIC),
      }),
    ];

    const { result } = renderHook(() =>
      useComposerProviders(providers, "codex"),
    );
    expect(result.current.map((m) => m.providerId)).toEqual([
      "valuz-channel-codex",
    ]);
  });

  it("for runtimeFilter=deepagents excludes openai-response-only system channels", () => {
    const providers = [
      provider({
        id: "valuz-channel",
        name: "Valuz 系统模型",
        provider_kind: "system",
        source: "system",
        credential_source: "system_managed",
        auth_type: "oauth",
        compatible_protocols: ["anthropic"],
        models: mdl(["sys-reportify-pro"], ANTHROPIC),
      }),
      provider({
        id: "valuz-channel-codex",
        name: "Valuz 系统模型",
        provider_kind: "system",
        source: "system",
        credential_source: "system_managed",
        auth_type: "oauth",
        compatible_protocols: ["openai-response"],
        models: mdl(["gpt-5.4-nano"], OPENAI_RESPONSE),
      }),
    ];

    const { result } = renderHook(() =>
      useComposerProviders(providers, "deepagents"),
    );
    expect(result.current.map((m) => m.providerId)).toEqual(["valuz-channel"]);
  });
});
