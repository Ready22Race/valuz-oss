import { useMemo, useState } from "react";
import { ExternalLink } from "lucide-react";
import type {
  CitationBundleV1,
  CitationRefV1,
  OpenCitationInput,
} from "@valuz/shared";

import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

const CITATION_HREF_PREFIX = "https://valuz.citation.invalid/";
const CITATION_URI_PATTERN = /citation:\/\/([A-Za-z0-9._~:-]+)/g;

export function rewriteCitationMarkdownLinks(content: string): string {
  return content.replace(CITATION_URI_PATTERN, (_whole, citationId: string) => {
    return `${CITATION_HREF_PREFIX}${encodeURIComponent(citationId)}`;
  });
}

export function citationIdFromHref(href?: string): string | null {
  if (!href?.startsWith(CITATION_HREF_PREFIX)) return null;
  try {
    const citationId = decodeURIComponent(href.slice(CITATION_HREF_PREFIX.length));
    return citationId || null;
  } catch {
    return null;
  }
}

export function citationDisplayOrder(content: string): Map<string, number> {
  const order = new Map<string, number>();
  for (const match of content.matchAll(CITATION_URI_PATTERN)) {
    const citationId = match[1];
    if (citationId && !order.has(citationId)) {
      order.set(citationId, order.size + 1);
    }
  }
  return order;
}

export function usedCitations(
  content: string,
  bundle?: CitationBundleV1,
): Array<{ displayIndex: number; citation: CitationRefV1 }> {
  if (!bundle) return [];
  const byId = new Map(bundle.citations.map((citation) => [citation.citationId, citation]));
  return Array.from(citationDisplayOrder(content), ([citationId, displayIndex]) => {
    const citation = byId.get(citationId);
    return citation ? { displayIndex, citation } : null;
  }).filter(
    (
      item,
    ): item is { displayIndex: number; citation: CitationRefV1 } => item !== null,
  );
}

function evidenceText(citation: CitationRefV1): {
  quote: string;
  snippet?: string;
  time?: string;
} {
  const evidence = citation.evidence;
  if (evidence.kind === "text") {
    return {
      quote: evidence.quote,
      snippet:
        evidence.snippet && evidence.snippet !== evidence.quote
          ? evidence.snippet
          : undefined,
      time: citation.source.publishedAt ?? evidence.capturedAt,
    };
  }
  if (evidence.kind === "structured-data") {
    const suffix = [evidence.unit, evidence.period ?? evidence.asOf]
      .filter(Boolean)
      .join(" · ");
    return {
      quote: `${evidence.field}: ${String(evidence.value)}${suffix ? ` (${suffix})` : ""}`,
      time: evidence.asOf ?? evidence.capturedAt,
    };
  }
  return {
    quote: `${evidence.expression} = ${String(evidence.result)}${
      evidence.unit ? ` ${evidence.unit}` : ""
    }`,
    time: evidence.calculatedAt,
  };
}

function qualityBadge(
  citation: CitationRefV1,
): { label: string; status?: string } | null {
  const value = citation.annotations?.quality;
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const label = typeof record.label === "string" ? record.label : "";
  const status = typeof record.status === "string" ? record.status : undefined;
  return label ? { label, status } : null;
}

function CitationHoverCard({
  displayIndex,
  citation,
  canOpen,
  onOpen,
  citationById,
  onOpenCitation,
}: {
  displayIndex: number;
  citation: CitationRefV1;
  canOpen: boolean;
  onOpen: () => void;
  citationById?: ReadonlyMap<string, CitationRefV1>;
  onOpenCitation: (citationId: string) => void;
}) {
  const { t } = useI18n();
  const detail = evidenceText(citation);
  const attribution =
    citation.source.organization ?? citation.source.author ?? citation.source.providerId;
  const quality = qualityBadge(citation);
  const calculationInputs =
    citation.evidence.kind === "calculation"
      ? citation.evidence.inputs.flatMap((input) => {
          const source = citationById?.get(input.citationId);
          return source ? [{ input, source }] : [];
        })
      : [];

  return (
    <span
      role="tooltip"
      className="absolute bottom-full left-1/2 z-50 mb-2 w-[min(360px,calc(100vw-32px))] -translate-x-1/2 rounded-lg border border-surface-border bg-surface p-3 text-left text-xs font-normal text-ink-body shadow-xl"
    >
      <span className="flex items-start gap-2">
        <span className="min-w-0 flex-1">
          <span className="block font-medium text-ink-heading">
            {displayIndex} {citation.source.title}
          </span>
          <span className="mt-0.5 block text-ink-meta">
            {[attribution, detail.time].filter(Boolean).join(" · ")}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-1">
          {quality ? (
            <span
              data-citation-quality={quality.status}
              className={cn(
                "rounded px-1.5 py-0.5 text-2xs",
                quality.status === "passed"
                  ? "bg-success-light text-success"
                  : "bg-warning-light text-warning-text",
              )}
            >
              {quality.label}
            </span>
          ) : null}
          {citation.resolutionStatus &&
          citation.resolutionStatus !== "ready" ? (
            <span className="rounded bg-surface-muted px-1.5 py-0.5 text-2xs text-ink-meta">
              {citation.resolutionStatus}
            </span>
          ) : null}
        </span>
      </span>
      <q className="mt-2 block border-l-2 border-primary/40 pl-2 leading-5 text-ink-heading">
        {detail.quote}
      </q>
      {detail.snippet ? (
        <span className="mt-1.5 block line-clamp-3 leading-5 text-ink-meta">
          {detail.snippet}
        </span>
      ) : null}
      {calculationInputs.length ? (
        <span className="mt-2 block border-t border-surface-border pt-2">
          <span className="block text-2xs font-medium uppercase tracking-wide text-ink-meta">
            {t("ui.citation.calculationInputs", "Calculation inputs")}
          </span>
          <span className="mt-1 flex flex-col gap-1">
            {calculationInputs.map(({ input, source }) => {
              const disabled =
                !canOpen ||
                source.resolutionStatus === "forbidden" ||
                source.resolutionStatus === "missing";
              return (
                <button
                  key={`${input.name}:${input.citationId}`}
                  type="button"
                  disabled={disabled}
                  className="rounded px-1.5 py-1 text-left text-2xs text-ink-body hover:bg-surface-muted disabled:cursor-default disabled:opacity-60"
                  onClick={(event) => {
                    event.stopPropagation();
                    onOpenCitation(input.citationId);
                  }}
                >
                  <span className="font-medium">{input.name}</span>
                  <span className="text-ink-meta">
                    {" "}
                    · {String(input.value)}
                    {input.unit ? ` ${input.unit}` : ""} ·{" "}
                    {source.source.title}
                  </span>
                </button>
              );
            })}
          </span>
        </span>
      ) : null}
      {canOpen ? (
        <button
          type="button"
          className="mt-2 inline-flex items-center gap-1 font-medium text-primary hover:underline"
          onClick={(event) => {
            event.stopPropagation();
            onOpen();
          }}
        >
          {t("ui.citation.openSource", "Open source")}
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
        </button>
      ) : null}
    </span>
  );
}

export function CitationPill({
  citationId,
  displayIndex,
  citation,
  citationById,
  messageId,
  onCitationClick,
}: {
  citationId: string;
  displayIndex?: number;
  citation?: CitationRefV1;
  citationById?: ReadonlyMap<string, CitationRefV1>;
  messageId?: string;
  onCitationClick?: (input: OpenCitationInput) => void;
}) {
  const { t } = useI18n();
  const [hovered, setHovered] = useState(false);
  const canOpen =
    Boolean(citation) &&
    citation?.resolutionStatus !== "forbidden" &&
    citation?.resolutionStatus !== "missing" &&
    Boolean(onCitationClick);
  // Numbering belongs to the message body, not the sidecar.  A newer/missing
  // bundle must still render a stable, non-interactive number instead of
  // replacing the user's citation position with an ambiguous question mark.
  const indexLabel = displayIndex ? String(displayIndex) : "?";
  const open = () => {
    if (!canOpen) return;
    onCitationClick?.({ messageId, citationId });
  };
  const openCitation = (nextCitationId: string) => {
    if (!onCitationClick) return;
    onCitationClick({ messageId, citationId: nextCitationId });
  };

  return (
    <span
      className="relative -top-px mx-0.5 inline-flex align-middle leading-none"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocusCapture={() => setHovered(true)}
      onBlurCapture={(event) => {
        const next = event.relatedTarget;
        if (!(next instanceof Node) || !event.currentTarget.contains(next)) {
          setHovered(false);
        }
      }}
    >
      <button
        type="button"
        aria-label={
          citation && displayIndex
            ? t("ui.citation.ariaLabel", "Citation {index}", {
                index: displayIndex,
              })
            : t("ui.citation.unavailable", "Citation unavailable")
        }
        aria-disabled={!canOpen}
        className={cn(
          "inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border p-0 text-2xs font-medium tabular-nums no-underline transition-colors",
          citation
            ? "border-surface-border bg-surface-muted text-ink-body hover:text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20"
            : "cursor-default border-surface-border bg-surface-muted text-ink-meta",
        )}
        onClick={open}
      >
        {indexLabel}
      </button>
      {hovered && citation && displayIndex ? (
        <CitationHoverCard
          displayIndex={displayIndex}
          citation={citation}
          canOpen={canOpen}
          onOpen={open}
          citationById={citationById}
          onOpenCitation={openCitation}
        />
      ) : null}
    </span>
  );
}

export function CitationSourceCards({
  content,
  citationBundle,
  messageId,
  onCitationClick,
}: {
  content: string;
  citationBundle?: CitationBundleV1;
  messageId?: string;
  onCitationClick?: (input: OpenCitationInput) => void;
}) {
  const { t } = useI18n();
  const used = useMemo(
    () => usedCitations(content, citationBundle),
    [content, citationBundle],
  );
  if (!used.length) return null;

  return (
    <section className="mt-3 border-t border-surface-border pt-2">
      <h3 className="text-xs font-medium text-ink-meta">
        {t("ui.citation.sources", "Sources")}
      </h3>
      <div className="mt-1.5 flex flex-col gap-1.5">
        {used.map(({ displayIndex, citation }) => {
          const disabled =
            !onCitationClick ||
            citation.resolutionStatus === "forbidden" ||
            citation.resolutionStatus === "missing";
          const quality = qualityBadge(citation);
          return (
            <button
              key={citation.citationId}
              type="button"
              disabled={disabled}
              onClick={() =>
                onCitationClick?.({
                  messageId,
                  citationId: citation.citationId,
                })
              }
              className="w-full max-w-full truncate rounded-md bg-transparent px-2 py-1 text-left text-xs text-ink-body transition enabled:hover:bg-surface-muted disabled:cursor-default disabled:opacity-60"
            >
              <span className="mr-1 font-semibold text-primary">
                {displayIndex}
              </span>
              {citation.source.title}
              {quality ? (
                <>
                  <span aria-hidden="true" className="ml-1 text-2xs text-ink-meta">
                    ·
                  </span>
                  <span
                    data-citation-quality={quality.status}
                    className="ml-1 text-2xs text-ink-meta"
                  >
                    {quality.label}
                  </span>
                </>
              ) : null}
            </button>
          );
        })}
      </div>
    </section>
  );
}
