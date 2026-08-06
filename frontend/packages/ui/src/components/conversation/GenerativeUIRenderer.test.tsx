import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@openuidev/react-ui", () => ({
  ThemeProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
vi.mock("./A2UIRenderer", () => ({
  A2UIRenderer: ({ body }: { body: string }) => (
    <div data-testid="a2ui-renderer">{body}</div>
  ),
}));

import { GenerativeUIRenderer } from "./GenerativeUIRenderer";
import { parseGenerativeUIPayload } from "./generative-ui-payload";

describe("GenerativeUIRenderer", () => {
  it("draws nothing for a payload that is not an A2UI stream", () => {
    // A2UI is the only protocol. Anything else — an older OpenUI Lang result,
    // a plain-text error — has no renderer, and printing its source where a
    // rendered UI belongs reads as a bug in the answer.
    const { container } = render(
      <GenerativeUIRenderer payload={"root = Stack([])"} status="success" />,
    );

    expect(screen.queryByTestId("a2ui-renderer")).toBeNull();
    expect(container.textContent).toBe("");
  });

  it("refuses a payload whose envelope names an unknown protocol", () => {
    expect(
      parseGenerativeUIPayload(
        JSON.stringify({ protocol: "openui-lang", content: "root = Stack([])" }),
      ),
    ).toBeNull();
  });

  it("parses an A2UI protocol envelope", () => {
    const messages = [
      JSON.stringify({
        version: "v0.9",
        createSurface: { surfaceId: "s1", catalogId: "openui" },
      }),
    ].join("\n");

    expect(
      parseGenerativeUIPayload(
        JSON.stringify({ protocol: "a2ui-json", content: messages }),
      ),
    ).toEqual({ protocol: "a2ui-json", body: messages });
  });

  it("renders A2UI payloads through the A2UI renderer", () => {
    const messages = [
      JSON.stringify({
        version: "v0.9",
        createSurface: { surfaceId: "dashboard", catalogId: "openui" },
      }),
      JSON.stringify({
        version: "v0.9",
        updateComponents: {
          surfaceId: "dashboard",
          components: [
            { id: "root", component: "TextContent", text: "Revenue" },
          ],
        },
      }),
    ].join("\n");

    render(
      <GenerativeUIRenderer
        payload={JSON.stringify({ protocol: "a2ui-json", content: messages })}
      />,
    );

    expect(screen.getByTestId("a2ui-renderer").textContent).toBe(messages);
  });
});
