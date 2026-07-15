import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@openuidev/react-lang", () => ({
  Renderer: (props: { response: string; isStreaming?: boolean }) => (
    <div data-testid="renderer" data-streaming={props.isStreaming ? "true" : "false"}>
      {props.response}
    </div>
  ),
}));
vi.mock("@openuidev/react-ui", () => ({
  // ThemeProvider just renders its children in the test — we don't need the
  // real style-injection/context machinery here.
  ThemeProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
vi.mock("@openuidev/react-ui/genui-lib", () => ({
  openuiLibrary: {},
}));
vi.mock("../../hooks/use-i18n", () => ({
  useI18n: () => ({ t: (k: string) => k }),
}));

import { GenerativeUICard, extractContentText } from "./GenerativeUICard";

describe("GenerativeUICard", () => {
  it("renders the OpenUI Renderer with the openui payload", () => {
    render(<GenerativeUICard openui={"Chart\n  data: 1"} />);
    expect(screen.getByTestId("renderer").textContent).toBe("Chart\n  data: 1");
  });

  it("lets horizontal charts expand to show every data row", () => {
    const { container } = render(<GenerativeUICard openui={"HorizontalBarChart"} />);
    const styles = Array.from(container.querySelectorAll("style"))
      .map((style) => style.textContent)
      .join("\n");

    expect(styles).toContain(".openui-horizontal-bar-chart-container-inner-wrapper");
    expect(styles).toContain("height: auto !important");
    expect(styles).toContain("overflow-y: visible");
  });

  it("unwraps a JSON content-block envelope before rendering", () => {
    // The kernel JSON-stringifies MCP TextContent at the SSE boundary — the
    // tool output arrives as [{"type":"text","text":"<OpenUI Lang>"}], not raw.
    const openuiLang = 'root = Stack([header], "column", "l")';
    const envelope = JSON.stringify([{ type: "text", text: openuiLang }]);
    render(<GenerativeUICard openui={envelope} />);
    expect(screen.getByTestId("renderer").textContent).toBe(openuiLang);
  });

  it("shows an empty state when there is no output yet", () => {
    render(<GenerativeUICard openui={undefined} status="running" />);
    expect(screen.getByTestId("genui-empty")).toBeTruthy();
  });

  it("renders in streaming mode while running", () => {
    render(<GenerativeUICard openui={"Chart\n  data: 1"} status="running" />);
    const r = screen.getByTestId("renderer");
    expect(r.getAttribute("data-streaming")).toBe("true");
    expect(r.textContent).toBe("Chart\n  data: 1");
  });

  it("renders non-streaming on success", () => {
    render(<GenerativeUICard openui={"Chart"} status="success" />);
    expect(screen.getByTestId("renderer").getAttribute("data-streaming")).toBe("false");
  });
});

describe("extractContentText", () => {
  it("unwraps a JSON content-block envelope (preserving quotes/newlines)", () => {
    const lang = 'root = Stack([header], "column", "l")\nheader = Card([t], "sunk")';
    expect(extractContentText(JSON.stringify([{ type: "text", text: lang }]))).toBe(lang);
  });

  it("concatenates multiple text blocks", () => {
    const wrapped = JSON.stringify([{ type: "text", text: "a=" }, { type: "text", text: "1" }]);
    expect(extractContentText(wrapped)).toBe("a=1");
  });

  it("unwraps a single content object", () => {
    expect(extractContentText(JSON.stringify({ type: "text", text: "hello" }))).toBe("hello");
  });

  it("returns raw OpenUI Lang unchanged when there is no envelope", () => {
    const lang = 'root = Stack([header], "column", "l")';
    expect(extractContentText(lang)).toBe(lang);
  });

  it("unwraps a Python-repr envelope from other runtimes", () => {
    expect(extractContentText("[{'type': 'text', 'text': 'root = Stack()'}]")).toBe(
      "root = Stack()",
    );
  });

  it("returns empty for empty/blank input", () => {
    expect(extractContentText(undefined)).toBe("");
    expect(extractContentText("   ")).toBe("");
  });
});
