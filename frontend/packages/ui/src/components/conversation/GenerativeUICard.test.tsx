import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

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

  it("adds a fullscreen action to the title row and opens a fullscreen preview", () => {
    render(<GenerativeUICard openui={"Chart\n  data: 1"} />);

    const action = screen.getByRole("button", { name: "genui.fullscreen" });
    expect(action.closest('[data-slot="generative-ui-card"]')).toBeTruthy();

    fireEvent.click(action);

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("dialog").className).toContain("top-9");
    expect(screen.getByRole("dialog").className).toContain("bottom-4");
    expect(screen.getByTestId("genui-fullscreen")).toBeTruthy();
    expect(screen.getAllByTestId("renderer")).toHaveLength(2);
    expect(screen.getAllByTestId("renderer")[1]?.textContent).toBe(
      "Chart\n  data: 1",
    );
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

  it("sizes peer cards from content and collapses them in narrow containers", () => {
    const { container } = render(<GenerativeUICard openui={"Card"} />);
    const styles = Array.from(container.querySelectorAll("style"))
      .map((style) => style.textContent)
      .join("\n");

    expect(styles).toContain("container-type: inline-size");
    expect(styles).toContain("flex-basis: max-content !important");
    expect(styles).toContain("@container (max-width: 34rem)");
    expect(styles).toContain("flex-basis: 100% !important");
    expect(styles).toContain(":has(> .openui-card:nth-child(3)) > .openui-card");
    expect(styles).toContain(".openui-card-sunk");
    expect(styles).toContain(
      "> :not([class]):has(> .openui-tag):has(> :nth-child(2) .openui-markdown-renderer)",
    );
    expect(styles).toContain("flex: 1 1 15rem");
    expect(styles).toContain(
      "padding: var(--openui-space-l)",
    );
    expect(styles).toContain("background: var(--color-surface-soft)");
    expect(styles).toContain("background: transparent");
    expect(styles).toContain(".openui-table-container");
    expect(styles).toContain("border-radius: 0");
    expect(styles).toContain(".openui-table-row:nth-child(even)");
    expect(styles).toContain(".openui-scrollable-table-wrapper");
    expect(styles).toContain("width: max-content");
    expect(styles).toContain("white-space: nowrap");
    expect(styles).not.toContain(".openui-tag-success::before");
    expect(styles).not.toContain(".openui-tag-danger::before");
    expect(styles).toContain("color: var(--error-text)");
    expect(styles).toContain("color: var(--success-text)");
    expect(styles).toContain(".openui-area-chart-container");
    expect(styles).toContain(".openui-horizontal-bar-chart-container");
    expect(styles).toContain(".openui-scatter-chart-container");
    expect(styles).toContain(".openui-radar-chart-container-wrapper");
    expect(styles).toContain(".openui-pie-chart-container-wrapper");
    expect(styles).toContain(".openui-radial-chart-container-wrapper");
    expect(styles).toContain(".openui-single-stacked-bar-chart-container");
    expect(styles).toContain('[class$="-chart-condensed-container-inner"]');
    expect(styles).toContain(".recharts-responsive-container");
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

  it("streams the reasoning while running, replacing the bare generating placeholder", () => {
    render(
      <GenerativeUICard openui={undefined} status="running" thinking={"planning the layout"} />,
    );
    const el = screen.getByTestId("genui-thinking");
    expect(el.textContent).toBe("planning the layout");
    // The reasoning section IS the progress surface — no second spinner row.
    expect(screen.queryByTestId("genui-empty")).toBeNull();
  });

  it("keeps the reasoning visible alongside the progressive render", () => {
    render(<GenerativeUICard openui={"Chart"} status="running" thinking={"still going"} />);
    expect(screen.getByTestId("genui-thinking").textContent).toBe("still going");
    expect(screen.getByTestId("renderer").textContent).toBe("Chart");
  });

  it("drops the reasoning once the tool completes", () => {
    render(<GenerativeUICard openui={"Chart"} status="success" thinking={"planning"} />);
    expect(screen.queryByTestId("genui-thinking")).toBeNull();
    expect(screen.getByTestId("renderer").textContent).toBe("Chart");
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
