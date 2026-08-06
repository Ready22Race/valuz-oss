import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { A2UIRenderer } from "./A2UIRenderer";

/**
 * Containers whose sub-items arrive as separate components, referenced by id.
 *
 * This is the form the catalog teaches — `{"id":"root","component":"Tabs",
 * "children":["t1"]}` with `t1` declared alongside — and it is the form every
 * one of these containers silently dropped. A Tabs rendered nothing at all; a
 * Table headed its column with the literal string "c1". Nothing errored, and no
 * test noticed, because every existing test passed its sub-items inline.
 *
 * Each case asserts the content *inside* the sub-item, not the sub-item's own
 * label: a container that renders its tab strip but loses the panel behind it
 * still fails the reader.
 */

const a2ui = (components: Record<string, unknown>[]): string =>
  [
    { version: "v0.9", createSurface: { surfaceId: "s", catalogId: "openui" } },
    { version: "v0.9", updateComponents: { surfaceId: "s", components } },
  ]
    .map((message) => JSON.stringify(message))
    .join("\n");

describe("A2UI containers resolve sub-items referenced by id", () => {
  it("Tabs → TabItem", () => {
    render(
      <A2UIRenderer
        body={a2ui([
          { id: "root", component: "Tabs", children: ["t1"] },
          { id: "t1", component: "TabItem", value: "a", trigger: "Overview", children: ["x"] },
          { id: "x", component: "TextContent", text: "Revenue grew" },
        ])}
      />,
    );
    expect(screen.getByText("Overview")).toBeTruthy();
    expect(screen.getByText("Revenue grew")).toBeTruthy();
  });

  it("Accordion → AccordionItem", () => {
    render(
      <A2UIRenderer
        body={a2ui([
          { id: "root", component: "Accordion", children: ["a1"] },
          { id: "a1", component: "AccordionItem", value: "a", label: "Method", children: ["x"] },
          { id: "x", component: "TextContent", text: "Discounted cash flow" },
        ])}
      />,
    );
    expect(screen.getByText("Method")).toBeTruthy();
    expect(screen.getByText("Discounted cash flow")).toBeTruthy();
  });

  it("Steps → StepsItem", () => {
    render(
      <A2UIRenderer
        body={a2ui([
          { id: "root", component: "Steps", children: ["s1"] },
          { id: "s1", component: "StepsItem", title: "Collect filings" },
        ])}
      />,
    );
    expect(screen.getByText("Collect filings")).toBeTruthy();
  });

  it("Select → SelectItem", () => {
    const { container } = render(
      <A2UIRenderer
        body={a2ui([
          { id: "root", component: "Select", children: ["o1"] },
          { id: "o1", component: "SelectItem", value: "q4", label: "Q4 FY26" },
        ])}
      />,
    );
    // The options live in a portal that only opens on interaction, so what is
    // assertable here is the negative: the id must not have become the option's
    // own label, which is what it was before.
    expect(container.textContent).not.toContain("o1");
    expect(screen.getByRole("combobox")).toBeTruthy();
  });

  it("Table → Col", () => {
    render(
      <A2UIRenderer
        body={a2ui([
          { id: "root", component: "Table", children: ["c1"] },
          { id: "c1", component: "Col", label: "Ticker", data: ["AAPL"] },
        ])}
      />,
    );
    expect(screen.getByText("Ticker")).toBeTruthy();
    expect(screen.getByText("AAPL")).toBeTruthy();
    // The id must never surface as content — that was the original symptom.
    expect(screen.queryByText("c1")).toBeNull();
  });

  it("Carousel → referenced slides", () => {
    render(
      <A2UIRenderer
        body={a2ui([
          { id: "root", component: "Carousel", children: ["p1"] },
          { id: "p1", component: "TextContent", text: "Slide one" },
        ])}
      />,
    );
    expect(screen.getByText("Slide one")).toBeTruthy();
  });

  it("keeps working when sub-items are inline instead", () => {
    // The form that always worked. Both have to, since the model writes either.
    render(
      <A2UIRenderer
        body={a2ui([
          {
            id: "root",
            component: "Tabs",
            items: [{ value: "a", trigger: "Inline", content: ["x"] }],
          },
          { id: "x", component: "TextContent", text: "Still here" },
        ])}
      />,
    );
    expect(screen.getByText("Inline")).toBeTruthy();
    expect(screen.getByText("Still here")).toBeTruthy();
  });

  it("renders a DatePicker rather than its own name as text", () => {
    const { container } = render(
      <A2UIRenderer
        body={a2ui([{ id: "root", component: "DatePicker", value: "2026-03-31" }])}
      />,
    );
    expect(container.textContent).not.toBe("DatePicker");
    expect(container.querySelector("button, [role='grid'], [data-slot]")).toBeTruthy();
  });
});
