import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@openuidev/react-ui/Modal", () => ({ Modal: () => null }));

import { A2UIRenderer } from "./A2UIRenderer";

/**
 * A2UI arrives as newline-delimited JSON, and one `updateComponents` message
 * routinely carries the whole tree — 28 components on a single line in the
 * dashboards this was built against. Waiting for that line's closing brace
 * means the document appears in one jump when generation finishes, which is
 * what "no streaming" looked like. These tests feed the payload the way the
 * network does: a prefix at a time.
 */

const BODY = [
  JSON.stringify({
    version: "v0.9",
    createSurface: { surfaceId: "s", catalogId: "openui" },
  }),
  JSON.stringify({
    version: "v0.9",
    updateComponents: {
      surfaceId: "s",
      components: [
        { id: "root", component: "Stack", children: ["a", "b", "c"] },
        { id: "a", component: "TextContent", text: "第一段" },
        { id: "b", component: "TextContent", text: "第二段" },
        { id: "c", component: "TextContent", text: "第三段" },
      ],
    },
  }),
].join("\n");

function textAt(prefixLength: number): string {
  const { container } = render(<A2UIRenderer body={BODY.slice(0, prefixLength)} />);
  return container.textContent ?? "";
}

describe("A2UI streaming", () => {
  it("shows earlier content before the message is closed", () => {
    // The point of the salvage: with only the first two components written,
    // the first paragraph is already on screen.
    const upToSecond = BODY.indexOf("第二段");
    expect(textAt(upToSecond)).toContain("第一段");
    expect(textAt(upToSecond)).not.toContain("第三段");
  });

  it("grows as more of the line arrives", () => {
    const seen = [
      BODY.indexOf("第一段"),
      BODY.indexOf("第二段"),
      BODY.indexOf("第三段"),
      BODY.length,
    ].map((at) => textAt(at));
    // Monotonic: each prefix shows at least what the one before it did.
    expect(seen[1]).toContain("第一段");
    expect(seen[2]).toContain("第二段");
    expect(seen[3]).toContain("第三段");
  });

  it("ends at exactly the finished document", () => {
    const complete = textAt(BODY.length);
    expect(complete).toContain("第一段");
    expect(complete).toContain("第二段");
    expect(complete).toContain("第三段");
  });

  it("is not confused by a brace inside a string", () => {
    // Scanning braces without tracking string state would end an object early
    // and salvage a broken component.
    const withBrace = [
      JSON.stringify({ version: "v0.9", createSurface: { surfaceId: "s", catalogId: "openui" } }),
      '{"version":"v0.9","updateComponents":{"surfaceId":"s","components":[' +
        '{"id":"root","component":"TextContent","text":"a { b } c"},' +
        '{"id":"x","component":"TextContent","text":"未完',
    ].join("\n");
    const { container } = render(<A2UIRenderer body={withBrace} />);
    expect(container.textContent).toContain("a { b } c");
  });
});
