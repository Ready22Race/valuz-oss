import { blockNames } from "@valuz/genui-blocks";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { A2UIRenderer } from "./A2UIRenderer";

/**
 * The blocks reach A2UI through one generic adapter rather than a switch arm
 * each, so what needs guarding is the adapter's contract: registered names
 * resolve, children cross the protocol boundary, and retired names still
 * render. A test per block would only re-test the adapter.
 */

function a2ui(components: Record<string, unknown>[]): string {
  return [
    { version: "v0.9", createSurface: { surfaceId: "s", catalogId: "openui" } },
    { version: "v0.9", updateComponents: { surfaceId: "s", components } },
  ]
    .map((m) => JSON.stringify(m))
    .join("\n");
}

describe("A2UI ↔ genui-blocks bridge", () => {
  it("renders a block's scalar props", () => {
    render(
      <A2UIRenderer
        body={a2ui([
          {
            id: "root",
            component: "MiniCard",
            label: "Revenue",
            value: "$4.2M",
            delta: "+12.4%",
            trend: "up",
          },
        ])}
      />,
    );
    expect(screen.getByText("Revenue")).toBeTruthy();
    expect(screen.getByText("$4.2M")).toBeTruthy();
    expect(screen.getByText("+12.4%")).toBeTruthy();
  });

  it("carries children across the protocol boundary", () => {
    // The one place the protocols genuinely differ: A2UI passes child ids,
    // blocks expect to call renderNode(props.children).
    render(
      <A2UIRenderer
        body={a2ui([
          { id: "root", component: "MiniCardBlock", children: ["a", "b"] },
          { id: "a", component: "MiniCard", label: "Margin", value: "38%" },
          { id: "b", component: "MiniCard", label: "Headcount", value: "184" },
        ])}
      />,
    );
    expect(screen.getByText("Margin")).toBeTruthy();
    expect(screen.getByText("38%")).toBeTruthy();
    expect(screen.getByText("Headcount")).toBeTruthy();
  });

  it("renders a block from a family with no A2UI counterpart", () => {
    render(
      <A2UIRenderer
        body={a2ui([
          {
            id: "root",
            component: "SourceItem",
            index: 1,
            title: "Annual report",
            url: "https://example.com/report",
          },
        ])}
      />,
    );
    expect(screen.getByText("Annual report")).toBeTruthy();
  });

  it("keeps a retired component name rendering", () => {
    // FinanceMetric's implementation is gone; payloads still naming it must
    // resolve onto StatsCard rather than degrading to bare text.
    render(
      <A2UIRenderer
        body={a2ui([
          {
            id: "root",
            component: "FinanceMetric",
            label: "PE",
            value: "18.4",
            unit: "x",
            changePct: "+2.1%",
          },
        ])}
      />,
    );
    expect(screen.getByText("PE")).toBeTruthy();
    expect(screen.getByText("18.4 x")).toBeTruthy();
    expect(screen.getByText("+2.1%")).toBeTruthy();
  });

  it("registers every block name with the runtime", () => {
    expect(blockNames).toContain("MiniCard");
    expect(blockNames).toContain("ReportPage");
    expect(blockNames).toContain("Citation");
  });
});
