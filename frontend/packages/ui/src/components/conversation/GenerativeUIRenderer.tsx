import { type ComponentProps, type ReactNode } from "react";
import { Renderer } from "@openuidev/react-lang";
import { ThemeProvider } from "@openuidev/react-ui";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

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
    return <A2UIBody body={parsed.body} />;
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
    <ThemeProvider
      lightTheme={VALUZ_OPENUUI_THEME}
      cssSelector={OPENUI_SCOPE_SELECTOR}
    >
      <Renderer
        library={openuiLibrary}
        response={body}
        isStreaming={status === "running"}
      />
    </ThemeProvider>
  );
}

function A2UIBody({ body }: { body: string }) {
  const surfaces = buildA2UISurfaces(parseA2UIMessages(body));
  if (!surfaces.length) return null;

  return (
    <div data-slot="a2ui-renderer" className="min-w-0 space-y-3">
      {surfaces.map((surface) => (
        <A2UISurface key={surface.id} surface={surface} />
      ))}
    </div>
  );
}

interface A2UISurfaceModel {
  id: string;
  data: unknown;
  components: A2UIComponent[];
}

interface A2UIComponent {
  id?: string;
  component?: string;
  type?: string;
  props?: Record<string, unknown>;
  children?: unknown;
  text?: unknown;
  value?: unknown;
}

function A2UISurface({ surface }: { surface: A2UISurfaceModel }) {
  const componentMap = new Map<string, A2UIComponent>();
  const referencedIds = new Set<string>();
  for (const component of surface.components) {
    collectComponents(component, componentMap, referencedIds);
  }
  const roots = surface.components.filter(
    (component) => !component.id || !referencedIds.has(component.id),
  );

  return (
    <div data-testid="a2ui-surface" data-a2ui-surface-id={surface.id}>
      {(roots.length ? roots : surface.components).map((component, index) =>
        renderA2UIComponent(component, {
          data: surface.data,
          componentMap,
          key: component.id ?? String(index),
        }),
      )}
    </div>
  );
}

function renderA2UIComponent(
  component: A2UIComponent,
  context: {
    data: unknown;
    componentMap: Map<string, A2UIComponent>;
    key: string;
  },
): ReactNode {
  const props = component.props ?? {};
  const type = normalizeComponentType(component.component ?? component.type);
  const children = renderA2UIChildren(component.children, context);
  const text = valueToText(
    props.text ?? props.label ?? component.text ?? component.value,
    context.data,
  );

  switch (type) {
    case "stack":
    case "column":
      return (
        <div key={context.key} className="flex min-w-0 flex-col gap-3">
          {children.length ? children : text}
        </div>
      );
    case "row":
      return (
        <div
          key={context.key}
          data-a2ui-component="row"
          className="flex min-w-0 flex-wrap gap-3"
        >
          {children.length ? children : text}
        </div>
      );
    case "grid":
      return (
        <div
          key={context.key}
          data-a2ui-component="grid"
          className="grid min-w-0 gap-3"
        >
          {children.length ? children : text}
        </div>
      );
    case "card":
    case "section":
      return (
        <section
          key={context.key}
          className="min-w-0 rounded-lg border border-surface-border bg-surface p-3"
        >
          {children.length ? children : text}
        </section>
      );
    case "heading":
    case "title":
      return (
        <h3
          key={context.key}
          className="min-w-0 text-sm font-medium leading-5 text-ink-heading"
        >
          {text}
        </h3>
      );
    case "text":
    case "markdown":
    case "paragraph":
      return (
        <p
          key={context.key}
          className="min-w-0 whitespace-pre-wrap text-sm leading-6 text-ink-body"
        >
          {text}
        </p>
      );
    case "metric":
    case "kpi":
      return renderA2UIMetric(component, context);
    case "table":
      return renderA2UITable(component, context);
    case "list":
      return renderA2UIList(component, context);
    case "separator":
    case "divider":
      return (
        <div
          key={context.key}
          className="h-px w-full bg-surface-border"
          aria-hidden="true"
        />
      );
    case "button":
      return (
        <button
          key={context.key}
          type="button"
          disabled
          className="inline-flex h-8 w-fit items-center rounded-md border border-surface-border bg-surface px-3 text-sm font-medium text-ink-label"
        >
          {text || valueToText(props.title, context.data)}
        </button>
      );
    case "tabs":
    case "accordion":
      return (
        <div key={context.key} className="min-w-0 space-y-2">
          {children.length ? children : text}
        </div>
      );
    default:
      return (
        <div key={context.key} className="min-w-0">
          {children.length ? children : text}
        </div>
      );
  }
}

function renderA2UIMetric(
  component: A2UIComponent,
  context: {
    data: unknown;
    componentMap: Map<string, A2UIComponent>;
    key: string;
  },
) {
  const props = component.props ?? {};
  return (
    <div key={context.key} className="min-w-0 rounded-lg bg-surface-soft p-3">
      <div className="truncate text-xs text-ink-meta">
        {valueToText(props.label ?? component.text, context.data)}
      </div>
      <div className="mt-1 truncate text-2xl font-medium leading-tight text-ink-heading">
        {valueToText(props.value ?? component.value, context.data)}
      </div>
      {props.caption ? (
        <div className="mt-1 text-xs text-ink-meta">
          {valueToText(props.caption, context.data)}
        </div>
      ) : null}
    </div>
  );
}

function renderA2UITable(
  component: A2UIComponent,
  context: {
    data: unknown;
    componentMap: Map<string, A2UIComponent>;
    key: string;
  },
) {
  const props = component.props ?? {};
  const columns = toArray(props.columns).map(normalizeColumn);
  const rows = toArray(resolveValue(props.rows, context.data));

  return (
    <div key={context.key} className="min-w-0 overflow-x-auto">
      <table className="w-full min-w-max border-collapse text-left text-sm">
        {columns.length ? (
          <thead>
            <tr className="border-b border-surface-border">
              {columns.map((column) => (
                <th
                  key={column.key}
                  className="whitespace-nowrap py-2 pr-3 text-xs font-medium text-ink-meta"
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
        ) : null}
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-b border-surface-border">
              {(columns.length ? columns : inferColumns(row)).map((column) => (
                <td
                  key={column.key}
                  className="whitespace-nowrap py-2 pr-3 text-ink-body"
                >
                  {valueToText(readCell(row, column.key), context.data)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderA2UIList(
  component: A2UIComponent,
  context: {
    data: unknown;
    componentMap: Map<string, A2UIComponent>;
    key: string;
  },
) {
  const props = component.props ?? {};
  const items = toArray(
    resolveValue(props.items ?? component.children, context.data),
  );
  if (!items.length) return null;

  return (
    <ul
      key={context.key}
      className="min-w-0 space-y-1 pl-4 text-sm text-ink-body"
    >
      {items.map((item, index) => (
        <li key={index} className="list-disc">
          {valueToText(item, context.data)}
        </li>
      ))}
    </ul>
  );
}

function renderA2UIChildren(
  children: unknown,
  context: {
    data: unknown;
    componentMap: Map<string, A2UIComponent>;
    key: string;
  },
): ReactNode[] {
  return toArray(children).flatMap((child, index): ReactNode[] => {
    if (typeof child === "string") {
      const referenced = context.componentMap.get(child);
      if (referenced) {
        return [
          renderA2UIComponent(referenced, {
            ...context,
            key: referenced.id ?? `${context.key}-${index}`,
          }),
        ];
      }
      return [child];
    }
    if (isA2UIComponent(child)) {
      return [
        renderA2UIComponent(child, {
          ...context,
          key: child.id ?? `${context.key}-${index}`,
        }),
      ];
    }
    return [valueToText(child, context.data)];
  });
}

function parseA2UIMessages(body: string): Record<string, unknown>[] {
  const trimmed = body.trim();
  if (!trimmed) return [];

  const parsed = safeJsonParse(trimmed);
  if (Array.isArray(parsed)) return parsed.filter(isRecord);
  if (isRecord(parsed) && isA2UIMessage(parsed)) return [parsed];

  return trimmed
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("{") && line.endsWith("}"))
    .map((line) => safeJsonParse(line))
    .filter((message): message is Record<string, unknown> => isRecord(message));
}

function buildA2UISurfaces(
  messages: Record<string, unknown>[],
): A2UISurfaceModel[] {
  const surfaces = new Map<string, A2UISurfaceModel>();

  for (const message of messages) {
    if (isRecord(message.createSurface)) {
      const surfaceId = readSurfaceId(message.createSurface, surfaces);
      surfaces.set(surfaceId, {
        id: surfaceId,
        data: {},
        components: [],
      });
      continue;
    }

    if (isRecord(message.updateDataModel)) {
      const surface = ensureSurface(
        readSurfaceId(message.updateDataModel, surfaces),
        surfaces,
      );
      surface.data =
        message.updateDataModel.data ??
        message.updateDataModel.model ??
        message.updateDataModel.patch ??
        {};
      continue;
    }

    if (isRecord(message.updateComponents)) {
      const surface = ensureSurface(
        readSurfaceId(message.updateComponents, surfaces),
        surfaces,
      );
      surface.components = mergeComponents(
        surface.components,
        toArray(message.updateComponents.components).filter(isA2UIComponent),
      );
      continue;
    }

    if (isRecord(message.deleteSurface)) {
      surfaces.delete(readSurfaceId(message.deleteSurface, surfaces));
    }
  }

  return Array.from(surfaces.values()).filter(
    (surface) => surface.components.length,
  );
}

function mergeComponents(
  previous: A2UIComponent[],
  next: A2UIComponent[],
): A2UIComponent[] {
  const byId = new Map<string, A2UIComponent>();
  const merged = [...previous];

  for (const component of next) {
    if (!component.id) {
      merged.push(component);
      continue;
    }
    byId.set(component.id, component);
    const index = merged.findIndex((item) => item.id === component.id);
    if (index >= 0) merged[index] = component;
    else merged.push(component);
  }

  return merged.map((component) =>
    component.id && byId.has(component.id) ? byId.get(component.id)! : component,
  );
}

function ensureSurface(
  surfaceId: string,
  surfaces: Map<string, A2UISurfaceModel>,
): A2UISurfaceModel {
  const existing = surfaces.get(surfaceId);
  if (existing) return existing;
  const created = { id: surfaceId, data: {}, components: [] };
  surfaces.set(surfaceId, created);
  return created;
}

function readSurfaceId(
  payload: Record<string, unknown>,
  surfaces: Map<string, A2UISurfaceModel>,
): string {
  return (
    readString(payload.surfaceId) ??
    readString(payload.surfaceID) ??
    surfaces.keys().next().value ??
    "default"
  );
}

function isA2UIMessage(value: unknown): boolean {
  return (
    isRecord(value) &&
    value.version === "v0.9" &&
    [
      "createSurface",
      "updateComponents",
      "updateDataModel",
      "deleteSurface",
    ].some((key) => key in value)
  );
}

function collectComponents(
  component: A2UIComponent,
  componentMap: Map<string, A2UIComponent>,
  referencedIds: Set<string>,
) {
  if (component.id) componentMap.set(component.id, component);
  for (const child of toArray(component.children)) {
    if (typeof child === "string") referencedIds.add(child);
    if (isA2UIComponent(child)) {
      collectComponents(child, componentMap, referencedIds);
    }
  }
}

function normalizeComponentType(value: unknown): string {
  return typeof value === "string" ? value.toLowerCase() : "text";
}

function normalizeColumn(
  value: unknown,
  index: number,
): { key: string; label: string } {
  if (typeof value === "string") return { key: String(index), label: value };
  if (isRecord(value)) {
    const key = readString(value.key) ?? readString(value.id) ?? "";
    const label = readString(value.label) ?? readString(value.title) ?? key;
    return { key, label };
  }
  return { key: String(value), label: String(value) };
}

function inferColumns(row: unknown): { key: string; label: string }[] {
  if (Array.isArray(row)) {
    return row.map((_, index) => ({ key: String(index), label: String(index) }));
  }
  if (isRecord(row)) {
    return Object.keys(row).map((key) => ({ key, label: key }));
  }
  return [{ key: "value", label: "value" }];
}

function readCell(row: unknown, key: string): unknown {
  if (Array.isArray(row)) return row[Number(key)];
  if (isRecord(row)) return row[key];
  return row;
}

function valueToText(value: unknown, data: unknown): string {
  const resolved = resolveValue(value, data);
  if (resolved === null || resolved === undefined) return "";
  if (typeof resolved === "string") return resolved;
  if (typeof resolved === "number" || typeof resolved === "boolean") {
    return String(resolved);
  }
  return JSON.stringify(resolved);
}

function resolveValue(value: unknown, data: unknown): unknown {
  if (isRecord(value)) {
    const path = readString(value.path) ?? readString(value.$path);
    if (path) return resolvePath(data, path);
  }
  return value;
}

function resolvePath(data: unknown, path: string): unknown {
  if (!path) return data;
  const parts = path.startsWith("/")
    ? path.split("/").slice(1)
    : path.split(".");
  let current = data;
  for (const rawPart of parts) {
    const part = rawPart.replace(/~1/g, "/").replace(/~0/g, "~");
    if (Array.isArray(current)) {
      current = current[Number(part)];
      continue;
    }
    if (isRecord(current)) {
      current = current[part];
      continue;
    }
    return undefined;
  }
  return current;
}

function toArray(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (value === undefined || value === null) return [];
  return [value];
}

function isA2UIComponent(value: unknown): value is A2UIComponent {
  return (
    isRecord(value) &&
    (typeof value.component === "string" || typeof value.type === "string")
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function safeJsonParse(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}
