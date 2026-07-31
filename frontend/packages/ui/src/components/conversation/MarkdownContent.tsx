import {
  memo,
  useCallback,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
} from "react";
import type {
  CitationBundleV1,
  CitationQualityIssueV1,
  OpenCitationInput,
} from "@valuz/shared";
import {
  Streamdown,
  defaultUrlTransform,
  type UrlTransform,
} from "streamdown";
import { code } from "@streamdown/code";
import { mermaid } from "@streamdown/mermaid";
import { math } from "@streamdown/math";
import { cjk } from "@streamdown/cjk";
import {
  AlertTriangle,
  Check,
  Copy,
  Download,
  ExternalLink,
  Info,
  Loader2,
  Maximize,
  RotateCcw,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import "streamdown/styles.css";
import "katex/dist/katex.min.css";

import { cn } from "../../lib/cn";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Button } from "../ui/button";
import { useI18n } from "../../hooks/use-i18n";
import {
  citationDisplayOrder,
  citationIdFromHref,
  CitationPill,
  CitationSourceCards,
  rewriteCitationMarkdownLinks,
  type CitationQualityDisplayIssue,
} from "./CitationInline";

/** Icon overrides so Streamdown's built-in toolbar buttons (copy /
 * download / fullscreen / etc.) draw from the same lucide set we use
 * everywhere else. Without this, Streamdown's defaults (its own SVG
 * set) render at a slightly different stroke weight / proportion,
 * which is jarring next to the lucide icons we put in our own panel
 * headers. Keys must match Streamdown's ``IconMap`` interface. */
const STREAMDOWN_ICONS = {
  CheckIcon: Check,
  CopyIcon: Copy,
  DownloadIcon: Download,
  ExternalLinkIcon: ExternalLink,
  Loader2Icon: Loader2,
  Maximize2Icon: Maximize,
  RotateCcwIcon: RotateCcw,
  XIcon: X,
  ZoomInIcon: ZoomIn,
  ZoomOutIcon: ZoomOut,
};

const LOCAL_FILE_HREF_PREFIX = "https://valuz.local-file.invalid/";
const QUALITY_CLAIM_HREF_PREFIX = "https://valuz.quality-claim.invalid/";

interface LocalizedClaimQualityEntry {
  targetId: string;
  exact: string;
  issues: CitationQualityDisplayIssue[];
}

function qualityClaimIdFromHref(href?: string): string | null {
  if (!href?.startsWith(QUALITY_CLAIM_HREF_PREFIX)) return null;
  try {
    return decodeURIComponent(href.slice(QUALITY_CLAIM_HREF_PREFIX.length));
  } catch {
    return null;
  }
}

function injectQualityClaimMarkers(
  content: string,
  entries: LocalizedClaimQualityEntry[],
): string {
  let result = content;
  for (const entry of entries) {
    const start = result.indexOf(entry.exact);
    if (start < 0) continue;
    const insertAt = start + entry.exact.length;
    const marker = ` [!](<${QUALITY_CLAIM_HREF_PREFIX}${encodeURIComponent(entry.targetId)}>)`;
    result = `${result.slice(0, insertAt)}${marker}${result.slice(insertAt)}`;
  }
  return result;
}

function encodeLocalFileHref(href: string): string {
  return `${LOCAL_FILE_HREF_PREFIX}${encodeURIComponent(href)}`;
}

function decodeLocalFileHref(href: string): string {
  if (!href.startsWith(LOCAL_FILE_HREF_PREFIX)) return href;
  try {
    return decodeURIComponent(href.slice(LOCAL_FILE_HREF_PREFIX.length));
  } catch {
    return href;
  }
}

function rewriteLocalFileMarkdownLinks(
  content: string,
  isLocalFileHref?: (href: string) => boolean,
): string {
  if (!isLocalFileHref) return content;
  return content.replace(/(\[[^\]\n]+\]\()([^)\n]+)(\))/g, (match) =>
    rewriteMarkdownLinkMatch(match, isLocalFileHref),
  );
}

function rewriteMarkdownLinkMatch(
  match: string,
  isLocalFileHref: (href: string) => boolean,
): string {
  const destinationStart = match.lastIndexOf("(");
  if (destinationStart === -1 || !match.endsWith(")")) return match;

  const prefix = match.slice(0, destinationStart + 1);
  const destination = match.slice(destinationStart + 1, -1);
  return `${prefix}${rewriteMarkdownLinkDestination(
    destination,
    isLocalFileHref,
  )})`;
}

function rewriteMarkdownLinkDestination(
  destination: string,
  isLocalFileHref: (href: string) => boolean,
): string {
  const leading = destination.match(/^\s*/)?.[0] ?? "";
  const trailing = destination.match(/\s*$/)?.[0] ?? "";
  const body = destination.slice(
    leading.length,
    destination.length - trailing.length,
  );
  if (!body) return destination;

  if (body.startsWith("<")) {
    const end = body.indexOf(">");
    if (end <= 0) return destination;
    const href = body.slice(1, end);
    if (!isLocalFileHref(href)) return destination;
    return `${leading}<${encodeLocalFileHref(href)}>${body.slice(
      end + 1,
    )}${trailing}`;
  }

  const [href = "", ...rest] = body.split(/(\s[\s\S]*)/);
  if (!href || !isLocalFileHref(href)) return destination;
  return `${leading}${encodeLocalFileHref(href)}${rest.join("")}${trailing}`;
}

interface MarkdownContentProps {
  content: string;
  className?: string;
  isAnimating?: boolean;
  isLocalFileHref?: (href: string) => boolean;
  onLocalFileLinkClick?: (href: string) => void;
  citationBundle?: CitationBundleV1;
  messageId?: string;
  onCitationClick?: (input: OpenCitationInput) => void;
}

/**
 * Shared style overrides applied around every Streamdown render so
 * fenced code blocks and tables land in the same "card" look across
 * the whole app (conversation transcript, skill detail panel, anything
 * else that uses MarkdownContent).
 *
 * The selectors target Streamdown's stable ``data-streamdown="..."``
 * markers; rules sit on the wrapper div and reach into Streamdown's
 * internals so the chrome (header bar, toolbar buttons, dropdown
 * items, body padding) all match.
 */
const RICH_TEXT_OVERRIDES = [
  // ── Fenced code blocks ────────────────────────────────────────
  // Card chrome only — the action overlay positioning is too complex
  // for Tailwind arbitrary variants (needs ``:has()`` on a parent
  // div) so it lives in the global ``CODE_ACTIONS_CSS`` block below.
  "[&_[data-streamdown='code-block']]:relative",
  "[&_[data-streamdown='code-block']]:overflow-hidden",
  "[&_[data-streamdown='code-block']]:rounded-lg",
  "[&_[data-streamdown='code-block']]:border",
  "[&_[data-streamdown='code-block']]:border-surface-border",
  "[&_[data-streamdown='code-block-header']]:bg-surface-soft",
  "[&_[data-streamdown='code-block-header']]:border-b",
  "[&_[data-streamdown='code-block-header']]:border-surface-border",
  "[&_[data-streamdown='code-block-header']]:rounded-none",
  "[&_[data-streamdown='code-block-header']]:px-3",
  // Streamdown's language label span has ``ml-1`` which adds another
  // 4px on top of the header's left padding; zero it so the label sits
  // flush against the header padding.
  "[&_[data-streamdown='code-block-header']>span]:ml-0",
  "[&_[data-streamdown='code-block-body']]:bg-surface",
  "[&_[data-streamdown='code-block-body']]:border-0",
  "[&_[data-streamdown='code-block-body']]:rounded-none",
  "[&_[data-streamdown='code-block-download-button']]:hidden",
  // Pull padding off Shiki's <pre data-language>; put it on the body.
  "[&_[data-language]]:p-0",
  "[&_[data-streamdown='code-block-body']]:px-4",
  "[&_[data-streamdown='code-block-body']]:pb-4",
  "[&_[data-streamdown='code-block-body']]:pt-2",
  "[&_pre]:m-0",

  // ── Tables ────────────────────────────────────────────────────
  "[&_[data-streamdown='table-wrapper']]:relative",
  "[&_[data-streamdown='table-wrapper']]:rounded-lg",
  "[&_[data-streamdown='table-wrapper']]:border",
  "[&_[data-streamdown='table-wrapper']]:border-[color:var(--fg-3)]",
  "[&_[data-streamdown='table-wrapper']]:bg-surface",
  "[&_[data-streamdown='table-wrapper']]:overflow-hidden",
  "[&_[data-streamdown='table-wrapper']]:p-0",
  "[&_[data-streamdown='table-wrapper']]:gap-0",
  "[&_[data-streamdown='table-wrapper']]:text-[13px]",
  // Toolbar region — overlay only, so it does not create a second header row.
  "[&_[data-streamdown='table-wrapper']>div:has(button)]:absolute",
  "[&_[data-streamdown='table-wrapper']>div:has(button)]:right-2",
  "[&_[data-streamdown='table-wrapper']>div:has(button)]:top-1.5",
  "[&_[data-streamdown='table-wrapper']>div:has(button)]:z-10",
  "[&_[data-streamdown='table-wrapper']>div:has(button)]:h-5",
  "[&_[data-streamdown='table-wrapper']>div:has(button)]:bg-transparent",
  "[&_[data-streamdown='table-wrapper']>div:has(button)]:border-0",
  "[&_[data-streamdown='table-wrapper']>div:has(button)]:p-0",
  "[&_[data-streamdown='table-wrapper']>div:has(button)]:items-center",
  // Table region — flat white, no inner border, bottom rounded.
  "[&_[data-streamdown='table-wrapper']>div:has([data-streamdown='table'])]:border-0",
  "[&_[data-streamdown='table-wrapper']>div:has([data-streamdown='table'])]:rounded-none",
  "[&_[data-streamdown='table-wrapper']>div:has([data-streamdown='table'])]:rounded-b-lg",
  "[&_[data-streamdown='table-wrapper']>div:has([data-streamdown='table'])]:bg-surface",
  "[&_[data-streamdown='table']]:w-full",
  "[&_[data-streamdown='table']]:border-collapse",
  "[&_[data-streamdown='table']]:tabular-nums",
  "[&_[data-streamdown='table-header']]:bg-[color:var(--fg-1)]",
  "[&_[data-streamdown='table-header']]:border-b",
  "[&_[data-streamdown='table-header']]:border-[color:var(--fg-3)]",
  "[&_[data-streamdown='table-header-cell']]:px-[14px]",
  "[&_[data-streamdown='table-header-cell']]:py-2",
  "[&_[data-streamdown='table-header-cell']]:text-2xs",
  "[&_[data-streamdown='table-header-cell']]:font-medium",
  "[&_[data-streamdown='table-header-cell']]:text-[color:var(--fg-60)]",
  "[&_[data-streamdown='table-header-cell']]:text-right",
  "[&_[data-streamdown='table-header-cell']:first-child]:text-left",
  "[&_[data-streamdown='table-header-cell']:last-child]:text-transparent",
  "[&_[data-streamdown='table-header-cell']:last-child]:select-none",
  "[&_[data-streamdown='table-row']]:border-b",
  "[&_[data-streamdown='table-row']]:border-[color:var(--fg-3)]",
  "[&_[data-streamdown='table-row']:last-child]:border-b-0",
  "[&_[data-streamdown='table-cell']]:px-[14px]",
  "[&_[data-streamdown='table-cell']]:py-[9px]",
  "[&_[data-streamdown='table-cell']]:text-[13px]",
  "[&_[data-streamdown='table-cell']]:text-ink-heading",
  "[&_[data-streamdown='table-cell']]:text-right",
  "[&_[data-streamdown='table-cell']:first-child]:text-left",
  // Dropdown items (Markdown / CSV / TSV) inside copy/download dropdowns.
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>div>button:hover]:bg-surface-muted",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>div>button]:cursor-default",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>div>button]:text-ink-heading",
  // Toolbar icons — direct-button case (fullscreen).
  "[&_[data-streamdown='table-wrapper']>div:has(button)>button]:flex",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>button]:h-5",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>button]:w-5",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>button]:items-center",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>button]:justify-center",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>button]:p-0",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>button]:cursor-default",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>button]:text-ink-muted",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>button>svg]:h-3",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>button>svg]:w-3",
  // Wrapped-button case (copy / download dropdowns).
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>button]:flex",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>button]:h-5",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>button]:w-5",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>button]:items-center",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>button]:justify-center",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>button]:p-0",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>button]:cursor-default",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>button]:text-ink-muted",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>button>svg]:h-3",
  "[&_[data-streamdown='table-wrapper']>div:has(button)>div>button>svg]:w-3",

  // ── Lists ──────────────────────────────────────────────────────
  // Streamdown's default list margins / line-height produce a
  // double-spaced look that reads more like slide bullets than body
  // copy. Tighten:
  //   - block margin between list and surrounding paragraphs
  //   - per-item margin (the gap between adjacent bullets)
  //   - item line-height (was inheriting container ``leading-[1.7]``)
  // Rhythm strategy: tight lines (1.55) + generous block spacing
  // (16px between paragraphs / list blocks). Inside-paragraph density
  // gives every block a clear silhouette; the wide gutters between
  // blocks supply the "breathing" the reference screenshot has.
  "[&_ul]:my-4",
  "[&_ol]:my-4",
  // Switch to ``list-outside`` so markers occupy the padding region
  // instead of riding inline with the text. With ``list-inside`` the
  // marker (``•`` / ``1.``) butts straight against the first
  // character with no controllable gap; with ``list-outside`` the
  // ``pl-*`` becomes "marker column + gutter", giving each row a
  // proper hanging-indent feel like Notion / GitLab.
  "[&_ul]:list-outside",
  "[&_ol]:list-outside",
  // ``pl-7`` (28px): with ``list-outside`` the marker rides in the
  // padding region, so this value sets both the indent depth AND
  // the marker → text gap. ~28px overall indent, ~20px between
  // marker and the first character — airy hanging indent like
  // Notion / GitLab.
  "[&_ul]:pl-7",
  "[&_ol]:pl-7",
  "[&_li_ul]:pl-7",
  "[&_li_ol]:pl-7",
  "[&_li]:my-0",
  // Streamdown ships ``py-1`` baked into its MarkdownLi component
  // (4px above + below every bullet). Override the padding too —
  // ``my-*`` alone leaves the 4px gap intact.
  "[&_[data-streamdown='list-item']]:py-0",
  "[&_li]:leading-[1.7]",
  // Nested lists ride tight too — a sub-list under a parent item
  // shouldn't open a gap that competes with the 16px gutter
  // between top-level blocks.
  "[&_li>ul]:my-0",
  "[&_li>ol]:my-0",

  // ── Paragraphs ────────────────────────────────────────────────
  "[&_p]:my-4",
  "[&_p]:leading-[1.7]",

  // ── Inline code ───────────────────────────────────────────────
  // Long unbroken strings inside inline ``<code>`` (file paths, URLs,
  // hashes …) overflowed the message column because the default
  // ``white-space`` for ``<code>`` lets them push past the right edge
  // when there's no whitespace to break on. ``break-all`` lets the
  // browser break the run anywhere — fine for paths/URLs which are
  // already opaque blobs. The ``:not(pre)`` guard keeps fenced code
  // blocks (which have their own scrollable layout) untouched.
  "[&_:not(pre)>code]:break-all",
];

/**
 * Global style block (rendered once with each MarkdownContent mount;
 * duplicates collapse via identical CSS) for two cases the Tailwind
 * arbitrary variants above can't cleanly cover:
 *
 *   1. Streamdown code blocks render their actions toolbar as a
 *      ``sticky top-2 -mt-10`` overlay floating ABOVE the body so it
 *      visually overlaps the (default) header. With our own header
 *      bg the floating capsule lands awkwardly outside the title
 *      strip and gets clipped by ``overflow-hidden`` on the card. We
 *      pin the overlay ``absolute top:0 right:0`` inside the
 *      ``relative`` code-block container so the buttons land in the
 *      header row regardless of header styling. Also drop the
 *      capsule's pill chrome (border / bg / blur) so the buttons
 *      read as plain icon controls in the header bar.
 *
 *   2. Streamdown's table fullscreen view is portaled to
 *      ``document.body`` so the wrapper-scoped Tailwind selectors
 *      can't reach it; mirrors the inline card look here.
 */
const GLOBAL_RICH_TEXT_CSS = `
  /* ── Code-block actions overlay ──────────────────────────────── */
  /* Streamdown stamps the floating div with a long compound class
     list (.pointer-events-none.sticky.top-2.z-10.-mt-10...) that
     out-specifies our attribute selector. !important is the cheapest
     way to win without inflating selector weight artificially. */
  [data-streamdown="code-block"] > div:has(> [data-streamdown="code-block-actions"]) {
    position: absolute !important;
    top: 0 !important;
    right: 0 !important;
    left: auto !important;
    bottom: auto !important;
    margin: 0 !important;
    height: 32px !important;
    padding: 0 8px !important;
    display: flex !important;
    align-items: center !important;
    pointer-events: auto !important;
    z-index: 1 !important;
  }
  [data-streamdown="code-block-actions"] {
    background: transparent !important;
    border: 0 !important;
    padding: 0 !important;
    backdrop-filter: none !important;
    box-shadow: none !important;
    gap: 4px;
  }
  [data-streamdown="code-block-copy-button"] {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    padding: 0;
    cursor: default;
    color: var(--color-ink-muted, #b6b7bc);
  }
  [data-streamdown="code-block-copy-button"] svg {
    width: 12px;
    height: 12px;
  }

  /* ── Table fullscreen ────────────────────────────────────────── */`;

const FULLSCREEN_TABLE_CSS = `
  [data-streamdown="table-fullscreen"] > div {
    background: var(--surface);
    border-radius: 8px;
    border: 1px solid var(--fg-3);
    overflow: hidden;
    /* Top offset = project TopBar height (36px). AppShell's inner
       flex uses p-4 pt-0, so the main card sits flush under the
       topbar; matching that here puts the fullscreen card on the
       same baseline. Other sides match AppShell's 16px outer gutter. */
    margin: 36px 16px 16px 16px;
    height: calc(100% - 52px);
  }
  [data-streamdown="table-fullscreen"] > div > div:first-child {
    background: var(--fg-1);
    border-bottom: 1px solid var(--fg-3);
    padding: 0 14px;
    height: 34px;
    align-items: center;
    gap: 4px;
  }
  [data-streamdown="table-fullscreen"] > div > div:first-child button {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    padding: 0;
    color: var(--color-ink-muted, #b6b7bc);
    cursor: default;
  }
  [data-streamdown="table-fullscreen"] > div > div:first-child button > svg {
    width: 12px;
    height: 12px;
  }
  [data-streamdown="table-fullscreen"] > div > div:nth-child(2) {
    padding: 0;
    background: var(--surface);
  }
  [data-streamdown="table-fullscreen"] table[data-streamdown="table"] {
    border: none;
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    font-feature-settings: "tnum";
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-header"] {
    background: var(--fg-1);
    border-bottom: 1px solid var(--fg-3);
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-header-cell"] {
    padding: 8px 14px;
    text-align: right;
    font-weight: 500;
    font-size: 11px;
    color: var(--fg-60);
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-header-cell"]:first-child {
    text-align: left;
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-row"] {
    border-bottom: 1px solid var(--fg-3);
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-row"]:last-child {
    border-bottom: none;
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-cell"] {
    padding: 9px 14px;
    font-size: 13px;
    color: var(--color-ink-heading, #131313);
    text-align: right;
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-cell"]:first-child {
    text-align: left;
  }
`;

/**
 * Hook + dialog for confirming external (http/https) link navigation.
 *
 * Streamdown ships its own link-safety modal but renders it inline
 * (``position: fixed`` inside the markdown subtree). The conversation
 * uses a virtualized list whose rows carry ``transform: translateY``,
 * which establishes a containing block for ``fixed`` and pins the
 * modal to the row instead of the viewport. We sidestep that by
 * overriding Streamdown's ``a`` component and presenting our own
 * radix Dialog — radix portals to ``document.body``, so transformed
 * ancestors don't affect positioning.
 */
const isExternalHref = (href: string | undefined): href is string =>
  typeof href === "string" && /^https?:\/\//i.test(href);

const openExternalUrl = async (url: string): Promise<void> => {
  const desktopApi = (
    window as Window & {
      valuzDesktop?: {
        invoke: <T>(
          channel: string,
          payload?: Record<string, unknown>,
        ) => Promise<T>;
      };
    }
  ).valuzDesktop;
  if (desktopApi) {
    try {
      const opened = await desktopApi.invoke<boolean>("open_external_url", {
        url,
      });
      if (opened) return;
    } catch {
      /* fall through to browser open */
    }
  }
  window.open(url, "_blank", "noopener,noreferrer");
};

const ExternalLinkConfirmDialog = ({
  url,
  onClose,
}: {
  url: string | null;
  onClose: () => void;
}) => {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard denied — silent */
    }
  }, [url]);

  const handleConfirm = useCallback(() => {
    if (!url) return;
    void openExternalUrl(url);
    onClose();
  }, [url, onClose]);

  return (
    <Dialog
      open={url !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ExternalLink className="h-4 w-4" />
            <span>{t("conversation.openExternalLink")}</span>
          </DialogTitle>
          <DialogDescription>
            {t("conversation.openExternalLinkDesc")}
          </DialogDescription>
        </DialogHeader>
        <div
          className={cn(
            "break-all rounded-md bg-muted p-3 font-mono text-xs",
            url && url.length > 100 && "max-h-32 overflow-y-auto",
          )}
        >
          {url ?? ""}
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => void handleCopy()}
          >
            {copied ? (
              <>
                <Check className="h-4 w-4" />
                <span>{t("common.copied")}</span>
              </>
            ) : (
              <>
                <Copy className="h-4 w-4" />
                <span>{t("conversation.copyLink")}</span>
              </>
            )}
          </Button>
          <Button type="button" onClick={handleConfirm}>
            <ExternalLink className="h-4 w-4" />
            <span>{t("conversation.openLink")}</span>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export const MarkdownContent = memo(function MarkdownContent({
  content,
  className,
  isAnimating,
  isLocalFileHref,
  onLocalFileLinkClick,
  citationBundle,
  messageId,
  onCitationClick,
}: MarkdownContentProps) {
  const { t } = useI18n();
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);
  const citationOrder = useMemo(() => citationDisplayOrder(content), [content]);
  const citationsById = useMemo(
    () =>
      new Map(
        citationBundle?.citations.map((citation) => [
          citation.citationId,
          citation,
        ]) ?? [],
      ),
    [citationBundle],
  );
  const qualityIssueLabel = useCallback(
    (issue: CitationQualityIssueV1): string => {
      const code = issue.code;
      if (
        code.includes("conflict") ||
        code === "low_tier_without_cross_check"
      ) {
        return t("ui.citation.qualityIssueCrossCheck");
      }
      if (
        code.startsWith("calculation_") ||
        code.startsWith("numeric_") ||
        code === "derived_claim_without_calculation_evidence"
      ) {
        return t("ui.citation.qualityIssueNumeric");
      }
      if (
        code.includes("coverage") ||
        code === "evidence_before_coverage" ||
        code === "evidence_after_coverage"
      ) {
        return t("ui.citation.qualityIssueFreshness");
      }
      if (code === "source_tier_unmatched") {
        return t("ui.citation.qualityIssueSource");
      }
      if (
        code.startsWith("evidence_") ||
        code === "text_quote_missing" ||
        code === "structured_value_missing"
      ) {
        return t("ui.citation.qualityIssueEvidence");
      }
      const keyByLayer: Record<string, string> = {
        L0: "ui.citation.qualityIssueIntegrity",
        L1: "ui.citation.qualityIssueEvidence",
        L2: "ui.citation.qualityIssueSource",
        L3: "ui.citation.qualityIssueCrossCheck",
        L4: "ui.citation.qualityIssueNumeric",
        L5: "ui.citation.qualityIssueFreshness",
      };
      return t(keyByLayer[issue.layer] ?? "ui.citation.qualityIssueGeneric");
    },
    [t],
  );
  const qualityIssuePlacement = useMemo(() => {
    const byCitationId = new Map<string, CitationQualityDisplayIssue[]>();
    const claimsByExact = new Map<string, LocalizedClaimQualityEntry>();
    const unlocalized: CitationQualityIssueV1[] = [];
    const issues = citationBundle?.quality?.issues ?? [];
    for (const [issueIndex, issue] of issues.entries()) {
      const localIds = Array.from(new Set(issue.citationIds ?? [])).filter(
        (citationId) =>
          citationsById.has(citationId) && citationOrder.has(citationId),
      );
      const displayIssue = {
        label: qualityIssueLabel(issue),
        severity: issue.severity,
      };
      if (!localIds.length) {
        const exact = issue.claim?.exact?.trim();
        if (
          exact &&
          !exact.includes("\n") &&
          !exact.includes("|") &&
          content.includes(exact)
        ) {
          const entry = claimsByExact.get(exact) ?? {
            targetId: `quality-claim-${issueIndex + 1}`,
            exact,
            issues: [],
          };
          if (
            !entry.issues.some(
              (item) =>
                item.label === displayIssue.label &&
                item.severity === displayIssue.severity,
            )
          ) {
            entry.issues.push(displayIssue);
          }
          claimsByExact.set(exact, entry);
          continue;
        }
        unlocalized.push(issue);
        continue;
      }
      for (const citationId of localIds) {
        const current = byCitationId.get(citationId) ?? [];
        if (
          !current.some(
            (item) =>
              item.label === displayIssue.label &&
              item.severity === displayIssue.severity,
          )
        ) {
          current.push(displayIssue);
        }
        byCitationId.set(citationId, current);
      }
    }
    return {
      byCitationId,
      claimEntries: Array.from(claimsByExact.values()),
      unlocalized,
    };
  }, [
    citationBundle?.quality?.issues,
    citationOrder,
    citationsById,
    content,
    qualityIssueLabel,
  ]);
  const hasLocalizedQualityIssues =
    qualityIssuePlacement.byCitationId.size > 0 ||
    qualityIssuePlacement.claimEntries.length > 0;
  const unlocalizedQualityStatus = useMemo(() => {
    const quality = citationBundle?.quality;
    if (hasLocalizedQualityIssues) return null;
    if (!quality || quality.status === "passed") return null;
    if (!quality.issues?.length) return quality.status;
    if (!qualityIssuePlacement.unlocalized.length) return null;
    return qualityIssuePlacement.unlocalized.every(
      (issue) => issue.severity === "unverified",
    )
      ? "unverified"
      : "degraded";
  }, [
    citationBundle?.quality,
    hasLocalizedQualityIssues,
    qualityIssuePlacement.unlocalized,
  ]);
  const claimQualityById = useMemo(
    () =>
      new Map(
        qualityIssuePlacement.claimEntries.map((entry) => [
          entry.targetId,
          entry,
        ]),
      ),
    [qualityIssuePlacement.claimEntries],
  );
  const renderedContent = useMemo(
    () =>
      rewriteLocalFileMarkdownLinks(
        rewriteCitationMarkdownLinks(
          injectQualityClaimMarkers(
            content,
            qualityIssuePlacement.claimEntries,
          ),
        ),
        isLocalFileHref,
      ),
    [content, isLocalFileHref, qualityIssuePlacement.claimEntries],
  );
  const urlTransform = useCallback<UrlTransform>(
    (url, key, node) => {
      if (key === "href" && isLocalFileHref?.(decodeLocalFileHref(url))) {
        return url;
      }
      return defaultUrlTransform(url, key, node);
    },
    [isLocalFileHref],
  );

  const components = useMemo(
    () => ({
      a: ({
        href,
        children,
        onClick,
        className: anchorClassName,
        ...rest
      }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
        const baseClass = cn(
          "wrap-anywhere font-medium text-primary underline",
          anchorClassName,
        );
        const localHref = href ? decodeLocalFileHref(href) : href;
        const qualityClaimId = qualityClaimIdFromHref(href);
        if (qualityClaimId) {
          const entry = claimQualityById.get(qualityClaimId);
          if (!entry) return null;
          const label = entry.issues.map((issue) => issue.label).join("；");
          return (
            <button
              type="button"
              data-citation-claim-quality
              data-quality-claim-id={entry.targetId}
              aria-label={`${t("ui.citation.qualityNeedsReview")} · ${label}`}
              title={label}
              className="relative -top-px mx-0.5 inline-flex h-4 w-4 align-middle items-center justify-center rounded-full border border-warning/50 bg-warning-light/70 text-warning-text no-underline transition hover:bg-warning-light focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warning/20"
            >
              <AlertTriangle className="h-2.5 w-2.5" aria-hidden="true" />
            </button>
          );
        }
        const citationId = citationIdFromHref(href);
        if (citationId) {
          return (
            <CitationPill
              citationId={citationId}
              displayIndex={citationOrder.get(citationId)}
              citation={citationsById.get(citationId)}
              citationById={citationsById}
              qualityIssues={qualityIssuePlacement.byCitationId.get(citationId)}
              messageId={messageId}
              onCitationClick={onCitationClick}
            />
          );
        }
        if (localHref && isLocalFileHref?.(localHref) && onLocalFileLinkClick) {
          return (
            <a
              {...rest}
              href={localHref}
              className={baseClass}
              onClick={(event) => {
                event.preventDefault();
                onClick?.(event);
                onLocalFileLinkClick(localHref);
              }}
            >
              {children}
            </a>
          );
        }
        if (isExternalHref(href)) {
          return (
            <a
              {...rest}
              href={href}
              className={baseClass}
              onClick={(event) => {
                event.preventDefault();
                onClick?.(event);
                setPendingUrl(href);
              }}
            >
              {children}
            </a>
          );
        }
        return (
          <a {...rest} href={href} className={baseClass} onClick={onClick}>
            {children}
          </a>
        );
      },
    }),
    [
      citationOrder,
      citationsById,
      claimQualityById,
      isLocalFileHref,
      messageId,
      onCitationClick,
      onLocalFileLinkClick,
      qualityIssuePlacement.byCitationId,
      t,
    ],
  );

  return (
    <>
      <style>{GLOBAL_RICH_TEXT_CSS + FULLSCREEN_TABLE_CSS}</style>
      <div
        id="streamdown"
        className={cn(
          // ``break-words`` (inherited by every descendant) breaks long
          // unbreakable runs — API-error JSON blobs, URLs, hashes in plain
          // paragraphs — so they wrap instead of pushing past the message
          // column's right edge. Inline ``<code>`` keeps its own ``break-all``.
          "text-base leading-[1.7] text-ink-heading break-words",
          ...RICH_TEXT_OVERRIDES,
          className,
        )}
      >
        <Streamdown
          plugins={{ code, mermaid, math, cjk }}
          icons={STREAMDOWN_ICONS}
          isAnimating={isAnimating}
          components={components}
          urlTransform={urlTransform}
        >
          {renderedContent}
        </Streamdown>
      </div>
      {!hasLocalizedQualityIssues &&
      citationBundle?.integrity?.status === "degraded" ? (
        <div
          role="status"
          data-citation-integrity="degraded"
          className="mt-2 flex items-center gap-1.5 text-xs leading-5 text-ink-meta"
        >
          <Info
            className="relative top-px h-3.5 w-3.5 shrink-0 text-ink-muted"
            aria-hidden="true"
          />
          <span>{t("ui.citation.integrityDegraded")}</span>
        </div>
      ) : null}
      {!hasLocalizedQualityIssues &&
      citationBundle?.integrity?.status !== "degraded" &&
      unlocalizedQualityStatus ? (
        <div
          role="status"
          data-citation-quality-warning={unlocalizedQualityStatus}
          className="mt-2 flex items-center gap-1.5 text-xs leading-5 text-ink-meta"
        >
          <Info
            className="relative top-px h-3.5 w-3.5 shrink-0 text-ink-muted"
            aria-hidden="true"
          />
          <span>
            {t(
              unlocalizedQualityStatus === "unverified"
                ? "ui.citation.qualityUnverified"
                : "ui.citation.qualityDegraded",
            )}
          </span>
        </div>
      ) : null}
      <CitationSourceCards
        content={content}
        citationBundle={citationBundle}
        messageId={messageId}
        onCitationClick={onCitationClick}
      />
      <ExternalLinkConfirmDialog
        url={pendingUrl}
        onClose={() => setPendingUrl(null)}
      />
    </>
  );
});
