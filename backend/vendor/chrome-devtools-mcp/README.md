# chrome-devtools-mcp — vendored engine (pin only)

The browser feature drives Chrome through Google's
[`chrome-devtools-mcp`](https://github.com/ChromeDevTools/chrome-devtools-mcp)
CLI. This directory pins that engine — but **only `package.json` +
`package-lock.json` are committed**, not the code.

- **Pinned version:** `1.2.0` (see `package.json`).
- **License:** Apache-2.0 (shipped inside the installed package).
- **Platform-independent:** pure JS (deps `puppeteer-core` / `ws` / `yargs` … are
  bundled into the package's `build/`), so one install serves every platform.
  Chrome itself is *not* downloaded — the engine uses `puppeteer-core` and
  locates the user's system Chrome.

The `node_modules/` tree is **not committed** (it's `.gitignore`d) — it's
fetched at build time; committing ~350 third-party files buys nothing (no
air-gapped build either way). Integrity comes from the lockfile's
per-dependency SHAs, verified by `npm ci`.

## How it ships

`scripts/build-desktop.sh` (Phase A4) runs `npm ci --omit=dev` here, applies
`scripts/patch-cdt-electron-node.cjs` (makes yargs' `hideBin` treat
Electron-as-node as plain node — see docs/design/browser-feature.md §8), then
stages the resulting `node_modules/` into the desktop bundle at
`libexec/chrome-devtools-mcp/node_modules/`. `sidecar.ts` points the backend at
it via `VALUZ_CDT_ENTRY`
(`…/chrome-devtools-mcp/build/src/bin/chrome-devtools.js`), invoked with
`VALUZ_NODE_PATH` = the app's own Electron binary under
`ELECTRON_RUN_AS_NODE=1` (`VALUZ_NODE_IS_ELECTRON=1`) — no separate node
binary ships. With the env vars set the backend skips `npx` entirely (see
`modules/browser/service.py::_engine_argv`).

## Refresh / bump the pin

```bash
# edits package.json's pin, regenerates package-lock.json (commit both)
bash scripts/vendor-chrome-devtools-mcp.sh 1.2.0
```

Then keep `infra/config.py::chrome_devtools_version` and the dev `npx` fallback
in sync with the same version, re-run the browser smoke test, and verify
`scripts/patch-cdt-electron-node.cjs` still finds its anchor in the new
bundle (the build fails loud if not).
