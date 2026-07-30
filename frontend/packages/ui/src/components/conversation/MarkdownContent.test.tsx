/** @vitest-environment jsdom */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MarkdownContent } from "./MarkdownContent";
import type { CitationBundleV1 } from "@valuz/shared";

const CITATIONS: CitationBundleV1 = {
  version: 1,
  citations: [
    {
      citationId: "cit_first",
      source: {
        sourceId: "doc:1",
        providerId: "docs",
        documentId: "1",
        sourceType: "document",
        title: "Annual report",
        organization: "Example Corp",
        publishedAt: "2026-03-20T00:00:00Z",
        retrievedAt: "2026-07-30T08:00:00Z",
      },
      evidence: {
        kind: "text",
        quote: "Revenue increased 18%.",
        snippet: "For the year, revenue increased 18%.",
        capturedAt: "2026-07-30T08:00:00Z",
      },
    },
    {
      citationId: "cit_second",
      source: {
        sourceId: "doc:2",
        providerId: "docs",
        sourceType: "document",
        title: "Earnings release",
        retrievedAt: "2026-07-30T08:00:00Z",
      },
      evidence: {
        kind: "text",
        quote: "Margin expanded.",
        snippet: "Gross margin expanded.",
        capturedAt: "2026-07-30T08:00:00Z",
      },
    },
  ],
};

describe("MarkdownContent local file links", () => {
  it("routes local file hrefs through the provided handler", () => {
    const onLocalFileLinkClick = vi.fn();

    render(
      <MarkdownContent
        content="[Open report](/Users/ada/project/report.md:12)"
        onLocalFileLinkClick={onLocalFileLinkClick}
        isLocalFileHref={(href) => href.startsWith("/Users/")}
      />,
    );

    fireEvent.click(screen.getByRole("link", { name: "Open report" }));

    expect(onLocalFileLinkClick).toHaveBeenCalledWith(
      "/Users/ada/project/report.md:12",
    );
  });

  it("renders file protocol local links without Streamdown blocking", () => {
    const onLocalFileLinkClick = vi.fn();

    render(
      <MarkdownContent
        content="[Open HTML](file:///Users/ada/Downloads/ai-crm/index.html)"
        onLocalFileLinkClick={onLocalFileLinkClick}
        isLocalFileHref={(href) => href.startsWith("file:///Users/")}
      />,
    );

    const link = screen.getByRole("link", { name: "Open HTML" });
    expect(link.getAttribute("href")).toBe(
      "file:///Users/ada/Downloads/ai-crm/index.html",
    );
    expect(screen.queryByText("[blocked]")).toBeNull();
  });

  it("leaves non-local hrefs on the normal markdown link path", () => {
    const onLocalFileLinkClick = vi.fn();

    render(
      <MarkdownContent
        content="[Settings](/settings)"
        onLocalFileLinkClick={onLocalFileLinkClick}
        isLocalFileHref={(href) => href.startsWith("/Users/")}
      />,
    );

    fireEvent.click(screen.getByRole("link", { name: "Settings" }));

    expect(onLocalFileLinkClick).not.toHaveBeenCalled();
  });
});

describe("MarkdownContent citations", () => {
  it("numbers citations by first appearance and reuses duplicate numbers", () => {
    render(
      <MarkdownContent
        content={
          "First [source](citation://cit_second), then [source](citation://cit_first), again [source](citation://cit_second)."
        }
        citationBundle={CITATIONS}
      />,
    );

    expect(
      screen.getAllByRole("button", { name: /(?:citation|引用) 1/i }),
    ).toHaveLength(2);
    expect(
      screen.getAllByRole("button", { name: /(?:citation|引用) 2/i }),
    ).toHaveLength(1);
  });

  it("shows the evidence snapshot on hover without fetching", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={CITATIONS}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );

    expect(screen.getByText("Annual report")).not.toBeNull();
    expect(screen.getByText("Revenue increased 18%.")).not.toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("keeps the hover action usable while keyboard focus moves into the card", () => {
    const onCitationClick = vi.fn();
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={CITATIONS}
        messageId="msg-1"
        onCitationClick={onCitationClick}
      />,
    );

    const pill = screen.getByRole("button", { name: /(?:citation|引用) 1/i });
    fireEvent.focus(pill);
    const openSource = screen.getByRole("button", {
      name: /(?:open source|打开原文)/i,
    });
    fireEvent.blur(pill, { relatedTarget: openSource });
    fireEvent.focus(openSource);
    fireEvent.click(openSource);

    expect(onCitationClick).toHaveBeenCalledWith({
      messageId: "msg-1",
      citationId: "cit_first",
    });
  });

  it("renders an additive policy quality badge without changing citation identity", () => {
    const bundle: CitationBundleV1 = {
      ...CITATIONS,
      citations: [
        {
          ...CITATIONS.citations[0],
          annotations: {
            quality: {
              policyId: "domain-policy",
              policyRevision: "v1",
              tier: "P1",
              status: "passed",
              label: "P1",
            },
          },
        },
      ],
    };
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={bundle}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );
    const badges = screen.getAllByText("P1");
    expect(badges).toHaveLength(2);
    expect(
      badges.every(
        (badge) => badge.getAttribute("data-citation-quality") === "passed",
      ),
    ).toBe(true);
  });

  it("opens the authoritative inputs from a calculation citation card", () => {
    const onCitationClick = vi.fn();
    const input = CITATIONS.citations[0];
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        input,
        {
          citationId: "cit_calculation",
          source: {
            sourceId: "calculation:1",
            providerId: "runtime",
            sourceType: "tool-result",
            title: "Growth calculation",
            retrievedAt: "2026-07-30T08:00:00Z",
          },
          evidence: {
            kind: "calculation",
            expression: "revenue / 100",
            inputs: [
              {
                name: "revenue",
                citationId: input.citationId,
                value: 118,
                unit: "USD million",
              },
            ],
            result: 1.18,
            unit: "x",
            calculatedAt: "2026-07-30T08:00:00Z",
          },
        },
      ],
    };
    render(
      <MarkdownContent
        content={"Growth [calc](citation://cit_calculation)."}
        citationBundle={bundle}
        messageId="msg-1"
        onCitationClick={onCitationClick}
      />,
    );

    fireEvent.focus(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /revenue.*annual report/i }));

    expect(onCitationClick).toHaveBeenCalledWith({
      messageId: "msg-1",
      citationId: "cit_first",
    });
  });

  it("opens a known citation and degrades an unknown citation", () => {
    const onCitationClick = vi.fn();
    render(
      <MarkdownContent
        content={
          "Known [source](citation://cit_first), unknown [source](citation://cit_missing)."
        }
        citationBundle={CITATIONS}
        messageId="msg-1"
        onCitationClick={onCitationClick}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );
    expect(onCitationClick).toHaveBeenCalledWith({
      messageId: "msg-1",
      citationId: "cit_first",
    });
    const unavailable = screen.getByRole("button", {
      name: /(?:citation unavailable|引用不可用)/i,
    });
    expect(unavailable.getAttribute("aria-disabled")).toBe("true");
    expect(unavailable.textContent).toBe("[2]");
  });

  it("keeps body-derived numbering when the citation bundle is unavailable", () => {
    render(
      <MarkdownContent
        content="Source [report](citation://cit_from_newer_bundle)."
      />,
    );

    const unavailable = screen.getByRole("button", {
      name: /(?:citation unavailable|引用不可用)/i,
    });
    expect(unavailable.textContent).toBe("[1]");
    expect(unavailable.getAttribute("aria-disabled")).toBe("true");
  });

  it("leaves an unbound plain [1] as normal text", () => {
    render(<MarkdownContent content="Plain [1] text." citationBundle={CITATIONS} />);

    expect(screen.getByText(/Plain \[1\] text/)).not.toBeNull();
    expect(
      screen.queryByRole("button", { name: /(?:citation|引用)/i }),
    ).toBeNull();
  });

  it("surfaces a generic warning when runtime citation integrity is degraded", () => {
    render(
      <MarkdownContent
        content="The answer could not bind a source."
        citationBundle={{
          version: 1,
          citations: [],
          integrity: {
            status: "degraded",
            unknownCitationIds: ["ev_missing"],
            unusedCitationIds: [],
            missingLocatorCitationIds: [],
            repairAttempts: 1,
            policyRevision: "citation-v1",
          },
        }}
      />,
    );

    expect(
      document.querySelector('[data-citation-integrity="degraded"]'),
    ).not.toBeNull();
    expect(
      screen.getByText(
        /some citations could not be verified|部分引用未能通过验证/i,
      ),
    ).not.toBeNull();
  });
});
