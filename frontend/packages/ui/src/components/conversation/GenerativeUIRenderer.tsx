import { type ComponentProps, type ReactNode } from "react";
import { Renderer } from "@openuidev/react-lang";
import { ThemeProvider } from "@openuidev/react-ui";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

import { A2UIRenderer } from "./A2UIRenderer";
import {
  parseGenerativeUIPayload,
  type GenerativeUIPayload,
} from "./generative-ui-payload";

type OpenUiTheme = NonNullable<
  ComponentProps<typeof ThemeProvider>["lightTheme"]
>;

export type {
  GenerativeUIPayload,
  GenerativeUIProtocol,
} from "./generative-ui-payload";

export type GenerativeUIStatus = "running" | "success" | "error";

export interface GenerativeUIRendererProps {
  payload: string | GenerativeUIPayload | undefined | null;
  status?: GenerativeUIStatus;
}

const OPENUI_SCOPE_SELECTOR = '[data-openui-scope="generative-ui"]';

const chartPalette = [
  "var(--accent-sky)",
  "var(--accent-teal)",
  "var(--accent-amber)",
  "var(--accent-pink)",
  "var(--accent-blue)",
  "var(--accent-lime)",
  "var(--accent-orange)",
  "var(--accent-fuchsia)",
];

/** Maps OpenUI directly onto the authoritative Valuz design tokens. */
const VALUZ_OPENUUI_THEME: OpenUiTheme = {
  background: "var(--color-background)",
  foreground: "var(--color-surface)",
  popoverBackground: "var(--color-surface)",
  sunkLight: "var(--color-surface-soft)",
  sunk: "var(--color-surface)",
  sunkDeep: "var(--color-surface-muted)",
  elevatedLight: "var(--color-surface-soft)",
  elevated: "var(--color-surface)",
  elevatedStrong: "var(--color-surface)",
  elevatedIntense: "var(--color-surface)",
  highlightSubtle: "var(--color-surface-soft)",
  highlight: "var(--color-surface-2)",
  highlightStrong: "var(--color-surface-muted)",
  highlightIntense: "var(--color-surface-border)",
  infoBackground: "var(--info-soft)",
  successBackground: "var(--success-soft)",
  alertBackground: "var(--warning-soft)",
  dangerBackground: "var(--error-soft)",

  textNeutralPrimary: "var(--color-ink-heading)",
  textNeutralSecondary: "var(--color-ink-body)",
  textNeutralTertiary: "var(--color-ink-disabled)",
  textNeutralLink: "var(--color-brand)",
  textBrand: "var(--color-brand)",
  textAccentPrimary: "white",
  textAccentSecondary: "var(--color-brand-700)",
  textAccentTertiary: "var(--color-brand)",
  textSuccessPrimary: "var(--success-text)",
  textSuccessInverted: "white",
  textAlertPrimary: "var(--warning-text)",
  textAlertInverted: "var(--foreground)",
  textDangerPrimary: "var(--error-text)",
  textDangerSecondary: "var(--error-text)",
  textDangerTertiary: "var(--color-ink-disabled)",
  textDangerInvertedPrimary: "white",
  textInfoPrimary: "var(--info-text)",
  textInfoInverted: "white",

  interactiveAccentDefault: "var(--color-brand)",
  interactiveAccentHover: "var(--color-brand-hover)",
  interactiveAccentPressed: "var(--color-brand-700)",
  interactiveAccentDisabled:
    "color-mix(in oklab, var(--color-brand) 40%, transparent)",
  interactiveDestructiveDefault: "var(--error-soft)",
  interactiveDestructiveHover: "var(--error-border)",
  interactiveDestructiveDisabled: "var(--color-surface-2)",
  interactiveDestructivePressed: "var(--error-border)",
  interactiveDestructiveAccentDefault: "var(--error-strong)",
  interactiveDestructiveAccentHover: "var(--error-hover)",
  interactiveDestructiveAccentPressed: "var(--error-hover)",
  interactiveDestructiveAccentDisabled:
    "color-mix(in oklab, var(--error-strong) 40%, transparent)",

  borderDefault: "var(--color-surface-border)",
  borderInteractive: "var(--color-surface-border-strong)",
  borderInteractiveEmphasis: "var(--color-surface-border-strong)",
  borderInteractiveSelected: "var(--color-brand)",
  borderAccent: "var(--color-brand)",
  borderAccentEmphasis: "var(--color-brand-600)",
  borderAccentSelected: "var(--color-brand-700)",
  borderInfo: "var(--info-border)",
  borderInfoEmphasis: "var(--color-brand)",
  borderAlert: "var(--warning-border)",
  borderAlertEmphasis: "var(--warning)",
  borderSuccess: "var(--success-border)",
  borderSuccessEmphasis: "var(--success)",
  borderDanger: "var(--error-border)",
  borderDangerEmphasis: "var(--error)",

  space000: "0px",
  space3xs: "4px",
  space2xs: "4px",
  spaceXs: "8px",
  spaceS: "8px",
  spaceSM: "12px",
  spaceM: "12px",
  spaceML: "16px",
  spaceL: "16px",
  spaceXl: "20px",
  space2xl: "24px",
  space3xl: "32px",
  radiusNone: "0px",
  radius3xs: "4px",
  radius2xs: "4px",
  radiusXs: "4px",
  radiusS: "4px",
  radiusM: "6px",
  radiusL: "8px",
  radiusXl: "10px",
  radius2xl: "12px",
  radius3xl: "12px",
  radius4xl: "12px",
  radius5xl: "12px",
  radius6xl: "12px",
  radius7xl: "12px",
  radius8xl: "12px",
  radius9xl: "12px",
  radiusFull: "9999px",

  fontBody:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontHeading:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontLabel:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontNumbers:
    '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontCode: 'ui-monospace, "SF Mono", Menlo, monospace',
  fontSize2xs: "10px",
  fontSizeXs: "11px",
  fontSizeSm: "12px",
  fontSizeMd: "13px",
  fontSizeLg: "14px",
  fontSizeXl: "15px",
  fontSize2xl: "18px",
  fontSize3xl: "24px",
  fontSize4xl: "24px",
  fontSize5xl: "24px",
  fontWeightRegular: "400",
  fontWeightMedium: "500",
  fontWeightBold: "600",
  fontWeightHeavy: "600",
  letterSpacingNormal: "0",
  letterSpacingTight: "0",
  letterSpacingTighter: "0",

  shadow0: "none",
  shadowS: "var(--shadow-outline)",
  // OpenUI's card/popover primitives already draw a border. Valuz requires
  // bordered surfaces to use the ring-free outline shadow, avoiding a double edge.
  shadowM: "var(--shadow-outline)",
  shadowL: "var(--shadow-2)",
  shadowXl: "var(--shadow-3)",
  shadow2xl: "var(--shadow-4)",
  shadow3xl: "var(--shadow-4)",

  defaultChartPalette: chartPalette,
  barChartPalette: chartPalette,
  lineChartPalette: chartPalette,
  areaChartPalette: chartPalette,
  pieChartPalette: chartPalette,
  radarChartPalette: chartPalette,
  radialChartPalette: chartPalette,
  horizontalBarChartPalette: chartPalette,
};

export function GenerativeUIRenderer({
  payload,
  status,
}: GenerativeUIRendererProps) {
  const parsed = parseGenerativeUIPayload(payload);
  if (!parsed.body) return null;

  if (parsed.protocol === "a2ui-json") {
    return <A2UIBody body={parsed.body} status={status} />;
  }

  return <OpenUIBody body={parsed.body} status={status} />;
}

function OpenUIBody({
  body,
  status,
}: {
  body: string;
  status?: GenerativeUIStatus;
}) {
  return (
    <OpenUITheme>
      <Renderer
        library={openuiLibrary}
        response={body}
        isStreaming={status === "running"}
      />
    </OpenUITheme>
  );
}

function OpenUITheme({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider
      lightTheme={VALUZ_OPENUUI_THEME}
      cssSelector={OPENUI_SCOPE_SELECTOR}
    >
      {children}
    </ThemeProvider>
  );
}

function A2UIBody({ body }: {
  body: string;
  status?: GenerativeUIStatus;
}) {
  return (
    <OpenUITheme>
      <A2UIRenderer body={body} />
    </OpenUITheme>
  );
}
