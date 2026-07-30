import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  Minus,
  Plus,
  RotateCw,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  PDFPageProxy,
} from "pdfjs-dist";
import type {
  NormalizedRectV1,
  TextQuoteSelectorV1,
} from "@valuz/shared";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { useI18n } from "../../hooks/use-i18n";
import type { DocumentLocation } from "./document-reader.types";
import { findBestTextQuote } from "./text-quote";
import "./PdfDocumentRenderer.css";

type LocateStatus =
  | "located-exact"
  | "located-fallback"
  | "page-only"
  | "not-found";
type PdfTextContent = Awaited<ReturnType<PDFPageProxy["getTextContent"]>>;

interface PixelRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function mapNormalizedPdfRects(
  rects: NormalizedRectV1[] | undefined,
  width: number,
  height: number,
): PixelRect[] {
  if (!rects || width <= 0 || height <= 0) return [];
  return rects.flatMap((rect) => {
    const values = [rect.x, rect.y, rect.width, rect.height];
    if (
      values.some((value) => !Number.isFinite(value)) ||
      rect.x < 0 ||
      rect.y < 0 ||
      rect.width <= 0 ||
      rect.height <= 0 ||
      rect.x + rect.width > 1.000001 ||
      rect.y + rect.height > 1.000001
    ) {
      return [];
    }
    return [
      {
        left: rect.x * width,
        top: rect.y * height,
        width: rect.width * width,
        height: rect.height * height,
      },
    ];
  });
}

export function locatePdfTextItemIndexes(
  items: PdfTextContent["items"],
  selector: TextQuoteSelectorV1,
): number[] {
  let raw = "";
  const rawItemIndexes: number[] = [];
  let textDivIndex = 0;
  items.forEach((item) => {
    if (!("str" in item)) return;
    if (raw) {
      raw += " ";
      rawItemIndexes.push(Math.max(0, textDivIndex - 1));
    }
    raw += item.str;
    rawItemIndexes.push(
      ...Array.from({ length: item.str.length }, () => textDivIndex),
    );
    textDivIndex += 1;
  });
  const match = findBestTextQuote(raw, selector);
  if (!match) return [];
  return Array.from(
    new Set(rawItemIndexes.slice(match.start, match.end)),
  ).filter((index) => index >= 0);
}

export function canUseNormalizedPdfRects(input: {
  rects?: NormalizedRectV1[];
  locatorPageRotation?: number;
  documentPageRotation: number;
  viewerRotation: number;
}): boolean {
  return (
    Boolean(input.rects?.length) &&
    input.viewerRotation === 0 &&
    (input.locatorPageRotation === undefined
      ? input.documentPageRotation === 0
      : input.locatorPageRotation === input.documentPageRotation)
  );
}

function textLayerRects(
  itemIndexes: number[],
  textDivs: HTMLElement[],
  pageElement: HTMLElement,
): PixelRect[] {
  const pageBox = pageElement.getBoundingClientRect();
  return itemIndexes.flatMap((index) => {
    const div = textDivs[index];
    if (!div) return [];
    const box = div.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return [];
    return [
      {
        left: box.left - pageBox.left,
        top: box.top - pageBox.top,
        width: box.width,
        height: box.height,
      },
    ];
  });
}

function HighlightLayer({
  rects,
  status,
}: {
  rects: PixelRect[];
  status: LocateStatus;
}) {
  return (
    <div
      className="pointer-events-none absolute inset-0 z-20"
      data-citation-pdf-highlight={status}
    >
      {rects.map((rect, index) => (
        <span
          // Geometry and order together are stable for one location render.
          key={`${rect.left}:${rect.top}:${index}`}
          className="absolute rounded-sm bg-warning/35 ring-1 ring-warning/70 mix-blend-multiply"
          style={{
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
          }}
        />
      ))}
    </div>
  );
}

function PdfPage({
  pdf,
  pageNumber,
  scale,
  rotation,
  location,
  onLocated,
}: {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  scale: number;
  rotation: number;
  location?: DocumentLocation;
  onLocated: (page: number, status: LocateStatus) => void;
}) {
  const { t } = useI18n();
  const pageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const [page, setPage] = useState<PDFPageProxy | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [highlightRects, setHighlightRects] = useState<PixelRect[]>([]);
  const [locateStatus, setLocateStatus] =
    useState<LocateStatus>("page-only");

  useEffect(() => {
    let cancelled = false;
    void pdf.getPage(pageNumber).then((next) => {
      if (!cancelled) setPage(next);
    });
    return () => {
      cancelled = true;
      setPage(null);
    };
  }, [pageNumber, pdf]);

  useEffect(() => {
    if (!page || !canvasRef.current || !textLayerRef.current) return;
    let disposed = false;
    let renderTask: ReturnType<PDFPageProxy["render"]> | null = null;
    let textLayer: { cancel(): void } | null = null;

    const render = async () => {
      const pdfjs = await import("pdfjs-dist");
      const viewport = page.getViewport({
        scale,
        rotation: (page.rotate + rotation) % 360,
      });
      if (disposed || !canvasRef.current || !textLayerRef.current) return;
      setSize({ width: viewport.width, height: viewport.height });

      const canvas = canvasRef.current;
      const outputScale = Math.max(window.devicePixelRatio || 1, 1);
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("canvas_context_unavailable");
      renderTask = page.render({
        canvas,
        viewport,
        transform:
          outputScale === 1
            ? undefined
            : [outputScale, 0, 0, outputScale, 0, 0],
      });

      const textContent = await page.getTextContent();
      if (disposed) return;
      const textContainer = textLayerRef.current;
      textContainer.replaceChildren();
      textContainer.style.setProperty(
        "--total-scale-factor",
        String(viewport.scale),
      );
      const layer = new pdfjs.TextLayer({
        textContentSource: textContent,
        container: textContainer,
        viewport,
      });
      textLayer = layer;
      await Promise.all([renderTask.promise, layer.render()]);
      if (disposed || !pageRef.current) return;

      let rects: PixelRect[] = [];
      let status: LocateStatus = "page-only";
      if (
        canUseNormalizedPdfRects({
          rects: location?.rects,
          locatorPageRotation: location?.pageRotation,
          documentPageRotation: page.rotate,
          viewerRotation: rotation,
        })
      ) {
        rects = mapNormalizedPdfRects(
          location?.rects,
          viewport.width,
          viewport.height,
        );
      }
      if (rects.length) {
        status = "located-exact";
      } else if (location?.quote) {
        const indexes = locatePdfTextItemIndexes(
          textContent.items,
          location.quote,
        );
        rects = textLayerRects(indexes, layer.textDivs, pageRef.current);
        status = rects.length ? "located-fallback" : "page-only";
      }
      setHighlightRects(rects);
      setLocateStatus(status);
      onLocated(pageNumber, status);
    };

    void render().catch(() => {
      if (!disposed) {
        setHighlightRects([]);
        setLocateStatus("page-only");
        onLocated(pageNumber, "page-only");
      }
    });
    return () => {
      disposed = true;
      renderTask?.cancel();
      textLayer?.cancel();
      setHighlightRects([]);
    };
  }, [location, onLocated, page, pageNumber, rotation, scale]);

  return (
    <section
      ref={pageRef}
      data-pdf-page={pageNumber}
      data-locate-status={locateStatus}
      className="relative mx-auto shrink-0 overflow-hidden bg-white shadow-sm"
      style={{ width: size.width || 612, height: size.height || 792 }}
    >
      {!page ? (
        <div className="absolute inset-0 flex items-center justify-center text-ink-meta">
          <Loader2 className="h-4 w-4 animate-spin" />
        </div>
      ) : null}
      <canvas ref={canvasRef} className="absolute inset-0" />
      <div
        ref={textLayerRef}
        className="valuz-pdf-text-layer z-10"
        aria-label={t("ui.reader.pdfPageText", { page: pageNumber })}
      />
      <HighlightLayer rects={highlightRects} status={locateStatus} />
    </section>
  );
}

function pageWindow(current: number, total: number): number[] {
  return [current - 1, current, current + 1].filter(
    (page) => page >= 1 && page <= total,
  );
}

export function PdfDocumentRenderer({
  url,
  title,
  location,
  onReload,
  onLoadError,
}: {
  url: string;
  title: string;
  location?: DocumentLocation;
  onReload?: () => void;
  onLoadError?: () => void;
}) {
  const { t } = useI18n();
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState(1.25);
  const [rotation, setRotation] = useState(0);
  const [currentPage, setCurrentPage] = useState(
    Math.max(1, location?.page ?? 1),
  );
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setCurrentPage(Math.max(1, location?.page ?? 1));
  }, [location?.page]);

  useEffect(() => {
    let task: PDFDocumentLoadingTask | null = null;
    let cancelled = false;
    setPdf(null);
    setError(null);
    void import("pdfjs-dist")
      .then((pdfjs) => {
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        task = pdfjs.getDocument({ url });
        return task.promise;
      })
      .then((document) => {
        if (cancelled) return;
        setPdf(document);
        setCurrentPage((page) => Math.min(page, document.numPages));
      })
      .catch((cause) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "pdf_load_failed");
        }
      });
    return () => {
      cancelled = true;
      void task?.destroy();
    };
  }, [url]);

  useEffect(() => {
    if (error) onLoadError?.();
  }, [error, onLoadError]);

  const onLocated = useCallback(
    (page: number) => {
      if (page !== currentPage) return;
      const target = scrollRef.current?.querySelector<HTMLElement>(
        `[data-pdf-page="${page}"]`,
      );
      if (!target) return;
      const reduced = window.matchMedia?.(
        "(prefers-reduced-motion: reduce)",
      ).matches;
      target.scrollIntoView({
        block: "center",
        behavior: reduced ? "auto" : "smooth",
      });
    },
    [currentPage],
  );

  const pages = useMemo(
    () => (pdf ? pageWindow(currentPage, pdf.numPages) : []),
    [currentPage, pdf],
  );

  if (error) {
    const hash = location?.page ? `#page=${location.page}` : "";
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-between border-b border-surface-border bg-warning-light px-3 py-2 text-xs text-warning-text">
          <span>{t("ui.reader.pdfFallback")}</span>
          {onReload ? (
            <button type="button" onClick={onReload} className="font-medium">
              {t("common.retry")}
            </button>
          ) : null}
        </div>
        <iframe
          src={`${url}${hash}`}
          title={title}
          className="min-h-0 flex-1 border-0"
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-soft">
      <div className="flex h-10 shrink-0 items-center justify-center gap-1 border-b border-surface-border bg-surface px-2">
        <button
          type="button"
          aria-label={t("ui.reader.previousPage")}
          disabled={!pdf || currentPage <= 1}
          onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
          className="rounded p-1.5 hover:bg-surface-muted disabled:opacity-40"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
        <span className="min-w-16 text-center text-xs tabular-nums text-ink-body">
          {currentPage} / {pdf?.numPages ?? "…"}
        </span>
        <button
          type="button"
          aria-label={t("ui.reader.nextPage")}
          disabled={!pdf || currentPage >= pdf.numPages}
          onClick={() =>
            setCurrentPage((page) =>
              pdf ? Math.min(pdf.numPages, page + 1) : page,
            )
          }
          className="rounded p-1.5 hover:bg-surface-muted disabled:opacity-40"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
        <span className="mx-1 h-5 w-px bg-surface-border" />
        <button
          type="button"
          aria-label={t("ui.reader.zoomOut")}
          onClick={() => setScale((value) => Math.max(0.6, value - 0.15))}
          className="rounded p-1.5 hover:bg-surface-muted"
        >
          <Minus className="h-3.5 w-3.5" />
        </button>
        <span className="min-w-10 text-center text-xs tabular-nums text-ink-meta">
          {Math.round(scale * 100)}%
        </span>
        <button
          type="button"
          aria-label={t("ui.reader.zoomIn")}
          onClick={() => setScale((value) => Math.min(3, value + 0.15))}
          className="rounded p-1.5 hover:bg-surface-muted"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          aria-label={t("ui.reader.rotateClockwise")}
          onClick={() => setRotation((value) => (value + 90) % 360)}
          className="rounded p-1.5 hover:bg-surface-muted"
        >
          <RotateCw className="h-3.5 w-3.5" />
        </button>
      </div>
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 space-y-4 overflow-auto p-4"
        onKeyDown={(event) => {
          if (event.key === "PageDown" || event.key === "ArrowRight") {
            setCurrentPage((page) =>
              pdf ? Math.min(pdf.numPages, page + 1) : page,
            );
          } else if (
            event.key === "PageUp" ||
            event.key === "ArrowLeft"
          ) {
            setCurrentPage((page) => Math.max(1, page - 1));
          }
        }}
        tabIndex={0}
      >
        {!pdf ? (
          <div className="flex h-full items-center justify-center text-sm text-ink-meta">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            {t("ui.reader.loadingPdf")}
          </div>
        ) : (
          pages.map((pageNumber) => (
            <PdfPage
              key={`${pageNumber}:${scale}:${rotation}`}
              pdf={pdf}
              pageNumber={pageNumber}
              scale={scale}
              rotation={rotation}
              location={
                pageNumber === location?.page ? location : undefined
              }
              onLocated={onLocated}
            />
          ))
        )}
      </div>
    </div>
  );
}
