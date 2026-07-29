import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ArtifactViewerShell,
  type ArtifactDescriptor,
} from "./ArtifactViewerShell";

function artifact(
  capabilities: Partial<ArtifactDescriptor["capabilities"]> = {},
): ArtifactDescriptor {
  return {
    id: "artifact:test",
    kind: "project_file",
    path: "notes.txt",
    name: "notes.txt",
    previewKind: "unsupported",
    capabilities: {
      canPreview: false,
      canEdit: false,
      canOpenExternal: false,
      canCopyContent: false,
      canDownload: false,
      ...capabilities,
    },
  };
}

describe("ArtifactViewerShell", () => {
  it("announces preview errors and exposes a retry action", () => {
    const onReload = vi.fn();
    render(
      <ArtifactViewerShell
        artifact={null}
        content={null}
        error="读取失败"
        onReload={onReload}
      />,
    );

    expect(screen.getByRole("alert").textContent).toContain("读取失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onReload).toHaveBeenCalledOnce();
  });

  it("blocks external-open controls and shortcuts without capability", () => {
    const onOpenExternal = vi.fn();
    render(
      <ArtifactViewerShell
        artifact={artifact()}
        content={{ kind: "external", reason: "unsupported" }}
        onOpenExternal={onOpenExternal}
      />,
    );

    expect(
      (screen.getByRole("button", {
        name: "外部打开",
      }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.keyDown(screen.getByRole("article"), {
      key: "o",
      metaKey: true,
      shiftKey: true,
    });
    expect(onOpenExternal).not.toHaveBeenCalled();
  });

  it("focuses a newly opened artifact and supports its external shortcut", async () => {
    const onOpenExternal = vi.fn();
    render(
      <ArtifactViewerShell
        artifact={artifact({ canOpenExternal: true })}
        content={{ kind: "external", reason: "unsupported" }}
        onOpenExternal={onOpenExternal}
      />,
    );

    const shell = screen.getByRole("article");
    await waitFor(() => expect(document.activeElement).toBe(shell));
    fireEvent.keyDown(shell, {
      key: "o",
      ctrlKey: true,
      shiftKey: true,
    });
    expect(onOpenExternal).toHaveBeenCalledOnce();
  });

  it("surfaces image loading failures", () => {
    render(
      <ArtifactViewerShell
        artifact={{
          ...artifact(),
          previewKind: "image",
          mimeType: "image/png",
          name: "preview.png",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.png",
          mimeType: "image/png",
        }}
      />,
    );

    expect(screen.getByRole("status").textContent).toContain("正在加载图片");
    fireEvent.error(screen.getByRole("img", { name: "preview.png" }));
    expect(screen.getByRole("alert").textContent).toContain("无法加载图片");
  });

  it("surfaces media loading failures", () => {
    const { container } = render(
      <ArtifactViewerShell
        artifact={{
          ...artifact(),
          previewKind: "media",
          mimeType: "video/mp4",
          name: "preview.mp4",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.mp4",
          mimeType: "video/mp4",
        }}
      />,
    );

    const video = container.querySelector("video");
    expect(video).not.toBeNull();
    fireEvent.error(video!);
    expect(screen.getByRole("alert").textContent).toContain("无法加载媒体文件");
  });

  it("removes the PDF loading overlay after the frame loads", () => {
    render(
      <ArtifactViewerShell
        artifact={{
          ...artifact(),
          previewKind: "pdf",
          mimeType: "application/pdf",
          name: "preview.pdf",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.pdf",
          mimeType: "application/pdf",
        }}
      />,
    );

    expect(screen.getByRole("status").textContent).toContain("正在加载 PDF");
    fireEvent.load(screen.getByTitle("preview.pdf"));
    expect(screen.queryByText("正在加载 PDF")).toBeNull();
  });

  it("opens a PDF at the requested one-based page", () => {
    render(
      <ArtifactViewerShell
        artifact={{
          ...artifact(),
          previewKind: "pdf",
          mimeType: "application/pdf",
          name: "preview.pdf",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.pdf#zoom=page-width",
          mimeType: "application/pdf",
        }}
        target={{ page: 12 }}
      />,
    );

    expect(screen.getByTitle("preview.pdf").getAttribute("src")).toBe(
      "https://example.invalid/preview.pdf#zoom=page-width&page=12",
    );
  });

  it("offers retry and external-open recovery when PDF loading times out", () => {
    const onOpenExternal = vi.fn();
    vi.useFakeTimers();
    try {
      render(
        <ArtifactViewerShell
          artifact={{
            ...artifact({ canOpenExternal: true }),
            previewKind: "pdf",
            mimeType: "application/pdf",
            name: "preview.pdf",
          }}
          content={{
            kind: "binary",
            openUrl: "https://example.invalid/preview.pdf",
            mimeType: "application/pdf",
          }}
          onOpenExternal={onOpenExternal}
        />,
      );

      act(() => vi.advanceTimersByTime(15_000));
      const alert = screen.getByRole("alert");
      expect(alert.textContent).toContain("preview.pdf");
      fireEvent.click(
        within(alert).getByRole("button", { name: "外部打开" }),
      );
      expect(onOpenExternal).toHaveBeenCalledOnce();

      fireEvent.click(within(alert).getByRole("button", { name: "重试" }));
      expect(screen.getByRole("status").textContent).toContain("正在加载 PDF");
    } finally {
      vi.useRealTimers();
    }
  });

  it("re-resolves on PDF retry instead of reusing a possibly expired address", () => {
    const onReload = vi.fn();
    vi.useFakeTimers();
    try {
      render(
        <ArtifactViewerShell
          artifact={{
            ...artifact(),
            previewKind: "pdf",
            mimeType: "application/pdf",
            name: "preview.pdf",
          }}
          content={{
            kind: "binary",
            openUrl: "https://example.invalid/preview.pdf",
            mimeType: "application/pdf",
          }}
          onReload={onReload}
        />,
      );

      act(() => vi.advanceTimersByTime(15_000));
      fireEvent.click(
        within(screen.getByRole("alert")).getByRole("button", { name: "重试" }),
      );

      expect(onReload).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });

  it("offers a re-resolving retry when an image fails to load", () => {
    const onReload = vi.fn();
    render(
      <ArtifactViewerShell
        artifact={{
          ...artifact(),
          previewKind: "image",
          mimeType: "image/png",
          name: "preview.png",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.png",
          mimeType: "image/png",
        }}
        onReload={onReload}
      />,
    );

    fireEvent.error(screen.getByRole("img", { name: "preview.png" }));
    fireEvent.click(
      within(screen.getByRole("alert")).getByRole("button", { name: "重试" }),
    );

    expect(onReload).toHaveBeenCalledOnce();
  });

  it("exposes fullscreen controls and a keyboard shortcut for PDFs", () => {
    const originalRequestFullscreen = Object.getOwnPropertyDescriptor(
      Element.prototype,
      "requestFullscreen",
    );
    const requestFullscreen = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(Element.prototype, "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });

    try {
      render(
        <ArtifactViewerShell
          artifact={{
            ...artifact(),
            previewKind: "pdf",
            mimeType: "application/pdf",
            name: "preview.pdf",
          }}
          content={{
            kind: "binary",
            openUrl: "https://example.invalid/preview.pdf",
            mimeType: "application/pdf",
          }}
        />,
      );

      fireEvent.click(screen.getByRole("button", { name: "进入全屏" }));
      expect(requestFullscreen).toHaveBeenCalledOnce();
      fireEvent.keyDown(screen.getByRole("article"), {
        key: "f",
        ctrlKey: true,
        shiftKey: true,
      });
      expect(requestFullscreen).toHaveBeenCalledTimes(2);
    } finally {
      if (originalRequestFullscreen) {
        Object.defineProperty(
          Element.prototype,
          "requestFullscreen",
          originalRequestFullscreen,
        );
      } else {
        Reflect.deleteProperty(Element.prototype, "requestFullscreen");
      }
    }
  });
});
