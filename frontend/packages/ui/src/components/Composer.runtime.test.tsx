/** @vitest-environment jsdom */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Composer, type RuntimeSelectorItem } from "./Composer";
import type { SkillSearchItem } from "./conversation/SkillSearchMenu";

const sampleRuntimes: RuntimeSelectorItem[] = [
  { id: "claude_agent", displayName: "Claude Agent", available: true },
  { id: "codex", displayName: "Codex Agent", available: true },
  {
    id: "deepagents",
    displayName: "Valuz Agent",
    available: false,
    unavailableReason: "binary missing",
  },
];

describe("Composer runtime selector (REP-107)", () => {
  it("does not render the runtime trigger when runtimes prop is empty", () => {
    render(<Composer runtimes={[]} />);
    expect(
      screen.queryByText(/Claude Agent|Codex Agent|Valuz Agent/),
    ).toBeNull();
  });

  it("falls back to the first available runtime label when none is selected", () => {
    render(<Composer runtimes={sampleRuntimes} selectedRuntimeId={null} />);
    // Trigger button shows the first available runtime — Valuz Agent
    // is unavailable, so claude_agent wins. Use queryAllByText because
    // the same label may also appear inside the dropdown if it's open.
    expect(screen.getAllByText("Claude Agent").length).toBeGreaterThan(0);
  });

  it("does NOT show a 默认 Runtime placeholder option", () => {
    render(<Composer runtimes={sampleRuntimes} />);
    expect(screen.queryByText(/默认\s*Runtime/)).toBeNull();
  });

  it("opens the dropdown and lists every runtime", () => {
    render(
      <Composer runtimes={sampleRuntimes} selectedRuntimeId="claude_agent" />,
    );
    // Click the trigger (the displayed label is the runtime name now).
    const triggers = screen.getAllByText("Claude Agent");
    fireEvent.click(triggers[0]);
    // After opening, all three runtime names appear in the dropdown.
    expect(screen.getAllByText("Claude Agent").length).toBeGreaterThan(0);
    expect(screen.getByText("Codex Agent")).toBeTruthy();
    expect(screen.getByText("Valuz Agent")).toBeTruthy();
  });

  it("calls onRuntimeChange + clears model on selection", () => {
    const onRuntimeChange = vi.fn();
    const onModelChange = vi.fn();
    render(
      <Composer
        runtimes={sampleRuntimes}
        selectedRuntimeId="claude_agent"
        selectedProviderId="ch-x"
        selectedModelId="some-model"
        providers={[
          {
            providerId: "ch-x",
            providerName: "Anthropic",
            modelId: "some-model",
            isDefault: false,
          },
        ]}
        onRuntimeChange={onRuntimeChange}
        onModelChange={onModelChange}
      />,
    );

    fireEvent.click(screen.getAllByText("Claude Agent")[0]);
    fireEvent.click(screen.getByText("Codex Agent"));

    expect(onRuntimeChange).toHaveBeenCalledWith("codex");
    expect(onModelChange).toHaveBeenCalledWith(null, null);
  });

  it("does not invoke onRuntimeChange when an unavailable runtime is clicked", () => {
    const onRuntimeChange = vi.fn();
    render(
      <Composer
        runtimes={sampleRuntimes}
        selectedRuntimeId="claude_agent"
        onRuntimeChange={onRuntimeChange}
      />,
    );

    fireEvent.click(screen.getAllByText("Claude Agent")[0]);
    // Valuz Agent is the unavailable one in sampleRuntimes.
    fireEvent.click(screen.getByText("Valuz Agent"));

    expect(onRuntimeChange).not.toHaveBeenCalled();
  });

  it("does not open the dropdown when modelLocked is true", () => {
    render(
      <Composer
        runtimes={sampleRuntimes}
        selectedRuntimeId="claude_agent"
        modelLocked
      />,
    );
    fireEvent.click(screen.getAllByText("Claude Agent")[0]);
    // The other runtimes would only appear if the dropdown opened.
    expect(screen.queryByText("Codex Agent")).toBeNull();
  });

  it("shows the runtime's display name when one is selected", () => {
    render(<Composer runtimes={sampleRuntimes} selectedRuntimeId="codex" />);
    expect(screen.getByText("Codex Agent")).toBeTruthy();
  });
});

describe("Composer IME submission guard", () => {
  it("does not send when Enter confirms an active IME composition", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} />);

    fireEvent.keyDown(screen.getByRole("textbox"), {
      key: "Enter",
      isComposing: true,
    });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send for IME composition keyCode 229", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} />);

    fireEvent.keyDown(screen.getByRole("textbox"), {
      key: "Enter",
      keyCode: 229,
    });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("still sends on a normal Enter press", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} />);

    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });

    expect(onSend).toHaveBeenCalledTimes(1);
  });
});

describe("Composer slash-command pass-through", () => {
  const SKILLS: SkillSearchItem[] = [
    { id: "1", name: "deep-research", description: "research deeply" },
  ];

  /** Drive the ``/`` trigger the way real typing does: the picker only opens
   *  when the *last* keystroke is the bare ``/``, then subsequent input feeds
   *  the query. So set ``/`` first, then the full token. */
  const typeSlashToken = (editor: HTMLElement, token: string) => {
    editor.textContent = "/";
    fireEvent.input(editor);
    editor.textContent = token;
    fireEvent.input(editor);
  };

  it("sends a /command that matches no skill on Enter (no longer swallowed)", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} skills={SKILLS} />);
    const editor = screen.getByRole("textbox");

    typeSlashToken(editor, "/compact");
    // No skill matches "compact" → picker closed → Enter sends.
    fireEvent.keyDown(editor, { key: "Enter" });

    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("does NOT send while the skill picker is open with matches", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} skills={SKILLS} />);
    const editor = screen.getByRole("textbox");

    typeSlashToken(editor, "/deep");
    // "deep" matches deep-research → picker open → Enter is captured by it.
    fireEvent.keyDown(editor, { key: "Enter" });

    expect(onSend).not.toHaveBeenCalled();
  });
});
