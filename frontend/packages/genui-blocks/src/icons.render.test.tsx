import { Renderer } from "@openuidev/react-lang";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { isKnownIcon } from "./lib/icon";
import { createValuzLibrary } from "./library";

function renderLang(source: string) {
  return render(<Renderer library={createValuzLibrary()} response={source} />);
}

describe("icons", () => {
  it("loads a lucide icon by name", async () => {
    const { container } = renderLang(`root = IconTag("trending-up")`);
    // The icon arrives through a dynamic import, so it is a frame or two late.
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
    expect(container.querySelector('[data-slot="vgb-icon-tag"]')).not.toBeNull();
  });

  it("renders nothing for a name lucide does not have", async () => {
    // The model will invent icon names. A thrown error from the lazy import
    // would take down the whole generated document, so an unknown name has to
    // degrade to no icon — the block around it still renders.
    const { container } = renderLang(
      `root = IconText("totally-made-up-icon", "Still here")`,
    );
    expect(screen.getByText("Still here")).toBeTruthy();
    await waitFor(() => expect(container.querySelector("svg")).toBeNull());
  });

  it("pairs an icon with text and an optional note", async () => {
    const { container } = renderLang(
      `root = IconText("dollar-sign", "Revenue", "Up on renewals")`,
    );
    expect(screen.getByText("Revenue")).toBeTruthy();
    expect(screen.getByText("Up on renewals")).toBeTruthy();
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
  });

  it("hides icons from assistive technology", async () => {
    // An icon here is always decorative: every block that carries one also
    // carries the text it marks, so announcing it would be a duplicate.
    const { container } = renderLang(`root = IconTag("star")`);
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
    expect(container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("knows which names exist", () => {
    expect(isKnownIcon("trending-up")).toBe(true);
    expect(isKnownIcon("TRENDING-UP")).toBe(true);
    expect(isKnownIcon("  dollar-sign  ")).toBe(true);
    expect(isKnownIcon("not-an-icon")).toBe(false);
    expect(isKnownIcon(undefined)).toBe(false);
    expect(isKnownIcon("")).toBe(false);
  });

  it("names only icons that exist in the prompt examples", () => {
    // The description lists example names to teach the shape. An example that
    // does not resolve teaches the model a name that renders nothing.
    for (const name of [
      "trending-up",
      "trending-down",
      "dollar-sign",
      "chart-line",
      "users",
      "alert-triangle",
      "circle-check",
      "info",
      "star",
      "activity",
      "wallet",
      "building-2",
    ]) {
      expect(isKnownIcon(name), `${name} is not a lucide icon`).toBe(true);
    }
  });
});
