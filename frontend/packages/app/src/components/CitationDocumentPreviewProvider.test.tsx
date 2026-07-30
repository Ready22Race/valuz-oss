import { afterEach, describe, expect, it, vi } from "vitest";
import type { PlatformCapabilities } from "@valuz/core";

import {
  decodeCitationOpenRef,
  encodeCitationOpenRef,
  locatorToDocumentLocation,
  materializeCitationDocument,
} from "./CitationDocumentPreviewProvider";

const WEB_PLATFORM: PlatformCapabilities = {
  selectDirectory: async () => null,
  copyFiles: async () => ({ copied: 0, errors: [] }),
  deleteFile: async () => ({ success: false }),
  revealInFinder: async () => undefined,
  quitApp: async () => undefined,
  openNewWindow: async () => undefined,
  isElectron: false,
  isMac: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("citation document preview helpers", () => {
  it("round-trips an opaque identity-only open ref", () => {
    const target = {
      sessionId: "session-1",
      messageId: "message-1",
      citationId: "cit-1",
    };
    const encoded = encodeCitationOpenRef(target);

    expect(encoded).not.toContain("session-1");
    expect(decodeCitationOpenRef(encoded)).toEqual(target);
    expect(decodeCitationOpenRef("not+base64")).toBeNull();
  });

  it("maps every PDF locator field needed by the highlighter", () => {
    expect(
      locatorToDocumentLocation({
        kind: "pdf",
        page: 42,
        rects: [{ x: 0.1, y: 0.2, width: 0.3, height: 0.04 }],
        quote: { exact: "Revenue grew." },
        pageRotation: 90,
      }),
    ).toEqual({
      kind: "pdf",
      page: 42,
      rects: [{ x: 0.1, y: 0.2, width: 0.3, height: 0.04 }],
      quote: { exact: "Revenue grew." },
      pageRotation: 90,
    });
  });

  it("reads remote HTML client-side before handing it to the sandboxed reader", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<h1>Report</h1>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
      ),
    );

    const document = await materializeCitationDocument(
      {
        id: "doc-1",
        title: "Report",
        render: {
          kind: "file",
          mimeType: "text/html",
          address: {
            kind: "remote",
            absPath: null,
            url: "https://signed.invalid/file",
            expiresAt: 123,
          },
        },
      },
      WEB_PLATFORM,
    );

    expect(document.render).toEqual({
      kind: "html",
      html: "<h1>Report</h1>",
    });
  });

  it("sanitizes backend-fetched inline HTML without a network request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const document = await materializeCitationDocument(
      {
        id: "doc-1",
        title: "Report",
        render: {
          kind: "html",
          html: '<h1 data-chunk-id="c1">Report</h1><script>steal()</script>',
        },
      },
      WEB_PLATFORM,
    );

    expect(fetchMock).not.toHaveBeenCalled();
    expect(document.render).toEqual({
      kind: "html",
      html: '<h1 data-chunk-id="c1">Report</h1>',
    });
  });

  it("sanitizes table markup in both the renderer and fallback chunk index", async () => {
    const malicious = '<table><tr><td onclick="steal()">42</td></tr></table>';
    const document = await materializeCitationDocument(
      {
        id: "doc-1",
        title: "Report",
        chunks: [{ id: "c1", type: "table", html: malicious }],
        render: {
          kind: "chunks",
          chunks: [{ id: "c1", type: "table", html: malicious }],
        },
      },
      WEB_PLATFORM,
    );

    expect(document.chunks?.[0]?.html).not.toContain("onclick");
    expect(document.render.kind).toBe("chunks");
    if (document.render.kind === "chunks") {
      expect(document.render.chunks[0]?.html).not.toContain("onclick");
    }
  });
});
