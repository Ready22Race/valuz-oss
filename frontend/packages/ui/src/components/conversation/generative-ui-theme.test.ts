import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const rendererPath = join(here, "GenerativeUIRenderer.tsx");
const blocksSrc = resolve(here, "../../../../genui-blocks/src");

/**
 * Every `--openui-*` custom property a block uses has to be mapped by
 * `VALUZ_OPENUUI_THEME`, because an unmapped one does not fail — it silently
 * keeps OpenUI's own default. A block styled with unmapped tokens renders in
 * OpenUI's font and sizes beside components using the Valuz scale, and nothing
 * anywhere reports it. Two real defects motivated this test: four composite
 * `font` shorthands that kept Inter, and one property name that OpenUI does not
 * define at all, which resolved to nothing.
 *
 * The theme is parsed rather than imported so that exporting it — which would
 * trip react-refresh's only-export-components rule in a file full of
 * components — is not required.
 */

function themeKeys(): Set<string> {
  const src = readFileSync(rendererPath, "utf8");
  const start = src.indexOf("const VALUZ_OPENUUI_THEME");
  const end = src.indexOf("\n};", start);
  expect(start, "VALUZ_OPENUUI_THEME not found — was it renamed?").toBeGreaterThan(-1);
  const body = src.slice(start, end);
  return new Set(
    [...body.matchAll(/^ {2}([a-zA-Z0-9]+):/gm)].map((m) => (m[1] ?? "").toLowerCase()),
  );
}

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(css|ts|tsx)$/.test(entry) && !entry.includes(".test.")) out.push(full);
  }
  return out;
}

describe("generative-UI theme coverage", () => {
  it("maps every openui token the blocks rely on", () => {
    const mapped = themeKeys();
    const unmapped = new Map<string, string[]>();
    for (const file of walk(blocksSrc)) {
      // Skip comment bodies: a token named only to explain why it is *not*
      // used should not count as a usage.
      const code = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
      for (const m of code.matchAll(/--openui-([a-z0-9-]+)/g)) {
        const token = m[1] ?? "";
        if (mapped.has(token.replace(/-/g, ""))) continue;
        const where = unmapped.get(token) ?? [];
        const rel = file.slice(blocksSrc.length + 1);
        if (!where.includes(rel)) where.push(rel);
        unmapped.set(token, where);
      }
    }
    expect(
      [...unmapped].map(([token, files]) => `--openui-${token} (${files.join(", ")})`),
    ).toEqual([]);
  });

  it("checks a meaningful number of tokens", () => {
    // Guards the guard: a walk that silently found nothing would pass above.
    const found = new Set<string>();
    for (const file of walk(blocksSrc)) {
      for (const m of readFileSync(file, "utf8").matchAll(/--openui-([a-z0-9-]+)/g)) {
        found.add(m[1] ?? "");
      }
    }
    expect(found.size).toBeGreaterThan(30);
  });
});
