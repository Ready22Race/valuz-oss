import { type ComponentProps, type ReactNode } from "react";
import { ThemeProvider } from "@openuidev/react-ui";

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
  /** Host render-context values for `$host` data-ref params, forwarded to
   *  the A2UI renderer's edition data host (e.g. {symbol: "US:NVDA"} on a
   *  company workbench). */
  hostParams?: Record<string, string | number | boolean>;
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

  // OpenUI also ships composite `font` shorthands, and they are not derived
  // from the primitives above — left unmapped they keep OpenUI's own defaults
  // (`400 14px/1.25 "Inter"`), so any component using one renders in Inter at
  // OpenUI's sizes while everything beside it uses the Valuz stack and scale.
  // Composing them from the mapped primitives is what puts them back under
  // this theme instead of restating the font stack a fifth time.
  textBodyDefault:
    "var(--openui-font-weight-regular) var(--openui-font-size-xl)/1.5 var(--openui-font-body)",
  textBodyLg:
    "var(--openui-font-weight-regular) var(--openui-font-size-2xl)/1.5 var(--openui-font-body)",
  textBodyLgHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-2xl)/1.5 var(--openui-font-body)",
  textBodySm:
    "var(--openui-font-weight-regular) var(--openui-font-size-lg)/1.5 var(--openui-font-body)",
  textBodySmHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-lg)/1.5 var(--openui-font-body)",
  textBodyXs:
    "var(--openui-font-weight-regular) var(--openui-font-size-sm)/1.5 var(--openui-font-body)",
  textBodyXsHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-sm)/1.5 var(--openui-font-body)",
  textCodeDefault:
    "var(--openui-font-weight-regular) var(--openui-font-size-lg)/1.5 var(--openui-font-code)",
  textCodeDefaultHeavy:
    "var(--openui-font-weight-heavy) var(--openui-font-size-lg)/1.5 var(--openui-font-code)",
  textCodeSm:
    "var(--openui-font-weight-regular) var(--openui-font-size-sm)/1.5 var(--openui-font-code)",
  textCodeSmHeavy:
    "var(--openui-font-weight-heavy) var(--openui-font-size-sm)/1.5 var(--openui-font-code)",
  textHeadingLg:
    "var(--openui-font-weight-bold) var(--openui-font-size-3xl)/1.1 var(--openui-font-heading)",
  textHeadingMd:
    "var(--openui-font-weight-bold) var(--openui-font-size-3xl)/1.1 var(--openui-font-heading)",
  textHeadingSm:
    "var(--openui-font-weight-bold) var(--openui-font-size-2xl)/1.25 var(--openui-font-heading)",
  textHeadingXl:
    "var(--openui-font-weight-heavy) var(--openui-font-size-3xl)/1.1 var(--openui-font-heading)",
  textHeadingXs:
    "var(--openui-font-weight-bold) var(--openui-font-size-xl)/1.25 var(--openui-font-heading)",
  textLabelDefault:
    "var(--openui-font-weight-regular) var(--openui-font-size-xl)/1.25 var(--openui-font-label)",
  textLabelLg:
    "var(--openui-font-weight-regular) var(--openui-font-size-2xl)/1.25 var(--openui-font-label)",
  textLabelLgHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-2xl)/1.25 var(--openui-font-label)",
  textLabelSmHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-lg)/1.25 var(--openui-font-label)",
  textLabelXs:
    "var(--openui-font-weight-regular) var(--openui-font-size-sm)/1.25 var(--openui-font-label)",
  textLabelXsHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-sm)/1.25 var(--openui-font-label)",
  textNumbersDefault:
    "var(--openui-font-weight-regular) var(--openui-font-size-xl)/1.5 var(--openui-font-numbers)",
  textNumbersDefaultHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-xl)/1.5 var(--openui-font-numbers)",
  textNumbersHeadingLg:
    "var(--openui-font-weight-bold) var(--openui-font-size-3xl)/1.1 var(--openui-font-numbers)",
  textNumbersHeadingSm:
    "var(--openui-font-weight-bold) var(--openui-font-size-2xl)/1.25 var(--openui-font-numbers)",
  textNumbersHeadingXl:
    "var(--openui-font-weight-bold) var(--openui-font-size-3xl)/1.1 var(--openui-font-numbers)",
  textNumbersLg:
    "var(--openui-font-weight-regular) var(--openui-font-size-2xl)/1.5 var(--openui-font-numbers)",
  textNumbersLgHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-2xl)/1.5 var(--openui-font-numbers)",
  textNumbersSm:
    "var(--openui-font-weight-regular) var(--openui-font-size-lg)/1.5 var(--openui-font-numbers)",
  textNumbersSmHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-lg)/1.5 var(--openui-font-numbers)",
  textNumbersXs:
    "var(--openui-font-weight-regular) var(--openui-font-size-sm)/1.5 var(--openui-font-numbers)",
  textNumbersXsHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-sm)/1.5 var(--openui-font-numbers)",
  textLabelSm:
    "var(--openui-font-weight-regular) var(--openui-font-size-sm)/1.25 var(--openui-font-label)",
  textLabelDefaultHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-lg)/1.25 var(--openui-font-label)",
  textBodyDefaultHeavy:
    "var(--openui-font-weight-medium) var(--openui-font-size-lg)/1.5 var(--openui-font-body)",
  textNumbersHeadingMd:
    "var(--openui-font-weight-bold) var(--openui-font-size-3xl)/1.1 var(--openui-font-numbers)",

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
  hostParams,
}: GenerativeUIRendererProps) {
  const parsed = parseGenerativeUIPayload(payload);
  // Nothing to draw rather than raw text on screen: a tool result that is not
  // an A2UI stream has no renderer, and printing its source where a rendered UI
  // belongs reads as a bug in the answer rather than in the payload.
  //
  // EXCEPT while the run is live. An empty payload there is a WAIT, not an
  // absence — the model routinely reasons for a minute before writing its
  // first byte, and returning null through all of it leaves the surface that
  // is supposed to be showing the generation completely blank. Hand the empty
  // body down so ``A2UIRenderer`` makes the call in ONE place: it breathes a
  // skeleton until something resolves, here and everywhere else.
  if (!parsed?.body) {
    if (status !== "running") return null;
    return <A2UIBody body="" status={status} hostParams={hostParams} />;
  }

  return (
    <A2UIBody body={parsed.body} status={status} hostParams={hostParams} />
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

function A2UIBody({
  body,
  status,
  hostParams,
}: {
  body: string;
  status?: GenerativeUIStatus;
  hostParams?: Record<string, string | number | boolean>;
}) {
  return (
    <OpenUITheme>
      {/* `data-a2ui-streaming` marks the subtree as still filling in. A partly
          written payload renders the components that have arrived, so without
          the marker a half-built document is indistinguishable from a finished
          one that came out short. */}
      <div
        className="vgb-root"
        data-a2ui-streaming={status === "running" ? "true" : undefined}
      >
        <A2UIRenderer
          body={body}
          status={status === "running" ? "running" : "success"}
          hostParams={hostParams}
        />
      </div>
    </OpenUITheme>
  );
}
