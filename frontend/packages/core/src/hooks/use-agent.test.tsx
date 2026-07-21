/** @vitest-environment jsdom */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent } from "../api/agents-api";
import { clearRequestCacheForTests } from "../api/request";
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
  clearRequestCacheForTests();
  vi.unstubAllGlobals();
});

describe("useComposerAgentLibrary", () => {
  it("reloads agents from each selected execution target", async () => {
    const fetchSpy = vi.fn().mockImplementation((input: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            agents: [
              input.includes("cloud.example.test")
                ? agent("cloud-agent")
                : agent("local-agent"),
            ],
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
      ({ baseUrl }) => useComposerAgentLibrary(baseUrl),
      { initialProps: { baseUrl: "http://localhost:8000" } },
    );

    await waitFor(() =>
      expect(result.current).toEqual({
        agents: [agent("local-agent")],
        loaded: true,
      }),
    );

    rerender({ baseUrl: "https://cloud.example.test" });
    expect(result.current).toEqual({ agents: [], loaded: false });
    await waitFor(() =>
      expect(result.current).toEqual({
        agents: [agent("cloud-agent")],
        loaded: true,
      }),
    );

    expect(fetchSpy.mock.calls.map(([url]) => url)).toEqual([
      "http://localhost:8000/v1/agents",
      "https://cloud.example.test/v1/agents",
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
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockReturnValueOnce(localRequest)
        .mockReturnValueOnce(cloudRequest),
    );

    const { result, rerender } = renderHook(
      ({ baseUrl }) => useComposerAgentLibrary(baseUrl),
      { initialProps: { baseUrl: "http://localhost:8000" } },
    );

    rerender({ baseUrl: "https://cloud.example.test" });
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
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ agents: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchSpy);

    const { result, rerender } = renderHook(
      ({ refreshKey }) =>
        useComposerAgentLibrary("http://localhost:8000", refreshKey),
      { initialProps: { refreshKey: "first" } },
    );

    await waitFor(() => expect(result.current.loaded).toBe(true));
    rerender({ refreshKey: "second" });
    expect(result.current).toEqual({ agents: [], loaded: false });
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });
});
