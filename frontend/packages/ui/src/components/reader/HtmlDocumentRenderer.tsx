import { useCallback, useEffect, useRef, useState } from "react";
import type { TextQuoteSelectorV1 } from "@valuz/shared";

import type { DocumentLocation } from "./document-reader.types";
import { selectBestNormalizedMatch } from "./text-quote";

const STYLE = `
  <style>
    :root { color-scheme: light; }
    html { scroll-behavior: smooth; scrollbar-width: thin; scrollbar-color: rgba(137, 143, 156, 0.12) transparent; }
    ::-webkit-scrollbar { width: 4px; height: 4px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(137, 143, 156, 0.12); border-radius: 9999px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(137, 143, 156, 0.28); }
    body { box-sizing: border-box; margin: 0 auto; max-width: 860px; padding: 24px; color: rgb(36 39 45); font: 14px/1.75 system-ui, sans-serif; }
    *, *::before, *::after { box-sizing: inherit; }
    img, video, canvas, svg, table { max-width: 100%; }
    mark[data-citation-highlight] { background: rgb(253 230 138); color: inherit; border-radius: 2px; outline: 1px solid rgb(245 158 11 / 40%); }
    [data-citation-block-highlight] { background: rgb(254 243 199); outline: 1px solid rgb(245 158 11 / 33%); border-radius: 4px; }
    @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
  </style>
`;

function srcDoc(html: string): string {
  const csp =
    "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data: blob: https: http:; media-src blob: https: http:; style-src 'unsafe-inline'\">";
  if (/<head[\s>]/i.test(html)) {
    return html.replace(/<head([^>]*)>/i, `<head$1>${csp}${STYLE}`);
  }
  if (/<html[\s>]/i.test(html)) {
    return html.replace(
      /<html([^>]*)>/i,
      `<html$1><head>${csp}${STYLE}</head>`,
    );
  }
  return `<!doctype html><html><head>${csp}${STYLE}</head><body>${html}</body></html>`;
}

interface TextPosition {
  node: Text;
  offset: number;
}

function collectNormalizedText(root: Node): {
  text: string;
  positions: TextPosition[];
} {
  const doc = root.ownerDocument ?? (root as Document);
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (
        !node.textContent ||
        parent?.closest("script,style,noscript,[data-citation-highlight]")
      ) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let text = "";
  const positions: TextPosition[] = [];
  let pendingWhitespace: TextPosition | null = null;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const value = node.textContent ?? "";
    for (let offset = 0; offset < value.length; offset += 1) {
      const char = value[offset] ?? "";
      if (/\s/u.test(char)) {
        if (text && !text.endsWith(" ") && !pendingWhitespace) {
          pendingWhitespace = { node: node as Text, offset };
        }
        continue;
      }
      if (pendingWhitespace) {
        text += " ";
        positions.push(pendingWhitespace);
        pendingWhitespace = null;
      }
      text += char;
      positions.push({ node: node as Text, offset });
    }
  }
  return { text, positions };
}

export function findHtmlQuoteRange(
  root: Node,
  selector: TextQuoteSelectorV1,
): Range | null {
  const { text, positions } = collectNormalizedText(root);
  const match = selectBestNormalizedMatch(text, selector);
  if (!match) return null;
  const start = positions[match.start];
  const end = positions[match.end - 1];
  if (!start || !end) return null;
  const range = (root.ownerDocument ?? (root as Document)).createRange();
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset + 1);
  return range;
}

function clearHighlights(doc: Document): void {
  for (const mark of Array.from(
    doc.querySelectorAll<HTMLElement>("[data-citation-highlight]"),
  )) {
    const parent = mark.parentNode;
    mark.replaceWith(...Array.from(mark.childNodes));
    parent?.normalize();
  }
  for (const block of Array.from(
    doc.querySelectorAll<HTMLElement>("[data-citation-block-highlight]"),
  )) {
    block.removeAttribute("data-citation-block-highlight");
  }
}

function markRange(range: Range): HTMLElement[] {
  const doc = range.startContainer.ownerDocument;
  if (!doc) return [];
  const root =
    range.commonAncestorContainer.nodeType === Node.TEXT_NODE
      ? range.commonAncestorContainer.parentElement
      : (range.commonAncestorContainer as Element);
  if (!root) return [];
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const slices: Array<{ node: Text; start: number; end: number }> = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!range.intersectsNode(node)) continue;
    const text = node as Text;
    const start = text === range.startContainer ? range.startOffset : 0;
    const end =
      text === range.endContainer ? range.endOffset : text.data.length;
    if (start < end) slices.push({ node: text, start, end });
  }
  const marks: HTMLElement[] = [];
  for (const slice of slices.reverse()) {
    const part = doc.createRange();
    part.setStart(slice.node, slice.start);
    part.setEnd(slice.node, slice.end);
    const mark = doc.createElement("mark");
    mark.setAttribute("data-citation-highlight", "exact");
    part.surroundContents(mark);
    marks.unshift(mark);
  }
  return marks;
}

export function highlightHtmlDocument(
  doc: Document,
  location?: DocumentLocation,
): {
  status: "idle" | "located-exact" | "located-fallback" | "not-found";
  target: HTMLElement | null;
} {
  clearHighlights(doc);
  if (!location) return { status: "idle", target: null };
  let anchor: HTMLElement | null = null;
  if (location.chunkId) {
    // Do not interpolate an upstream chunk id into selector syntax.  Besides
    // making quotes/newlines awkward to escape correctly, a malformed trusted
    // locator could otherwise make querySelector throw and take down the
    // whole reader. Attribute equality is both exact and syntax-independent.
    anchor =
      Array.from(
        doc.querySelectorAll<HTMLElement>("[data-chunk-id]"),
      ).find(
        (element) =>
          element.getAttribute("data-chunk-id") === location.chunkId,
      ) ?? null;
  }
  if (!anchor && location.elementId) {
    anchor = doc.getElementById(location.elementId);
  }
  if (!anchor && location.cssSelector) {
    try {
      anchor = doc.querySelector(location.cssSelector);
    } catch {
      anchor = null;
    }
  }

  if (location.quote) {
    const exactRange = findHtmlQuoteRange(anchor ?? doc.body, location.quote);
    if (exactRange) {
      const marks = markRange(exactRange);
      return {
        status: anchor ? "located-exact" : "located-fallback",
        target: marks[0] ?? anchor,
      };
    }
    if (anchor) {
      anchor.setAttribute("data-citation-block-highlight", "true");
      return { status: "located-exact", target: anchor };
    }
    return { status: "not-found", target: null };
  }
  if (anchor) {
    anchor.setAttribute("data-citation-block-highlight", "true");
    return { status: "located-exact", target: anchor };
  }
  return { status: "not-found", target: null };
}

export function HtmlDocumentRenderer({
  html,
  title,
  location,
}: {
  html: string;
  title: string;
  location?: DocumentLocation;
}) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [status, setStatus] = useState("idle");

  const locate = useCallback(() => {
    const doc = iframeRef.current?.contentDocument;
    if (!doc) return;
    const result = highlightHtmlDocument(doc, location);
    setStatus(result.status);
    if (result.target) {
      const reduced = window.matchMedia?.(
        "(prefers-reduced-motion: reduce)",
      ).matches;
      result.target.scrollIntoView({
        block: "center",
        behavior: reduced ? "auto" : "smooth",
      });
    }
  }, [location]);

  useEffect(() => {
    locate();
  }, [html, locate]);

  useEffect(
    () => () => {
      const doc = iframeRef.current?.contentDocument;
      if (doc) clearHighlights(doc);
    },
    [],
  );

  return (
    <iframe
      ref={iframeRef}
      srcDoc={srcDoc(html)}
      title={title}
      sandbox="allow-same-origin"
      data-locate-status={status}
      onLoad={locate}
      className="h-full w-full border-0 bg-white"
    />
  );
}
