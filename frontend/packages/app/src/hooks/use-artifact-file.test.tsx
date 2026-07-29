/** @vitest-environment jsdom */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { resolveOne, resolvedToArtifactFile } = vi.hoisted(() => ({
  resolveOne: vi.fn(),
  resolvedToArtifactFile: vi.fn(),
}));

vi.mock("@valuz/core", async (loadOriginal) => {
  const actual = await loadOriginal<typeof import("@valuz/core")>();
  return {
    ...actual,
    filesApi: { ...actual.filesApi, resolveOne },
  };
});

vi.mock("../lib/resolve-artifact", () => ({ resolvedToArtifactFile }));

import type {
  ApiBaseRef,
  ArtifactFileResponse,
  PlatformCapabilities,
  ResolvedFileDescriptor,
} from "@valuz/core";
import { useArtifactFile } from "./use-artifact-file";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const platform = {
  isElectron: false,
  isMac: false,
} as PlatformCapabilities;

function descriptor(name: string): ResolvedFileDescriptor {
  return {
    ref: `valuz-file:///root/${name}`,
    kind: "remote",
    absPath: null,
    url: `https://files.example/${name}`,
    expiresAt: null,
    name,
    mimeType: "text/plain",
    size: 1,
    exists: true,
    previewKind: "plain",
    capabilities: {
      canPreview: true,
      canDownload: true,
      canOpenExternal: false,
      canCopyContent: true,
    },
    error: null,
  };
}

function response(name: string): ArtifactFileResponse {
  return {
    artifact: {
      id: name,
      kind: "project_file",
      projectId: "p1",
      path: name,
      name,
      previewKind: "plain",
      capabilities: {
        canPreview: true,
        canEdit: false,
        canOpenExternal: false,
        canCopyContent: true,
        canDownload: true,
      },
    },
    content: {
      kind: "text",
      encoding: "utf-8",
      content: name,
      truncated: false,
    },
  };
}

const renderArtifactHook = (baseRef?: ApiBaseRef) =>
  renderHook(() =>
    useArtifactFile({
      projectId: "p1",
      platform,
      locate: (path) => ({
        absolutePath: `/root/${path}`,
        relativePath: path,
      }),
      missingErrorMessage: "missing",
      baseRef,
    }),
  );

beforeEach(() => {
  resolveOne.mockReset();
  resolvedToArtifactFile.mockReset();
  resolvedToArtifactFile.mockImplementation(
    async (item: ResolvedFileDescriptor) => response(item.name),
  );
});

describe("useArtifactFile", () => {
  it("keeps the latest selection when an older request finishes last", async () => {
    const first = deferred<ResolvedFileDescriptor | null>();
    const second = deferred<ResolvedFileDescriptor | null>();
    resolveOne
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { result } = renderArtifactHook();

    act(() => {
      void result.current.open("a.txt");
      void result.current.open("b.txt");
    });
    expect(result.current.selectedPath).toBe("b.txt");

    await act(async () => second.resolve(descriptor("b.txt")));
    await waitFor(() => expect(result.current.artifact?.name).toBe("b.txt"));
    await act(async () => first.resolve(descriptor("a.txt")));

    expect(result.current.artifact?.name).toBe("b.txt");
    expect(resolvedToArtifactFile).toHaveBeenCalledTimes(1);
  });

  it("aborts the active transport and clears state on close", () => {
    resolveOne.mockReturnValue(new Promise(() => {}));
    const { result } = renderArtifactHook();

    act(() => {
      void result.current.open("a.txt");
    });
    const signal = resolveOne.mock.calls[0]?.[1]?.signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    act(() => result.current.close());
    expect(signal.aborted).toBe(true);
    expect(result.current.selectedPath).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("shows the page-provided message for a missing descriptor", async () => {
    resolveOne.mockResolvedValue(null);
    const { result } = renderArtifactHook();

    await act(async () => result.current.open("missing.txt"));

    expect(result.current.error).toBe("missing");
    expect(result.current.loading).toBe(false);
  });

  it("routes the resolve with the caller's entity ref", async () => {
    resolveOne.mockResolvedValue(descriptor("a.txt"));
    const { result } = renderArtifactHook({ sessionId: "s1", projectId: "p1" });

    await act(async () => result.current.open("a.txt"));

    expect(resolveOne.mock.calls[0]?.[1]?.baseRef).toEqual({
      sessionId: "s1",
      projectId: "p1",
      taskId: undefined,
      automationId: undefined,
      kbId: undefined,
    });
  });

  it("defaults the entity ref to the project", async () => {
    resolveOne.mockResolvedValue(descriptor("a.txt"));
    const { result } = renderArtifactHook();

    await act(async () => result.current.open("a.txt"));

    expect(resolveOne.mock.calls[0]?.[1]?.baseRef).toEqual({
      projectId: "p1",
    });
  });

  it("preserves an artifact target across reload and clears it on close", async () => {
    resolveOne.mockResolvedValue(descriptor("report.pdf"));
    const { result } = renderArtifactHook();

    await act(async () => result.current.open("report.pdf", { page: 12 }));
    expect(result.current.target).toEqual({ page: 12 });

    await act(async () => result.current.reload());
    expect(resolveOne).toHaveBeenCalledTimes(2);
    expect(result.current.target).toEqual({ page: 12 });

    act(() => result.current.close());
    expect(result.current.target).toBeNull();
  });
});
