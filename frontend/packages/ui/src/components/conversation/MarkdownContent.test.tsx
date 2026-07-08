/** @vitest-environment jsdom */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MarkdownContent } from "./MarkdownContent";

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
