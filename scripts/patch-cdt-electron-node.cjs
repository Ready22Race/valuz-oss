#!/usr/bin/env node
// patch-cdt-electron-node.cjs — make the vendored chrome-devtools-mcp run
// under Electron-as-node (ELECTRON_RUN_AS_NODE=1).
//
// Why: under ELECTRON_RUN_AS_NODE, `process.versions.electron` is still set
// and `process.defaultApp` is undefined, so yargs' hideBin() misdetects the
// process as a *bundled Electron app* and slices argv at 1 instead of 2 —
// the script path leaks into positionals and every subcommand parse fails.
// The daemon (and watchdog / update-check) are re-spawned via
// `process.execPath` with the parent env inherited, so patching the single
// bundled `isBundledElectronApp()` definition fixes every process at once.
// See docs/design/browser-feature.md §8.
//
// Invoked by scripts/build-desktop.sh Phase A4 after `npm ci`, before the
// tree is staged into libexec/. Idempotent; fails loud when the expected
// upstream text is missing (an upstream bump changed the bundle — re-verify
// the patch against the new chrome-devtools-mcp before shipping).
//
// Usage: node scripts/patch-cdt-electron-node.cjs <vendor-dir>
//   <vendor-dir> = backend/vendor/chrome-devtools-mcp (holds node_modules/)

"use strict";

const fs = require("fs");
const path = require("path");

const ORIGINAL = "return isElectronApp() && !process.defaultApp;";
const PATCHED =
  "return isElectronApp() && !process.defaultApp && !process.env.ELECTRON_RUN_AS_NODE; " +
  "// valuz: Electron-as-node is plain node — see docs/design/browser-feature.md §8";

const vendorDir = process.argv[2];
if (!vendorDir) {
  console.error("usage: node patch-cdt-electron-node.cjs <vendor-dir>");
  process.exit(2);
}

const target = path.join(
  vendorDir,
  "node_modules",
  "chrome-devtools-mcp",
  "build",
  "src",
  "third_party",
  "index.js",
);

if (!fs.existsSync(target)) {
  console.error(`[patch-cdt] target not found: ${target} (run npm ci first)`);
  process.exit(1);
}

const src = fs.readFileSync(target, "utf8");

if (src.includes(PATCHED)) {
  console.log("[patch-cdt] already patched — nothing to do");
  process.exit(0);
}

const occurrences = src.split(ORIGINAL).length - 1;
if (occurrences !== 1) {
  console.error(
    `[patch-cdt] expected exactly 1 occurrence of the isBundledElectronApp ` +
      `body, found ${occurrences}. chrome-devtools-mcp bundle changed — ` +
      `re-verify Electron-as-node against the new version and update this patch.`,
  );
  process.exit(1);
}

fs.writeFileSync(target, src.replace(ORIGINAL, PATCHED));
console.log(`[patch-cdt] patched isBundledElectronApp() in ${target}`);
