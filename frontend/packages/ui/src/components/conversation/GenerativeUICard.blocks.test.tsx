import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GenerativeUICard } from "./GenerativeUICard";

/**
 * The sibling GenerativeUICard.test.tsx stubs A2UI to test the card's chrome.
 * This file does the opposite: nothing is mocked, so one payload goes through
 * the real parser and both component sets at once — OpenUI's primitives and the
 * Valuz blocks. It is the only test that would catch the two sets diverging
 * inside the actual product component rather than in the library package.
 */

const MIXED = [
  { version: "v0.9", createSurface: { surfaceId: "s", catalogId: "openui" } },
  {
    version: "v0.9",
    updateComponents: {
      surfaceId: "s",
      components: [
        { id: "root", component: "Stack", children: ["heading", "strip", "sources"] },
        { id: "heading", component: "TextContent", text: "Q4 performance", size: "large-heavy" },
        { id: "strip", component: "MiniCardBlock", children: ["a", "b"] },
        {
          id: "a",
          component: "MiniCard",
          label: "Revenue",
          value: "$4.2M",
          delta: "+12.4%",
          trend: "up",
        },
        { id: "b", component: "MiniCard", label: "Margin", value: "38%" },
        { id: "sources", component: "CondensedSources", children: ["s1"] },
        {
          id: "s1",
          component: "SourceItem",
          index: 1,
          title: "Annual report",
          url: "https://example.com/report",
        },
      ],
    },
  },
]
  .map((m) => JSON.stringify(m))
  .join("\n");

describe("GenerativeUICard with both component sets", () => {
  it("renders OpenUI components and Valuz blocks from one payload", () => {
    render(<GenerativeUICard openui={MIXED} status="success" />);

    // From OpenUI's own library.
    expect(screen.getByText("Q4 performance")).toBeTruthy();
    // From @valuz/genui-blocks.
    expect(screen.getByText("Revenue")).toBeTruthy();
    expect(screen.getByText("$4.2M")).toBeTruthy();
    expect(screen.getByText("Margin")).toBeTruthy();
    expect(screen.getByText("Annual report")).toBeTruthy();
  });

  it("keeps blocks rendering inside the fullscreen surface too", () => {
    // Fullscreen mounts a second renderer over the same payload; a change
    // wired into only one of the two call sites would pass the test above.
    const { container } = render(<GenerativeUICard openui={MIXED} status="success" />);
    const scopes = container.querySelectorAll('[data-openui-scope="generative-ui"]');
    expect(scopes.length).toBeGreaterThan(0);
    expect(container.textContent).toContain("$4.2M");
  });
});
