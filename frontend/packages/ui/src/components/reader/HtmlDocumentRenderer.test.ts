import { describe, expect, it } from "vitest";

import {
  findHtmlQuoteRange,
  highlightHtmlDocument,
} from "./HtmlDocumentRenderer";

function html(value: string): Document {
  return new DOMParser().parseFromString(value, "text/html");
}

describe("HTML citation location", () => {
  it("matches a quote across text nodes", () => {
    const doc = html("<p>Revenue <strong>increased 18%</strong> year over year.</p>");
    const range = findHtmlQuoteRange(doc.body, {
      exact: "Revenue increased 18% year over year.",
    });

    expect(range?.toString()).toBe("Revenue increased 18% year over year.");
  });

  it("uses prefix and suffix to disambiguate repeated exact text", () => {
    const doc = html(
      "<p>First: unchanged. Old.</p><p>Guidance: unchanged. New.</p>",
    );
    const range = findHtmlQuoteRange(doc.body, {
      exact: "unchanged",
      prefix: "Guidance: ",
      suffix: ". New",
    });

    expect(range?.startContainer.parentElement?.textContent).toContain(
      "Guidance",
    );
  });

  it("cleans the previous mark before highlighting a new citation", () => {
    const doc = html(
      '<p data-chunk-id="c1">Alpha evidence.</p><p data-chunk-id="c2">Beta evidence.</p>',
    );
    highlightHtmlDocument(doc, {
      kind: "html",
      chunkId: "c1",
      quote: { exact: "Alpha" },
    });
    expect(doc.querySelectorAll("[data-citation-highlight]")).toHaveLength(1);
    expect(doc.querySelector("[data-citation-highlight]")?.textContent).toBe(
      "Alpha",
    );

    highlightHtmlDocument(doc, {
      kind: "html",
      chunkId: "c2",
      quote: { exact: "Beta" },
    });
    expect(doc.querySelectorAll("[data-citation-highlight]")).toHaveLength(1);
    expect(doc.querySelector("[data-citation-highlight]")?.textContent).toBe(
      "Beta",
    );
  });

  it("matches chunk ids as opaque attributes without selector interpolation", () => {
    const chunkId = 'chunk-"quoted\nvalue';
    const doc = html("<p>Opaque chunk evidence.</p>");
    doc.querySelector("p")?.setAttribute("data-chunk-id", chunkId);

    const result = highlightHtmlDocument(doc, {
      kind: "html",
      chunkId,
      quote: { exact: "Opaque chunk" },
    });

    expect(result.status).toBe("located-exact");
    expect(doc.querySelector("[data-citation-highlight]")?.textContent).toBe(
      "Opaque chunk",
    );
  });
});
