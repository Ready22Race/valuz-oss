import { ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import type { ArtifactRendererProps } from "./artifact-viewer.types";

type PdfLoadState = "loading" | "ready" | "error";
export const PDF_LOAD_TIMEOUT_MS = 15_000;

function pdfUrlWithTarget(url: string, page?: number): string {
  if (!Number.isSafeInteger(page) || (page ?? 0) < 1) return url;
  const hashIndex = url.indexOf("#");
  const baseUrl = hashIndex === -1 ? url : url.slice(0, hashIndex);
  const fragment = hashIndex === -1 ? "" : url.slice(hashIndex + 1);
  const params = new URLSearchParams(fragment);
  params.set("page", String(page));
  return `${baseUrl}#${params.toString()}`;
}

export function PdfRenderer({
  artifact,
  content,
  target,
  onOpenExternal,
}: ArtifactRendererProps) {
  const [loadState, setLoadState] = useState<PdfLoadState>("loading");
  const [frameKey, setFrameKey] = useState(0);
  const basePdfUrl =
    content?.kind === "binary" && content.mimeType === "application/pdf"
      ? content.openUrl
      : null;
  const pdfUrl = basePdfUrl
    ? pdfUrlWithTarget(basePdfUrl, target?.page)
    : null;

  useEffect(() => {
    setLoadState("loading");
    setFrameKey(0);
  }, [pdfUrl]);

  useEffect(() => {
    if (!pdfUrl || loadState !== "loading") return;
    const timeout = window.setTimeout(
      () => setLoadState("error"),
      PDF_LOAD_TIMEOUT_MS,
    );
    return () => window.clearTimeout(timeout);
  }, [frameKey, loadState, pdfUrl]);

  if (!pdfUrl) {
    return (
      <div className="flex h-full items-center justify-center px-6 py-16">
        <div
          className="max-w-md rounded-lg border border-error-light bg-error-light px-5 py-4 text-error-text"
          role="alert"
        >
          <div className="text-sm font-medium">无法预览 PDF</div>
          <p className="mt-1 text-xs leading-5">
            当前文件没有可用的 PDF 访问地址。
          </p>
          {onOpenExternal ? (
            <button
              type="button"
              onClick={onOpenExternal}
              className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-md border border-error-text/20 bg-surface px-3 text-xs font-medium text-error-text transition hover:bg-surface-soft"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              外部打开
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  const retry = () => {
    setLoadState("loading");
    setFrameKey((current) => current + 1);
  };

  return (
    <div className="relative h-full min-h-0 overflow-hidden bg-surface-base">
      {loadState === "loading" ? (
        <div
          className="absolute inset-0 z-10 flex items-center justify-center bg-surface-base/85 text-sm text-ink-meta"
          role="status"
          aria-live="polite"
        >
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          正在加载 PDF
        </div>
      ) : null}
      {loadState === "error" ? (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-surface-base px-6 py-16">
          <div
            className="max-w-md rounded-lg border border-error-light bg-error-light px-5 py-4 text-error-text"
            role="alert"
          >
            <div className="text-sm font-medium">无法加载 PDF</div>
            <p className="mt-1 text-xs leading-5">
              请重试，或使用系统应用打开 {artifact.name}。
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={retry}
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-error-text/20 bg-surface px-3 text-xs font-medium text-error-text transition hover:bg-surface-soft"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                重试
              </button>
              {onOpenExternal ? (
                <button
                  type="button"
                  onClick={onOpenExternal}
                  className="inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium text-error-text transition hover:bg-surface-soft"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  外部打开
                </button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
      <iframe
        key={frameKey}
        src={pdfUrl}
        title={artifact.name}
        allowFullScreen
        onLoad={() => setLoadState("ready")}
        onError={() => setLoadState("error")}
        className={`h-full w-full border-0 bg-surface transition-opacity duration-150 ${
          loadState === "ready" ? "opacity-100" : "opacity-0"
        }`}
      />
    </div>
  );
}
