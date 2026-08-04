import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@openuidev/react-lang", () => ({
  Renderer: (props: { response: string; isStreaming?: boolean }) => (
    <div
      data-testid="renderer"
      data-streaming={props.isStreaming ? "true" : "false"}
    >
      {props.response}
    </div>
  ),
}));
vi.mock("@openuidev/react-ui", () => ({
  ThemeProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
vi.mock("@openuidev/react-ui/genui-lib", () => ({
  openuiLibrary: {},
}));

import { GenerativeUIRenderer } from "./GenerativeUIRenderer";
import { parseGenerativeUIPayload } from "./generative-ui-payload";

describe("GenerativeUIRenderer", () => {
  it("renders OpenUI Lang through the OpenUI renderer", () => {
    render(
      <GenerativeUIRenderer payload={"Chart\n  data: 1"} status="running" />,
    );

    const renderer = screen.getByTestId("renderer");
    expect(renderer.textContent).toBe("Chart\n  data: 1");
    expect(renderer.getAttribute("data-streaming")).toBe("true");
  });

  it("parses an A2UI protocol envelope", () => {
    const messages = [
      JSON.stringify({
        version: "v0.9",
        createSurface: { surfaceId: "s1", catalogId: "valuz" },
      }),
    ].join("\n");

    expect(
      parseGenerativeUIPayload(
        JSON.stringify({ protocol: "a2ui-json", content: messages }),
      ),
    ).toEqual({ protocol: "a2ui-json", body: messages });
  });

  it("renders A2UI v0.9 message streams with the local catalog", () => {
    const messages = [
      {
        version: "v0.9",
        createSurface: { surfaceId: "dashboard", catalogId: "valuz" },
      },
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "dashboard",
          components: [
            {
              id: "root",
              component: "Stack",
              props: { direction: "column" },
              children: [
                {
                  id: "title",
                  component: "Heading",
                  props: { text: "Revenue dashboard" },
                },
                {
                  id: "revenue",
                  component: "Metric",
                  props: { label: "Revenue", value: "$12.4M" },
                },
                {
                  id: "rows",
                  component: "Table",
                  props: {
                    columns: ["Name", "Value"],
                    rows: [["North", "$7.1M"]],
                  },
                },
              ],
            },
          ],
        },
      },
    ]
      .map((message) => JSON.stringify(message))
      .join("\n");

    render(
      <GenerativeUIRenderer
        payload={JSON.stringify({ protocol: "a2ui-json", content: messages })}
      />,
    );

    expect(screen.queryByTestId("renderer")).toBeNull();
    expect(screen.getByTestId("a2ui-surface")).toBeTruthy();
    expect(screen.getByText("Revenue dashboard")).toBeTruthy();
    expect(screen.getByText("Revenue")).toBeTruthy();
    expect(screen.getByText("$12.4M")).toBeTruthy();
    expect(screen.getByText("North")).toBeTruthy();
  });
});
