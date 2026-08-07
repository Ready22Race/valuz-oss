import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@openuidev/react-ui/Modal", () => ({ Modal: () => null }));

import { A2UIRenderer } from "./A2UIRenderer";

/**
 * A generated page must only ever GROW on screen.
 *
 * Two things used to break that, both observed on a real research-desk
 * generation:
 *
 * 1. The runtime narrates its own gaps — a half-written component name renders
 *    as ``Unknown component: PageHea``, an undelivered child as
 *    ``[Loading card-brief...]``. Mid-stream that was most of the viewport.
 * 2. A turn can carry the same document twice (the canonical assistant text is
 *    the join of every model-end segment). The repeat arrives a character at a
 *    time and was merged over the finished page, so every component dissolved
 *    back into a skeleton and grew again.
 */

const ONE_COPY = [
  JSON.stringify({
    version: "v0.9",
    createSurface: { surfaceId: "main", catalogId: "openui" },
  }),
  JSON.stringify({
    version: "v0.9",
    updateComponents: {
      surfaceId: "main",
      components: [
        { id: "root", component: "Stack", children: ["a", "b"] },
        { id: "a", component: "TextContent", text: "第一块" },
        { id: "b", component: "TextContent", text: "第二块" },
      ],
    },
  }),
].join("\n");

/** What the turn actually hands over: the document, then the document again. */
const DOUBLED = `${ONE_COPY}\n${ONE_COPY}`;

const textAt = (body: string): string => {
  const { container } = render(<A2UIRenderer body={body} />);
  return (container.textContent ?? "").replace(/\s+/g, " ").trim();
};

const frames = (body: string, count: number): string[] =>
  Array.from({ length: count }, (_, i) =>
    textAt(body.slice(0, Math.round((body.length * (i + 1)) / count))),
  );

describe("A2UI progressive paint", () => {
  it("should never narrate a half-written component name to the user", () => {
    const all = frames(ONE_COPY, 24).join("");
    expect(all).not.toMatch(/Unknown component/);
  });

  it("should not narrate undelivered children as loading text", () => {
    const all = frames(ONE_COPY, 24).join("");
    expect(all).not.toMatch(/\[Loading/);
  });

  it("should breathe a page skeleton while nothing resolves yet", () => {
    // The surface header has landed and its first component has not. A hole
    // reads as "nothing is happening"; the runtime's own answer is the literal
    // string ``[Loading root...]``.
    const { container } = render(<A2UIRenderer body={ONE_COPY.slice(0, 40)} />);
    expect(
      container.querySelector('[data-slot="a2ui-generation-skeleton"]'),
    ).toBeTruthy();
  });

  it("should drop the skeleton as soon as a real component resolves", () => {
    const { container } = render(<A2UIRenderer body={ONE_COPY} />);
    expect(
      container.querySelector('[data-slot="a2ui-generation-skeleton"]'),
    ).toBe(null);
    expect(container.textContent).toContain("第一块");
  });

  it("should render nothing at all for an empty payload", () => {
    const { container } = render(<A2UIRenderer body="" />);
    expect(container.firstChild).toBe(null);
  });

  it("should only ever grow while a single copy streams", () => {
    const seen = frames(ONE_COPY, 24);
    seen.forEach((frame, i) => {
      if (i === 0) return;
      expect(frame.startsWith(seen[i - 1] as string)).toBe(true);
    });
  });

  it("should only ever grow when the turn repeats the whole document", () => {
    // The regression: without the repeat being dropped, frames past the
    // half-way mark went BACKWARDS as the second copy overwrote the first.
    const seen = frames(DOUBLED, 24);
    seen.forEach((frame, i) => {
      if (i === 0) return;
      expect(frame.startsWith(seen[i - 1] as string)).toBe(true);
    });
  });

  it("should render a repeated document exactly once", () => {
    const once = textAt(ONE_COPY);
    expect(textAt(DOUBLED)).toBe(once);
    expect(once).toContain("第一块");
    expect(once).toContain("第二块");
  });

  it("should keep a genuine second surface that says something different", () => {
    // Only a re-emission of what we already have is dropped; a real restart
    // carrying different content must still render.
    const other = ONE_COPY.replace("第二块", "改过了");
    expect(textAt(`${ONE_COPY}\n${other}`)).toContain("改过了");
  });
});
