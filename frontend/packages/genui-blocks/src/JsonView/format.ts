import { readLooseNumber } from "../lib/props";

/**
 * Pretty-printing for structured data of unknown size and shape.
 *
 * Everything here is a *cap*. The value handed to `JsonView` comes out of a
 * model or out of a tool result it echoed, so its depth, its key count, its
 * string lengths and its total size are all unbounded — and a block renders
 * inside a chat transcript that may hold dozens of other blocks. An uncapped
 * pretty-printer turns one oversized tool result into a page that takes seconds
 * to lay out and scrolls for a screen and a half.
 *
 * Four independent limits, because they fail in different directions:
 *
 *  - **depth** — the one the caller controls, via `collapsedDepth`. Guards the
 *    deeply-nested-but-small object.
 *  - **entries per level** — guards the shallow-but-enormous object: a single
 *    map with ten thousand keys never reaches the depth cap at all.
 *  - **string length** — guards the one field holding a base64 blob or a whole
 *    document.
 *  - **total lines** — the backstop. Depth and breadth caps multiply, so their
 *    product is still large; this is the number that actually bounds the work.
 *
 * Every limit leaves an ellipsis marker behind, so what is shown is never
 * mistaken for what is there.
 *
 * Nothing in this file executes the value. `JSON.parse` is a data parser — it
 * evaluates no code, resolves no references, and calls nothing on the object it
 * builds — and it is the only place a string is ever interpreted.
 */

const DEFAULT_DEPTH = 3;
const MAX_ALLOWED_DEPTH = 8;
const MAX_ENTRIES_PER_LEVEL = 50;
const MAX_STRING_LENGTH = 200;
const MAX_LINES = 400;
const INDENT = "  ";
const ELLIPSIS = "…";

/**
 * The effective depth cap. A missing, unparseable, negative or absurd
 * `collapsedDepth` falls back to the default rather than disabling the cap —
 * the cap is the safety property, so the failure mode has to be "capped at
 * something sensible", never "uncapped".
 */
export function normaliseDepth(raw: unknown): number {
  const parsed = readLooseNumber(raw);
  if (parsed === undefined || !Number.isFinite(parsed)) return DEFAULT_DEPTH;
  return Math.max(0, Math.min(MAX_ALLOWED_DEPTH, Math.floor(parsed)));
}

/**
 * A JSON string rendered as the data it encodes.
 *
 * Models routinely hand a tool result over already stringified. Rendering that
 * as one long quoted line would be technically faithful and useless, so a
 * string that starts like an object or an array gets one parse attempt.
 * Anything else — including a string that merely contains braces — is returned
 * untouched and printed as a string.
 */
function coerce(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return value;
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return value;
  }
}

function clampString(value: string): string {
  if (value.length <= MAX_STRING_LENGTH) return value;
  return `${value.slice(0, MAX_STRING_LENGTH)}${ELLIPSIS}`;
}

/** A leaf, as source text. Never throws — a symbol or a function is not JSON,
 *  but neither is it a reason for the whole block to fail to render. */
function scalar(node: unknown): string {
  if (node === null) return "null";
  if (node === undefined) return "undefined";
  if (typeof node === "string") return JSON.stringify(clampString(node));
  if (typeof node === "number" || typeof node === "boolean") return String(node);
  if (typeof node === "bigint") return `${node.toString()}n`;
  if (typeof node === "function") return "[Function]";
  if (typeof node === "symbol") return node.toString();
  return String(node);
}

/**
 * `value` as indented, read-only source text.
 *
 * Returns plain text, not markup: the caller puts it in a `<pre>`, so React
 * escapes it and a value containing `<script>` is text about a script tag.
 */
export function formatJson(value: unknown, collapsedDepth?: unknown): string {
  const maxDepth = normaliseDepth(collapsedDepth);
  const lines: string[] = [];
  // Model output is acyclic, but `value` is typed `unknown` and this function
  // is the only thing standing between a host-supplied object graph and an
  // infinite recursion.
  const seen = new WeakSet<object>();
  let truncated = false;

  const push = (text: string): boolean => {
    if (lines.length >= MAX_LINES) {
      truncated = true;
      return false;
    }
    lines.push(text);
    return true;
  };

  const walk = (node: unknown, depth: number, indent: string, prefix: string, suffix: string) => {
    if (truncated) return;

    const isArray = Array.isArray(node);
    const isObject = !isArray && typeof node === "object" && node !== null;

    if (!isArray && !isObject) {
      push(`${indent}${prefix}${scalar(node)}${suffix}`);
      return;
    }

    const container = node as object;
    const open = isArray ? "[" : "{";
    const close = isArray ? "]" : "}";

    if (seen.has(container)) {
      push(`${indent}${prefix}${open}${ELLIPSIS} circular${close}${suffix}`);
      return;
    }

    if (depth >= maxDepth) {
      // The collapsed marker. Not "{}" — an empty object and an object whose
      // contents were withheld are different facts about the data.
      push(`${indent}${prefix}${open} ${ELLIPSIS} ${close}${suffix}`);
      return;
    }

    const entries: [string, unknown][] = isArray
      ? (node as unknown[]).map((item, i) => [String(i), item])
      : Object.entries(container);

    if (entries.length === 0) {
      push(`${indent}${prefix}${open}${close}${suffix}`);
      return;
    }

    seen.add(container);
    if (push(`${indent}${prefix}${open}`)) {
      const shown = entries.slice(0, MAX_ENTRIES_PER_LEVEL);
      const withheld = entries.length - shown.length;
      shown.forEach(([key, item], i) => {
        const last = withheld === 0 && i === shown.length - 1;
        walk(
          item,
          depth + 1,
          `${indent}${INDENT}`,
          isArray ? "" : `${JSON.stringify(key)}: `,
          last ? "" : ",",
        );
      });
      if (withheld > 0) push(`${indent}${INDENT}${ELLIPSIS} ${withheld} more`);
      push(`${indent}${close}${suffix}`);
    }
    seen.delete(container);
  };

  walk(coerce(value), 0, "", "", "");
  if (truncated) lines.push(`${ELLIPSIS} output truncated`);
  return lines.join("\n");
}
