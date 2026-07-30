import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { documentResearchApi, sessionsApi } from "@valuz/core";
import type { DocumentSummaryArtifactV1 } from "@valuz/shared";
import { initI18n } from "@valuz/shared/i18n";
import type { DocumentSource } from "@valuz/ui";

import { DocumentResearchPanel } from "./DocumentResearchPanel";

vi.mock("./SessionStreamView", () => ({
  SessionStreamView: ({
    sessionId,
    onCitationClick,
  }: {
    sessionId: string;
    onCitationClick?: (input: {
      messageId?: string;
      citationId: string;
    }) => void;
  }) => (
    <button
      type="button"
      data-testid="research-stream"
      onClick={() =>
        onCitationClick?.({ messageId: "qa-message", citationId: "cit_1" })
      }
    >
      {sessionId}
    </button>
  ),
}));

const DOCUMENT: DocumentSource = {
  id: "doc-1",
  title: "Annual Report",
  render: {
    kind: "chunks",
    chunks: [{ id: "chunk-1", type: "paragraph", text: "Revenue grew." }],
  },
};

const SUMMARY: DocumentSummaryArtifactV1 = {
  version: 1,
  summary_id: "summary-1",
  document_id: "doc-1",
  document_version: "sha256:abc",
  status: "ready",
  profile: "brief",
  content: "- Revenue grew [report](citation://cit_1).",
  citation_bundle: {
    version: 1,
    citations: [
      {
        citationId: "cit_1",
        source: {
          sourceId: "doc-1",
          providerId: "docs",
          documentId: "doc-1",
          sourceType: "document",
          title: "Annual Report",
          retrievedAt: "2026-07-30T10:00:00Z",
        },
        evidence: {
          kind: "text",
          quote: "Revenue grew.",
          snippet: "Revenue grew.",
          capturedAt: "2026-07-30T10:00:00Z",
        },
        locator: { kind: "chunk", chunkId: "chunk-1" },
      },
    ],
  },
  generated_at: "2026-07-30T10:00:00Z",
  model_id: "model-1",
  prompt_revision: "document-summary-v1",
  policy_revision: "citation-v1",
  research_session_id: "research-1",
  message_id: "summary-message",
  error_message: null,
};

beforeAll(() => initI18n({ locale: "en-US", fallbackLocale: "en-US" }));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DocumentResearchPanel", () => {
  it("generates a missing summary and opens its canonical citation", async () => {
    vi.spyOn(documentResearchApi, "getSummary").mockResolvedValue(null);
    vi.spyOn(documentResearchApi, "generateSummary").mockResolvedValue(SUMMARY);
    vi.spyOn(documentResearchApi, "shareToOrigin").mockResolvedValue({
      target_session_id: "origin-session",
      message_id: "shared-message",
      source_session_id: "research-1",
      source_message_id: "summary-message",
    });
    const onCitationClick = vi.fn();

    render(
      <DocumentResearchPanel
        document={DOCUMENT}
        originSessionId="origin-session"
        originMessageId="origin-message"
        onCitationClick={onCitationClick}
      />,
    );

    await waitFor(() => {
      expect(document.body.textContent).toContain("Revenue grew");
    });
    expect(documentResearchApi.generateSummary).toHaveBeenCalledWith(
      "doc-1",
      expect.objectContaining({
        profile: "brief",
        originSessionId: "origin-session",
        originMessageId: "origin-message",
      }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    fireEvent.click(screen.getByRole("button", { name: /Citation 1/i }));
    expect(onCitationClick).toHaveBeenCalledWith("research-1", {
      messageId: "summary-message",
      citationId: "cit_1",
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Send to main chat" }),
    );
    await waitFor(() => {
      expect(documentResearchApi.shareToOrigin).toHaveBeenCalledWith(
        "research-1",
        "summary-message",
      );
    });
    expect(
      await screen.findByText("Sent to the main chat with citations."),
    ).toBeTruthy();
  });

  it("restores a locked Q&A session, sends a question, and routes citations", async () => {
    vi.spyOn(documentResearchApi, "getSummary").mockResolvedValue(SUMMARY);
    vi.spyOn(documentResearchApi, "getOrCreateSession").mockResolvedValue({
      session_id: "research-1",
      purpose: "document-research",
      document_ids: ["doc-1"],
      document_versions: ["sha256:abc"],
      source_scope: "locked",
      origin_session_id: "origin-session",
      origin_message_id: "origin-message",
      reused: true,
    });
    vi.spyOn(sessionsApi, "sendMessage").mockResolvedValue({} as never);
    const onCitationClick = vi.fn();

    render(
      <DocumentResearchPanel
        document={DOCUMENT}
        originSessionId="origin-session"
        originMessageId="origin-message"
        onCitationClick={onCitationClick}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Ask" }));
    await screen.findByTestId("research-stream");
    fireEvent.change(screen.getByLabelText("Ask this document…"), {
      target: { value: "What changed?" },
    });
    fireEvent.submit(screen.getByLabelText("Ask this document…").closest("form")!);

    await waitFor(() => {
      expect(sessionsApi.sendMessage).toHaveBeenCalledWith(
        "research-1",
        "What changed?",
      );
    });

    fireEvent.click(screen.getByTestId("research-stream"));
    expect(onCitationClick).toHaveBeenCalledWith("research-1", {
      messageId: "qa-message",
      citationId: "cit_1",
    });
  });
});
