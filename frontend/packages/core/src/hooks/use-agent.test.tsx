/** @vitest-environment jsdom */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent } from "../api/agents-api";
import { clearRequestCacheForTests } from "../api/request";
import { setComposerCatalogAdapter } from "../edition/composer-catalog";
import { useComposerAgentLibrary } from "./use-agent";

const agent = (slug: string): Agent => ({
  id: slug,
  slug,
  name: slug,
  description: "",
  instructions: "",
  runtime: "claude_agent",
  model: "claude-sonnet-4-6",
  skills: [],
  connector_types: [],
  provider_id: null,
  effort: null,
  source: "custom",
  readonly: false,
  deletable: true,
  avatar: null,
});

afterEach(() => {
  setComposerCatalogAdapter(null);
  clearRequestCacheForTests();
  vi.unstubAllGlobals();
});

describe("useComposerAgentLibrary", () => {
  it("reloads agents from each selected execution target", async () => {
    const listAgents = vi.fn(({ targetId }: { targetId?: string | null }) =>
      Promise.resolve({
        agents: [
          targetId === "cloud" ? agent("cloud-agent") : agent("local-agent"),
        ],
      }),
    );
    setComposerCatalogAdapter({
      getScopeKey: ({ targetId }) => `test:${targetId ?? "default"}`,
      listAgents,
      listProviderChannels: vi.fn(),
    });

    const { result, rerender } = renderHook(
      ({ targetId }) => useComposerAgentLibrary(targetId),
      { initialProps: { targetId: "local" } },
    );

    await waitFor(() =>
      expect(result.current).toEqual({
        agents: [agent("local-agent")],
        loaded: true,
      }),
    );

    rerender({ targetId: "cloud" });
    expect(result.current).toEqual({ agents: [], loaded: false });
    await waitFor(() =>
      expect(result.current).toEqual({
        agents: [agent("cloud-agent")],
        loaded: true,
      }),
    );

    expect(listAgents).toHaveBeenCalledTimes(2);
    expect(listAgents.mock.calls.map(([context]) => context.targetId)).toEqual([
      "local",
      "cloud",
    ]);
  });

  it("ignores an obsolete response after switching targets", async () => {
    let resolveLocal!: (value: Response) => void;
    let resolveCloud!: (value: Response) => void;
    const localRequest = new Promise<Response>((resolve) => {
      resolveLocal = resolve;
    });
    const cloudRequest = new Promise<Response>((resolve) => {
      resolveCloud = resolve;
    });
    const listAgents = vi
      .fn()
      .mockReturnValueOnce(localRequest.then((response) => response.json()))
      .mockReturnValueOnce(cloudRequest.then((response) => response.json()));
    setComposerCatalogAdapter({
      getScopeKey: ({ targetId }) => `test:${targetId ?? "default"}`,
      listAgents,
      listProviderChannels: vi.fn(),
    });

    const { result, rerender } = renderHook(
      ({ targetId }) => useComposerAgentLibrary(targetId),
      { initialProps: { targetId: "local" } },
    );

    rerender({ targetId: "cloud" });
    expect(result.current).toEqual({ agents: [], loaded: false });

    await act(async () => {
      resolveLocal(
        new Response(JSON.stringify({ agents: [agent("local-agent")] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      await localRequest;
    });
    expect(result.current).toEqual({ agents: [], loaded: false });

    await act(async () => {
      resolveCloud(
        new Response(JSON.stringify({ agents: [agent("cloud-agent")] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      await cloudRequest;
    });
    expect(result.current).toEqual({
      agents: [agent("cloud-agent")],
      loaded: true,
    });
  });

  it("reloads the current target when its refresh key changes", async () => {
    const listAgents = vi.fn().mockResolvedValue({ agents: [] });
    setComposerCatalogAdapter({
      getScopeKey: ({ targetId }) => `test:${targetId ?? "default"}`,
      listAgents,
      listProviderChannels: vi.fn(),
    });

    const { result, rerender } = renderHook(
      ({ refreshKey }) =>
        useComposerAgentLibrary("local", refreshKey),
      { initialProps: { refreshKey: "first" } },
    );

    await waitFor(() => expect(result.current.loaded).toBe(true));
    rerender({ refreshKey: "second" });
    expect(result.current).toEqual({ agents: [], loaded: false });
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(listAgents).toHaveBeenCalledTimes(2);
  });
});
