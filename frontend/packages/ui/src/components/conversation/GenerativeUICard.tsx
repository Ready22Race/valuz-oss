import type { ComponentProps } from "react";
import { Renderer } from "@openuidev/react-lang";
import { ThemeProvider } from "@openuidev/react-ui";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

import { useI18n } from "../../hooks/use-i18n";
import { Spinner } from "../ui/spinner";

/**
 * OpenUI theme override — brand/primary colour + the neutral text scale, both
 * pointed at Valuz tokens. Derived off the ``ThemeProvider`` prop type since
 * ``ColorTheme`` isn't exported. Everything else (surfaces, status colours,
 * borders) keeps OpenUI's defaults. ``--color-brand`` / ``--color-ink-*`` flip
 * under ``.dark``, so the override tracks both modes.
 */
type OpenUiTheme = NonNullable<ComponentProps<typeof ThemeProvider>["lightTheme"]>;

const VALUZ_OPENUUI_THEME: OpenUiTheme = {
  interactiveAccentDefault: "var(--color-brand)",
  interactiveAccentHover: "var(--color-brand-hover)",
  interactiveAccentPressed: "var(--color-brand-700)",
  interactiveAccentDisabled: "color-mix(in oklab, var(--color-brand) 40%, transparent)",
  textBrand: "var(--color-brand)",
  textAccentPrimary: "var(--color-brand)",
  textNeutralLink: "var(--color-brand)",
  borderInteractiveSelected: "var(--color-brand)",
  // Neutral text → Valuz ink scale (OpenUI's near-black defaults are illegible
  // on the dark surface). Flip under ``.dark``.
  // foreground: "var(--color-ink-body)",
  textNeutralPrimary: "var(--color-ink-heading)",
  textNeutralSecondary: "var(--color-ink-meta)",
  textNeutralTertiary: "var(--color-ink-muted)",
};

/**
 * Extract the raw text payload from a kernel tool-output string.
 *
 * MCP tool results surface on the frontend wrapped in a JSON content-block
 * envelope — ``[{"type":"text","text":"<payload>"}]`` — because the host
 * toolkit MCP server returns ``TextContent`` and the kernel JSON-stringifies
 * the content blocks at the SSE boundary (``event_sse_adapter._stringify``).
 * Some runtimes also emit a Python-repr variant (``[{'type': 'text', ...}]``).
 * The OpenUI ``<Renderer>`` needs the inner text (the OpenUI Lang), not the
 * envelope, so unwrap both; fall through to the raw string when there's none.
 */
export function extractContentText(raw: string | undefined | null): string {
  const s = (raw ?? "").trim();
  if (!s) return "";

  // 1. JSON envelope (the common path).
  try {
    const parsed: unknown = JSON.parse(s);
    const text = readTextBlocks(parsed);
    if (text !== null) return text;
  } catch {
    /* not JSON — try repr / fall through */
  }

  // 2. Python-repr envelope from other runtimes: {'type': 'text', 'text': '…'}.
  const repr = matchReprText(s);
  if (repr !== null) return repr;

  // 3. No envelope — already raw text (OpenUI Lang passed through directly).
  return s;
}

function readTextBlocks(parsed: unknown): string | null {
  const entries = Array.isArray(parsed) ? parsed : [parsed];
  const texts: string[] = [];
  for (const e of entries) {
    if (e && typeof e === "object" && typeof (e as Record<string, unknown>).text === "string") {
      texts.push((e as Record<string, string>).text);
    }
  }
  if (texts.length) return texts.join("");
  if (typeof parsed === "string") return parsed; // double-stringified
  return null;
}

function matchReprText(s: string): string | null {
  // Match  'text': '…'  tolerating escaped quotes inside the value.
  const m = s.match(/'text'\s*:\s*'((?:[^'\\]|\\.)*)'/);
  if (!m || !m[1]) return null;
  return m[1]
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "\t")
    .replace(/\\'/g, "'")
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, "\\");
}

export interface GenerativeUICardProps {
  /** OpenUI Lang string — the generate_ui tool's output. */
  openui?: string;
  /** Tool status; "running" while the tool hasn't returned yet. */
  status?: "running" | "success" | "error";
}

/**
 * Renders the OpenUI Lang produced by the ``generate_ui`` MCP tool as live,
 * interactive components. Mounted inline via ``ConversationPage``'s
 * ``renderToolCall`` override (the same lift-out seam AskUserQuestion and
 * submit_skill use).
 */
export function GenerativeUICard({ openui, status }: GenerativeUICardProps) {
  const { t } = useI18n();
  const body = extractContentText(openui);

  return (
    <div
      data-slot="generative-ui-card"
      className="rounded-xl border border-surface-border bg-surface overflow-hidden"
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-surface-border">
        <span className="text-sm font-medium text-ink-heading">
          {t("genui.cardTitle" as Parameters<typeof t>[0])}
        </span>
        {status === "running" && <Spinner className="size-3.5" />}
      </div>
      <div className="p-3">
        {body ? (
          <ThemeProvider
            lightTheme={VALUZ_OPENUUI_THEME}
            cssSelector="[data-slot='generative-ui-card']"
          >
            <Renderer
              library={openuiLibrary}
              response={body}
              isStreaming={status === "running"}
            />
          </ThemeProvider>
        ) : (
          <div
            data-testid="genui-empty"
            className="flex items-center gap-2 text-sm text-ink-meta"
          >
            {status === "running" ? (
              <>
                <Spinner className="size-3.5" />
                {t("genui.generating" as Parameters<typeof t>[0])}
              </>
            ) : (
              t("genui.empty" as Parameters<typeof t>[0])
            )}
          </div>
        )}
      </div>
    </div>
  );
}
