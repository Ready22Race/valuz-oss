/** @vitest-environment jsdom */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../conversation/MarkdownContent", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <div data-testid="markdown-content">{content}</div>
  ),
}));

import { DocumentDetailPanel } from "./DocumentDetailPanel";

describe("DocumentDetailPanel", () => {
  it("renders the complete preview through the markdown renderer", () => {
    const preview = `# README\n\n\`\`\`text\n${"tree entry\n".repeat(250)}\`\`\``;

    render(
      <DocumentDetailPanel
        doc={{
          name: "README.md",
          format: "MARKDOWN",
          status: "ready",
          preview,
        }}
      />,
    );

    expect(screen.getByTestId("markdown-content").textContent).toBe(preview);
  });

  it("keeps document actions in a dedicated footer", () => {
    render(
      <DocumentDetailPanel
        doc={{ name: "README.md", format: "MARKDOWN", status: "ready" }}
        onRegenerate={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /rebuild|重建/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /delete|删除/i })).toBeTruthy();
  });

  it("uses one 24-hour time format for document and parser timestamps", () => {
    render(
      <DocumentDetailPanel
        doc={{ name: "README.md", format: "MARKDOWN", status: "ready" }}
        meta={{ importedAt: Date.parse("2026-07-22T10:13:00Z") }}
        parse={{
          parserMode: "light_local",
          attempts: [
            {
              pluginId: "light_local",
              error: "",
              occurredAt: "2026-07-22T10:13:17Z",
              ok: true,
            },
          ],
        }}
      />,
    );

    expect(screen.queryByText(/\b(?:AM|PM)\b/i)).toBeNull();
  });
});
