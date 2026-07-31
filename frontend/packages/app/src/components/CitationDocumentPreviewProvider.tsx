import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  buildLocalFileUrl,
  citationsApi,
  type PlatformCapabilities,
  useTranslation,
} from "@valuz/core";
import type {
  CitationLocatorV1,
  OpenCitationInput,
  ResolvedCitationChunk,
  ResolvedCitationDocumentSource,
} from "@valuz/shared";
import {
  DocumentReaderView,
  type DocumentChunk,
  type DocumentLocation,
  type DocumentSource,
} from "@valuz/ui";
import DOMPurify from "dompurify";

import { usePlatform } from "../platform";
import { DocumentResearchPanel } from "./DocumentResearchPanel";

interface CitationOpenTarget {
  sessionId: string;
  messageId: string;
  citationId: string;
}

interface CitationDocumentPreviewContextValue {
  openCitation: (
    input: OpenCitationInput & { sessionId: string },
  ) => void;
  closeCitation: () => void;
}

const CitationDocumentPreviewContext =
  createContext<CitationDocumentPreviewContextValue | null>(null);

export function citationResolutionI18nKey(
  reason: string | null | undefined,
): string {
  switch (reason) {
    case "citation_has_no_document":
    case "citation_has_no_readable_document":
      return "ui.citation.noReadableDocument";
    case "document_address_unavailable":
      return "ui.citation.documentAddressUnavailable";
    case "document_version_changed":
      return "ui.reader.locationDegraded";
    case "external_reader_unavailable":
      return "ui.reader.externalOnly";
    default:
      return "ui.citation.unavailable";
  }
}

export function useCitationDocumentPreview(): CitationDocumentPreviewContextValue {
  const value = useContext(CitationDocumentPreviewContext);
  if (!value) {
    throw new Error(
      "useCitationDocumentPreview must be used inside CitationDocumentPreviewProvider",
    );
  }
  return value;
}

export function encodeCitationOpenRef(target: CitationOpenTarget): string {
  const json = JSON.stringify([target.sessionId, target.messageId, target.citationId]);
  const bytes = new TextEncoder().encode(json);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
}

export function decodeCitationOpenRef(value: string): CitationOpenTarget | null {
  if (!value || value.length > 2048 || !/^[A-Za-z0-9_-]+$/.test(value)) {
    return null;
  }
  try {
    const base64 = value.replaceAll("-", "+").replaceAll("_", "/");
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    const decoded: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (
      !Array.isArray(decoded) ||
      decoded.length !== 3 ||
      decoded.some((part) => typeof part !== "string" || !part)
    ) {
      return null;
    }
    return {
      sessionId: decoded[0] as string,
      messageId: decoded[1] as string,
      citationId: decoded[2] as string,
    };
  } catch {
    return null;
  }
}

function addressUrl(
  source: ResolvedCitationDocumentSource,
  platform: PlatformCapabilities,
): string | null {
  if (source.render.kind !== "file") return null;
  const { address } = source.render;
  if (address.kind === "remote") return address.url;
  if (address.kind === "local" && address.absPath && platform.isElectron) {
    return buildLocalFileUrl(address.absPath);
  }
  return null;
}

function sanitizeChunks(
  chunks: ResolvedCitationChunk[] | undefined,
): DocumentChunk[] | undefined {
  return chunks?.map((chunk) => ({
    ...chunk,
    html: chunk.html
      ? DOMPurify.sanitize(chunk.html, {
          FORBID_TAGS: ["script", "iframe", "object", "embed"],
          FORBID_ATTR: ["srcdoc"],
        })
      : undefined,
  }));
}

export async function materializeCitationDocument(
  source: ResolvedCitationDocumentSource,
  platform: PlatformCapabilities,
  signal?: AbortSignal,
): Promise<DocumentSource> {
  const common = {
    id: source.id,
    title: source.title,
    source: source.source,
    chunks: sanitizeChunks(source.chunks),
    documentVersion: source.documentVersion,
    originalUrl: source.originalUrl ?? undefined,
  };
  if (source.render.kind === "chunks") {
    return {
      ...common,
      render: {
        kind: "chunks",
        chunks: sanitizeChunks(source.render.chunks) ?? [],
      },
    };
  }
  if (source.render.kind === "html") {
    return {
      ...common,
      render: {
        kind: "html",
        html: DOMPurify.sanitize(source.render.html, {
          FORBID_TAGS: ["script", "iframe", "object", "embed"],
          FORBID_ATTR: ["srcdoc"],
        }),
      },
    };
  }
  if (source.render.kind === "external") {
    return {
      ...common,
      originalUrl: source.render.url,
      render: source.render,
    };
  }

  const { mimeType, address } = source.render;
  if (mimeType === "text/html") {
    let html: string | null = null;
    if (address.kind === "remote" && address.url) {
      const response = await fetch(address.url, { signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      html = await response.text();
    } else if (
      address.kind === "local" &&
      address.absPath &&
      platform.readFileContent
    ) {
      html = (await platform.readFileContent(address.absPath)).content;
    }
    if (html === null) throw new Error("document_address_unavailable");
    return {
      ...common,
      render: {
        kind: "html",
        html: DOMPurify.sanitize(html, {
          FORBID_TAGS: ["script", "iframe", "object", "embed"],
          FORBID_ATTR: ["srcdoc"],
        }),
      },
    };
  }

  const url = addressUrl(source, platform);
  if (!url) throw new Error("document_address_unavailable");
  return { ...common, render: { kind: "file", url, mimeType } };
}

export function locatorToDocumentLocation(
  locator: CitationLocatorV1 | null,
): DocumentLocation | undefined {
  if (!locator) return undefined;
  if (locator.kind === "chunk") {
    return {
      kind: "chunk",
      chunkId: locator.chunkId,
      segmentId: locator.segmentId,
      quote: locator.quote,
    };
  }
  if (locator.kind === "html") {
    return {
      kind: "html",
      chunkId: locator.chunkId,
      elementId: locator.elementId,
      cssSelector: locator.cssSelector,
      quote: locator.quote,
    };
  }
  if (locator.kind === "pdf") {
    return {
      kind: "pdf",
      page: locator.page,
      rects: locator.rects,
      quote: locator.quote,
      pageRotation: locator.pageRotation,
    };
  }
  return { kind: "external" };
}

export function CitationDocumentPreviewProvider({
  children,
}: {
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const platform = usePlatform();
  const location = useLocation();
  const navigate = useNavigate();
  const [target, setTarget] = useState<CitationOpenTarget | null>(null);
  const [originTarget, setOriginTarget] =
    useState<CitationOpenTarget | null>(null);
  const [resolvedDocument, setResolvedDocument] =
    useState<DocumentSource | null>(null);
  const [documentLocation, setDocumentLocation] = useState<
    DocumentLocation | undefined
  >(undefined);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resolutionNotice, setResolutionNotice] = useState<string | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const automaticRefreshesRef = useRef(new Set<string>());

  const setCitationQuery = useCallback(
    (next: CitationOpenTarget | null) => {
      const params = new URLSearchParams(location.search);
      if (next) params.set("citation", encodeCitationOpenRef(next));
      else params.delete("citation");
      const search = params.toString();
      navigate(
        {
          pathname: location.pathname,
          search: search ? `?${search}` : "",
          hash: location.hash,
        },
        { replace: true },
      );
    },
    [location.hash, location.pathname, location.search, navigate],
  );

  const openCitation = useCallback(
    (input: OpenCitationInput & { sessionId: string }) => {
      if (!input.messageId) return;
      openerRef.current =
        window.document.activeElement instanceof HTMLElement
          ? window.document.activeElement
          : null;
      const next = {
        sessionId: input.sessionId,
        messageId: input.messageId,
        citationId: input.citationId,
      };
      setTarget(next);
      setOriginTarget(next);
      setCitationQuery(next);
    },
    [setCitationQuery],
  );

  const closeCitation = useCallback(() => {
    setTarget(null);
    setOriginTarget(null);
    setResolvedDocument(null);
    setDocumentLocation(undefined);
    setError(null);
    setResolutionNotice(null);
    automaticRefreshesRef.current.clear();
    setCitationQuery(null);
    window.requestAnimationFrame(() => openerRef.current?.focus());
  }, [setCitationQuery]);

  useEffect(() => {
    const encoded = new URLSearchParams(location.search).get("citation");
    if (!encoded) return;
    const decoded = decodeCitationOpenRef(encoded);
    if (!decoded) return;
    if (
      target?.sessionId === decoded.sessionId &&
      target.messageId === decoded.messageId &&
      target.citationId === decoded.citationId
    ) {
      return;
    }
    setTarget(decoded);
    if (!originTarget) setOriginTarget(decoded);
  }, [location.search, originTarget, target]);

  const load = useCallback(
    async (active: CitationOpenTarget, signal: AbortSignal) => {
      setLoading(true);
      setError(null);
      setResolutionNotice(null);
      try {
        const result = await citationsApi.resolve(active, { signal });
        if (!result.document) {
          throw new Error(
            citationResolutionI18nKey(result.fallback_reason),
          );
        }
        const resolved = await materializeCitationDocument(
          result.document,
          platform,
          signal,
        );
        if (signal.aborted) return;
        setResolvedDocument(resolved);
        setDocumentLocation(
          locatorToDocumentLocation(result.effective_locator),
        );
        setResolutionNotice(
          result.status === "stale" || result.status === "degraded"
            ? t(citationResolutionI18nKey(result.fallback_reason))
            : null,
        );
      } catch (cause) {
        if (signal.aborted) return;
        setResolvedDocument(null);
        const reason =
          cause instanceof Error
            ? cause.message
            : "ui.citation.unavailable";
        setError(
          reason.startsWith("ui.")
            ? t(reason)
            : t(citationResolutionI18nKey(reason)),
        );
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [platform, t],
  );

  useEffect(() => {
    if (!target) return;
    const controller = new AbortController();
    void load(target, controller.signal);
    return () => controller.abort();
  }, [load, target]);

  const refreshExpiredAddress = useCallback(() => {
    if (!target) return;
    const key = `${target.sessionId}\0${target.messageId}\0${target.citationId}`;
    if (automaticRefreshesRef.current.has(key)) return;
    automaticRefreshesRef.current.add(key);
    const controller = new AbortController();
    void load(target, controller.signal);
  }, [load, target]);

  useEffect(() => {
    if (!target) return;
    dialogRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeCitation();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeCitation, target]);

  const context = useMemo(
    () => ({ openCitation, closeCitation }),
    [closeCitation, openCitation],
  );

  return (
    <CitationDocumentPreviewContext.Provider value={context}>
      {children}
      {target ? (
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-label={t("ui.citation.documentDialog")}
          tabIndex={-1}
          className="fixed inset-0 z-[80] overflow-hidden bg-surface p-2 outline-none sm:p-3"
          onWheel={(event) => event.stopPropagation()}
          onTouchMove={(event) => event.stopPropagation()}
        >
          <DocumentReaderView
            doc={resolvedDocument}
            location={documentLocation}
            loading={loading}
            error={error}
            onReload={() => {
              if (!target) return;
              const controller = new AbortController();
              void load(target, controller.signal);
            }}
            onLoadError={refreshExpiredAddress}
            onClose={closeCitation}
            sidePanel={
              <DocumentResearchPanel
                document={resolvedDocument}
                resolutionNotice={resolutionNotice}
                originSessionId={originTarget?.sessionId}
                originMessageId={originTarget?.messageId}
                onCitationClick={(sessionId, input) => {
                  if (!input.messageId) return;
                  const next = {
                    sessionId,
                    messageId: input.messageId,
                    citationId: input.citationId,
                  };
                  setTarget(next);
                  setCitationQuery(next);
                }}
              />
            }
          />
        </div>
      ) : null}
    </CitationDocumentPreviewContext.Provider>
  );
}
