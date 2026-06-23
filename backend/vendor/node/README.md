# Node.js runtime — downloaded at build time (NOT vendored)

The browser feature runs the `chrome-devtools-mcp` JS tree
(`backend/vendor/chrome-devtools-mcp/`) with a real Node binary. A packaged
desktop app **cannot** rely on the user's Node: a GUI-launched macOS app gets a
stripped launchd `PATH` (`/usr/bin:/bin:/usr/sbin:/sbin`) that excludes
nvm/Homebrew/`/usr/local/bin`, and Electron's embedded Node can't be reused for
this CLI (`ELECTRON_RUN_AS_NODE` breaks yargs' `hideBin`, and the daemon
re-spawn is un-shimmable). So we ship our own Node. See
`docs/design/browser-feature.md` §8.

Unlike `rg`, the Node binary is **not committed** here — it is ~100 MB/platform,
and committing all four desktop targets would add ~0.4 GB to git history
permanently (this repo has no git-LFS). Instead it is **downloaded +
SHA256-verified at build time**:

- **Pin:** `scripts/download-node.sh` (`NODE_VERSION`, currently Node 22 LTS).
- **Build:** `scripts/build-desktop.sh` Phase A4 fetches the binary for the
  build target and stages it at `libexec/node` (skip with `--skip-node`).
- **Runtime:** `sidecar.ts` sets `VALUZ_NODE_PATH` to that absolute path so the
  backend invokes Node directly, bypassing `PATH` entirely.

Trade-off: packaging needs network (the release CI has it); a fully air-gapped
desktop build of this feature is not supported. The integrity guarantee comes
from the pinned version + checksum, not from a committed artifact.

## Refresh / bump the pin

Edit `NODE_VERSION` in `scripts/download-node.sh`, then re-test. To cache a
binary locally (e.g. for an offline build):

```bash
bash scripts/download-node.sh --target=darwin-arm64 \
  --out=frontend/apps/desktop/resources/libexec/node
```
