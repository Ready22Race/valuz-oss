#!/usr/bin/env node
/**
 * GenUI CSS audit — scans OSS and finance genui-blocks stylesheets for:
 *   1. --openui-* token usage (by family)
 *   2. Hardcoded colour values (hex, rgb, hsl, named)
 *   3. Hardcoded spacing values (px/rem that aren't tokens)
 *   4. Viewport media queries (should be container queries)
 *   5. Unprefixed class names (no .vgb- or .vfb- prefix)
 *   6. Class name count per file
 *
 * Usage:
 *   node scripts/genui-css-audit.mjs           # print report, always exit 0
 *   node scripts/genui-css-audit.mjs --json    # machine-readable JSON
 *   node scripts/genui-css-audit.mjs --check   # exit 1 on real violations
 *
 * A "real violation" is a hardcoded colour/spacing, viewport @media, or
 * unprefixed class that is NOT:
 *   - a custom-property default (--vgb-chart-1: #0ea5e9)
 *   - the structural keywords transparent / currentColor
 *   - geometry (chart heights, widths, bar radii) rather than spacing
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Resolve from this script's own location so the audit works regardless of
// the working directory it is invoked from. The script lives at
// <oss>/frontend/scripts/, OSS styles under frontend/packages/..., and the
// finance edition four levels up at the monorepo root (vendor/valuz-oss →
// valuz-oss → vendor → <commercial root>).
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(__dirname, "..");
const OSS_DIR = path.join(
  FRONTEND_DIR,
  "packages/genui-blocks/src/styles",
);
const FINANCE_DIR = path.resolve(
  FRONTEND_DIR,
  "../../../editions/finance/frontend/src/genui-blocks/styles",
);

const asJson = process.argv.includes("--json");
const asCheck = process.argv.includes("--check");

// ── helpers ─────────────────────────────────────────────────────────────────

function walkDir(dir) {
  const out = [];
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    if (name.endsWith(".css")) out.push(full);
  }
  return out.sort();
}

function tokenFamily(token) {
  const t = token.replace(/^--openui-/, "");
  if (t.startsWith("text-") || t.startsWith("font")) return "typography";
  if (t.startsWith("space")) return "spacing";
  if (t.startsWith("radius")) return "radius";
  if (t.startsWith("shadow")) return "shadow";
  if (t.startsWith("border")) return "border";
  if (t.startsWith("interactive")) return "interactive";
  if (
    t.includes("background") ||
    t.startsWith("sunk") ||
    t.startsWith("elevated") ||
    t.startsWith("highlight") ||
    t.startsWith("foreground") ||
    t.startsWith("popover") ||
    t.startsWith("background")
  )
    return "surface";
  if (t.startsWith("info") || t.startsWith("success") ||
      t.startsWith("alert") || t.startsWith("danger") || t.startsWith("warning"))
    return "semantic";
  if (t.startsWith("chart") || t.includes("chart")) return "chart";
  if (t.startsWith("letter-spacing")) return "typography";
  return "other";
}

const HEX_RE = /#([0-9a-fA-F]{3,8})\b/g;
const RGB_RE = /\brgb(a?)\s*\(/g;
const HSL_RE = /\bhsl(a?)\s*\(/g;
// Named colours that are legitimate in a token-driven stylesheet:
// `transparent` and `currentColor` are structural keywords, not palette picks.
// Every other named colour is a hardcoded value the host cannot retune.
const LEGAL_NAMED_COLORS = new Set(["transparent", "currentcolor"]);
const NAMED_COLORS = new Set([
  "transparent", "currentcolor", "black", "white", "red", "blue",
  "green", "yellow", "gray", "grey", "purple", "orange", "pink",
  "cyan", "magenta", "brown", "silver", "gold", "navy", "teal",
  "maroon", "olive", "aqua", "lime",
]);

function findHardcodedColors(css, filePath) {
  const hits = [];
  const lines = css.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    // Skip lines that only declare custom properties (token defaults) or are
    // comments / at-rules — those aren't hardcoded component colours.
    if (/^\s*--/.test(line)) continue;
    if (/^\s*\/\*/.test(line)) continue;
    if (/^\s*\*/.test(line)) continue;

    const found = new Set();
    const cleaned = line.replace(/var\(--[^)]+\)/g, "");

    for (const m of cleaned.matchAll(HEX_RE)) {
      if (!/^[0-9a-f]{3,8}$/i.test(m[1])) continue;
      found.add(m[0]);
    }
    for (const m of cleaned.matchAll(RGB_RE)) found.add(`${m[1]}()`);
    for (const m of cleaned.matchAll(HSL_RE)) found.add(`${m[1]}()`);
    // named colours — be conservative, only match value context (after ":")
    const valMatch = cleaned.match(/:\s*([a-z]+)\s*[;}]/i);
    if (
      valMatch &&
      NAMED_COLORS.has(valMatch[1].toLowerCase()) &&
      !LEGAL_NAMED_COLORS.has(valMatch[1].toLowerCase())
    ) {
      found.add(valMatch[1].toLowerCase());
    }

    if (found.size) {
      hits.push({ line: i + 1, values: [...found], snippet: line.trim() });
    }
  }
  return hits;
}

const PX_RE = /:\s*[^;]*?(-?\d+\.?\d*)px/g;
const REM_RE = /:\s*[^;]*?(-?\d+\.?\d*)rem/g;

function findHardcodedSpacing(css) {
  const hits = [];
  const lines = css.split("\n");
  const spacingProps = new Set([
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "gap", "row-gap", "column-gap",
    "width", "height", "min-width", "min-height", "max-width", "max-height",
    "top", "right", "bottom", "left",
    "border-radius",
  ]);

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line || line.startsWith("/*") || line.startsWith("*")) continue;
    const colon = line.indexOf(":");
    if (colon < 0) continue;
    const prop = line.slice(0, colon).trim();
    if (!spacingProps.has(prop)) continue;

    const val = line.slice(colon + 1);
    if (val.includes("var(--openui-")) continue; // uses token
    if (val.includes("var(--vgb-")) continue; // custom token
    if (val.includes("var(--vfb-")) continue;
    if (val.includes("calc(var(")) continue; // calc with token inside

    const values = [];
    for (const m of val.matchAll(PX_RE)) values.push(`${m[1]}px`);
    for (const m of val.matchAll(REM_RE)) values.push(`${m[1]}rem`);
    if (values.length) {
      hits.push({ line: i + 1, prop, values, snippet: line });
    }
  }
  return hits;
}

function findViewportMediaQueries(css) {
  const hits = [];
  const lines = css.split("\n");
  for (let i = 0; i < lines.length; i++) {
    if (/\(min-width:|\(max-width:|\(width:/.test(lines[i]) &&
        lines[i].includes("@media")) {
      hits.push({ line: i + 1, snippet: lines[i].trim() });
    }
  }
  return hits;
}

function findUnprefixedClasses(css, prefixes) {
  const hits = [];
  const lines = css.split("\n");
  const selectorRe = /^\s*([.#][a-zA-Z][\w-]*)/;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim() || line.trim().startsWith("@")) continue;
    if (line.trim().startsWith("/*")) continue;
    if (!line.includes("{")) continue;

    const m = line.match(selectorRe);
    if (!m) continue;
    const sel = m[1];
    // Skip pseudo-classes on the same selector
    const base = sel.split(":")[0];
    // Check class selectors only
    if (!base.startsWith(".")) continue;
    if (prefixes.some((p) => base.startsWith(p))) continue;
    // Known false positives
    if (base === ".vgb-root" || base === ".vfb-root") continue;
    if (base.startsWith(".vgb-") || base.startsWith(".vfb-")) continue;
    // HTML elements with classes — check if it's element.class
    hits.push({ line: i + 1, selector: base, snippet: line.trim() });
  }
  return hits;
}

function countTokens(css) {
  const counts = {};
  const re = /var\(--openui-([^)]+)\)/g;
  for (const m of css.matchAll(re)) {
    const token = `--openui-${m[1]}`;
    counts[token] = (counts[token] || 0) + 1;
  }
  return counts;
}

function countClasses(css) {
  const set = new Set();
  const re = /\.([a-zA-Z_][\w-]*)/g;
  for (const m of css.matchAll(re)) set.add(m[1]);
  return set.size;
}

// ── main ────────────────────────────────────────────────────────────────────

function auditDir(dir, prefixes, label) {
  const files = walkDir(dir);
  const result = { label, files: {}, totals: {} };
  const allTokens = {};
  let totalClasses = 0;
  const allColorHits = [];
  const allSpacingHits = [];
  const allMediaHits = [];
  const allUnprefixedHits = [];

  for (const file of files) {
    const css = fs.readFileSync(file, "utf8");
    const tokens = countTokens(css);
    const classes = countClasses(css);
    const colorHits = findHardcodedColors(css, file);
    const spacingHits = findHardcodedSpacing(css);
    const mediaHits = findViewportMediaQueries(css);
    const unprefixed = findUnprefixedClasses(css, prefixes);

    totalClasses += classes;
    for (const [t, n] of Object.entries(tokens)) {
      allTokens[t] = (allTokens[t] || 0) + n;
    }
    allColorHits.push(...colorHits.map((h) => ({ file: path.basename(file), ...h })));
    allSpacingHits.push(...spacingHits.map((h) => ({ file: path.basename(file), ...h })));
    allMediaHits.push(...mediaHits.map((h) => ({ file: path.basename(file), ...h })));
    allUnprefixedHits.push(...unprefixed.map((h) => ({ file: path.basename(file), ...h })));

    result.files[path.basename(file)] = {
      tokenCount: Object.keys(tokens).length,
      classCount: classes,
      colorHitCount: colorHits.length,
      spacingHitCount: spacingHits.length,
      mediaHitCount: mediaHits.length,
      unprefixedCount: unprefixed.length,
    };
  }

  // Token families
  const families = {};
  for (const [t, n] of Object.entries(allTokens)) {
    const fam = tokenFamily(t);
    if (!families[fam]) families[fam] = { unique: 0, occurrences: 0, tokens: {} };
    families[fam].unique++;
    families[fam].occurrences += n;
    families[fam].tokens[t] = n;
  }

  result.totals = {
    files: files.length,
    uniqueTokens: Object.keys(allTokens).length,
    tokenOccurrences: Object.values(allTokens).reduce((a, b) => a + b, 0),
    classes: totalClasses,
    colorHits: allColorHits.length,
    spacingHits: allSpacingHits.length,
    mediaHits: allMediaHits.length,
    unprefixedClasses: allUnprefixedHits.length,
    families,
  };
  result.colorHits = allColorHits;
  result.spacingHits = allSpacingHits;
  result.mediaHits = allMediaHits;
  result.unprefixedClasses = allUnprefixedHits;
  result.tokens = allTokens;

  return result;
}

const oss = auditDir(OSS_DIR, [".vgb-"], "OSS genui-blocks");
const finDir = path.resolve(OSS_DIR, FINANCE_DIR);
const finance = fs.existsSync(finDir)
  ? auditDir(finDir, [".vfb-", ".vgb-"], "Finance genui-blocks")
  : null;

// ── output ───────────────────────────────────────────────────────────────────

if (asJson) {
  console.log(JSON.stringify({ oss, finance }, null, 2));
  process.exit(0);
}

function printSection(title) {
  console.log(`\n${"━".repeat(60)}`);
  console.log(`  ${title}`);
  console.log(`${"━".repeat(60)}`);
}

function printAudit(label, data) {
  printSection(label);
  console.log(`  Files:            ${data.totals.files}`);
  console.log(`  Classes:          ${data.totals.classes}`);
  console.log(`  Unique tokens:    ${data.totals.uniqueTokens}`);
  console.log(`  Token uses:       ${data.totals.tokenOccurrences}`);
  console.log(`  Color hits:       ${data.totals.colorHits}`);
  console.log(`  Spacing hits:     ${data.totals.spacingHits}`);
  console.log(`  Viewport @media:  ${data.totals.mediaHits}`);
  console.log(`  Unprefixed cls:   ${data.totals.unprefixedClasses}`);

  console.log(`\n  Token families:`);
  const fams = Object.entries(data.totals.families).sort(
    (a, b) => b[1].occurrences - a[1].occurrences,
  );
  for (const [name, info] of fams) {
    console.log(
      `    ${name.padEnd(14)} ${String(info.unique).padStart(3)} unique  ${String(info.occurrences).padStart(4)} uses`,
    );
  }

  // Per-file breakdown
  console.log(`\n  Per-file:`);
  for (const [file, info] of Object.entries(data.files)) {
    const flags = [];
    if (info.colorHitCount) flags.push(`color:${info.colorHitCount}`);
    if (info.spacingHitCount) flags.push(`space:${info.spacingHitCount}`);
    if (info.mediaHitCount) flags.push(`media:${info.mediaHitCount}`);
    if (info.unprefixedCount) flags.push(`unprefixed:${info.unprefixedCount}`);
    const flagStr = flags.length ? `  ⚠ ${flags.join(", ")}` : "";
    console.log(
      `    ${file.padEnd(20)} ${String(info.classCount).padStart(3)} classes  ${String(info.tokenCount).padStart(3)} tokens${flagStr}`,
    );
  }
}

printAudit("OSS genui-blocks", oss);
if (finance) printAudit("Finance genui-blocks", finance);

// Top hardcoded color hits (sample)
if (oss.colorHits.length || finance?.colorHits.length) {
  printSection("Hardcoded color samples");
  for (const h of oss.colorHits.slice(0, 10)) {
    console.log(`  [oss/${h.file}:${h.line}] ${h.values.join(", ")} — ${h.snippet.slice(0, 80)}`);
  }
  if (finance) {
    for (const h of finance.colorHits.slice(0, 10)) {
      console.log(`  [fin/${h.file}:${h.line}] ${h.values.join(", ")} — ${h.snippet.slice(0, 80)}`);
    }
  }
}

// Unprefixed class samples
if (oss.unprefixedClasses.length || finance?.unprefixedClasses.length) {
  printSection("Unprefixed class samples");
  for (const h of oss.unprefixedClasses.slice(0, 15)) {
    console.log(`  [oss/${h.file}:${h.line}] ${h.selector}`);
  }
  if (finance) {
    for (const h of finance.unprefixedClasses.slice(0, 15)) {
      console.log(`  [fin/${h.file}:${h.line}] ${h.selector}`);
    }
  }
}

// ── check mode: fail on real violations ──────────────────────────────────────

if (asCheck) {
  // Spacing hits on width/height/top/etc are GEOMETRY (chart heights, bar
  // radii, track sizes), not design spacing — those stay in px deliberately.
  // Only padding/margin/gap/border-radius px values are real violations.
  const SPACING_PROPS = new Set([
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "gap", "row-gap", "column-gap",
  ]);
  const isRealSpacingHit = (h) => SPACING_PROPS.has(h.prop);

  const violations = [];
  for (const [label, data] of [["oss", oss], ["fin", finance]]) {
    if (!data) continue;
    for (const h of data.colorHits) {
      violations.push(`${label}/${h.file}:${h.line} hardcoded colour ${h.values.join(", ")}`);
    }
    for (const h of data.spacingHits.filter(isRealSpacingHit)) {
      violations.push(`${label}/${h.file}:${h.line} spacing px on ${h.prop} (${h.values.join(", ")})`);
    }
    for (const h of data.mediaHits) {
      violations.push(`${label}/${h.file}:${h.line} viewport @media — use @container`);
    }
    for (const h of data.unprefixedClasses) {
      violations.push(`${label}/${h.file}:${h.line} unprefixed class ${h.selector}`);
    }
  }

  if (violations.length) {
    printSection("GenUI CSS audit FAILED");
    for (const v of violations) console.log(`  ✗ ${v}`);
    console.log(`\n  ${violations.length} violation(s). Use --openui-* tokens; see AUTHORING.md.`);
    process.exit(1);
  }

  console.log("\n✓ GenUI CSS audit passed — no hardcoded colours/spacing, no viewport @media, all classes prefixed.");
  process.exit(0);
}

console.log("\n");
