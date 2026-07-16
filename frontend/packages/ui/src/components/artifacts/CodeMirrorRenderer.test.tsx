import { render, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import type { ArtifactDescriptor } from "./artifact-viewer.types";
import { CodeMirrorRenderer } from "./CodeMirrorRenderer";

function artifact(previewKind: "code" | "plain"): ArtifactDescriptor {
  return {
    id: `artifact:${previewKind}`,
    kind: "project_file",
    path: previewKind === "code" ? "example.unknown" : "notes.txt",
    name: previewKind === "code" ? "example.unknown" : "notes.txt",
    previewKind,
    capabilities: {
      canPreview: true,
      canEdit: false,
      canOpenExternal: false,
      canCopyContent: true,
      canDownload: true,
    },
  };
}

const content = {
  kind: "text" as const,
  encoding: "utf-8" as const,
  content: "a very long line that should only wrap in plain text mode",
  truncated: false,
};

beforeAll(() => {
  // CodeMirror measures text ranges; jsdom intentionally omits these layout
  // APIs, so provide neutral geometry for renderer behavior tests.
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => new DOMRect(),
  });
});

describe("CodeMirrorRenderer", () => {
  it("enables line wrapping for plain text", async () => {
    const { container } = render(
      <CodeMirrorRenderer artifact={artifact("plain")} content={content} />,
    );

    await waitFor(() =>
      expect(container.querySelector(".cm-lineWrapping")).not.toBeNull(),
    );
  });

  it("preserves horizontal code layout", async () => {
    const { container } = render(
      <CodeMirrorRenderer artifact={artifact("code")} content={content} />,
    );

    await waitFor(() =>
      expect(container.querySelector(".cm-editor")).not.toBeNull(),
    );
    expect(container.querySelector(".cm-lineWrapping")).toBeNull();
  });
});
