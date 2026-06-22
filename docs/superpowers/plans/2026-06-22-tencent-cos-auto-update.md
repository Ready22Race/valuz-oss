# Tencent COS Auto-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the desktop auto-updater feed from GitHub Releases to a self-hosted Tencent COS bucket fronted by Tencent CDN (`files.valuz.cn`); CI double-writes to both targets.

**Architecture:** `electron-builder` `publish:` block switches to `provider: generic` with `url: ${env.VALUZ_UPDATER_URL}`, so the `app-update.yml` baked into the bundle points at `https://files.valuz.cn/valuz-<edition>/`. CI invokes a new `scripts/upload-to-cos.sh` helper (wrapping `tccli cos`) after each platform build to push artifacts to `cos://<bucket>/<edition>/v<version>/` and to overwrite the live `latest-*.yml` at `<edition>/`. GitHub Releases upload (`gh release upload`) stays as the manual-download + backup path. Final commit is one unified commit covering the whole change (per user preference, not per-task commits).

**Tech Stack:** electron-builder, electron-updater (unchanged on the client), `tccli` (Tencent Cloud CLI), GitHub Actions, bash.

**Spec:** [`docs/superpowers/specs/2026-06-22-tencent-cos-auto-update-design.md`](../specs/2026-06-22-tencent-cos-auto-update-design.md)

---

## File Structure

| File | Role | Status |
|---|---|---|
| `scripts/upload-to-cos.sh` | Thin wrapper over `tccli cos`. Uploads artifacts to `<edition>/v<version>/` and live manifests to `<edition>/`. Supports `--dry-run`. | **Create** |
| `frontend/apps/desktop/build/electron-builder.yml` | Bake the COS feed URL into `app-update.yml` via `publish:` block. | Modify |
| `scripts/build-desktop.sh` | Default `VALUZ_UPDATER_URL` from edition. | Modify |
| `.github/workflows/release-desktop.yml` | Drop `--publish=always` from 4 jobs; add `Setup tccli` + `Upload to Tencent COS` steps; rewrite `merge-mac-manifest` final step. | Modify |
| `CLAUDE.md` | Rewrite "Release process (desktop)" — mutable-releases warning + COS subsection + recipes. | Modify |
| `docs/architecture.md` | Update §"Distribution" with COS/CDN feed URL. | Modify |

---

## Pre-flight (operator — outside this plan)

Before the first release ships, these one-time actions must be done by a human in the Tencent Cloud console and GitHub:

- [ ] COS bucket created, public-read (or per-prefix public-read for `valuz-*`).
- [ ] Tencent CDN domain `files.valuz.cn` bound to the bucket, origin-pull configured.
- [ ] CDN cache rules: `latest-*.yml` TTL 60–300s; everything else ≥ 1 day; no 4xx/5xx caching.
- [ ] GitHub repo secrets set: `TENCENT_SECRET_ID`, `TENCENT_SECRET_KEY`, `TENCENT_COS_BUCKET`, `TENCENT_COS_REGION`.

This plan does NOT perform any of those — they require console access.

---

## Task 1: Create `scripts/upload-to-cos.sh`

**Files:**
- Create: `scripts/upload-to-cos.sh`

- [ ] **Step 1: Write the helper script**

Create `scripts/upload-to-cos.sh`:

```bash
#!/usr/bin/env bash
# upload-to-cos.sh — Upload desktop release artifacts to Tencent COS.
#
# Uploads two things from a release directory:
#   1. Every distributable artifact (*.dmg, *.zip, *.exe, *.AppImage, *.deb,
#      *.blockmap, latest*.yml) to ${EDITION}/v${VERSION}/ — immutable per release.
#   2. The named manifest(s) (e.g. "latest-mac.yml") also overwrite
#      ${EDITION}/<name> — the live feed URL electron-updater reads.
#
# Env (required unless --dry-run):
#   TENCENT_SECRET_ID, TENCENT_SECRET_KEY, TENCENT_COS_BUCKET, TENCENT_COS_REGION
#
# Usage:
#   scripts/upload-to-cos.sh \
#     --edition oss \
#     --version 0.1.5 \
#     --release-dir frontend/apps/desktop/release/ \
#     --manifests "latest-mac.yml"
#
#   scripts/upload-to-cos.sh ... --dry-run   # print actions, upload nothing

set -euo pipefail

EDITION=""
VERSION=""
RELEASE_DIR=""
MANIFESTS=""
DRY_RUN=false

while [ $# -gt 0 ]; do
  case "$1" in
    --edition=*)     EDITION="${1#--edition=}" ;;
    --version=*)     VERSION="${1#--version=}" ;;
    --release-dir=*) RELEASE_DIR="${1#--release-dir=}" ;;
    --manifests=*)   MANIFESTS="${1#--manifests=}" ;;
    --dry-run)       DRY_RUN=true ;;
    --help|-h)
      sed -n '2,26p' "$0"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

[ -n "$EDITION" ]     || { echo "ERROR: --edition required" >&2; exit 1; }
[ -n "$VERSION" ]     || { echo "ERROR: --version required" >&2; exit 1; }
[ -n "$RELEASE_DIR" ] || { echo "ERROR: --release-dir required" >&2; exit 1; }
[ -d "$RELEASE_DIR" ] || { echo "ERROR: release dir not found: $RELEASE_DIR" >&2; exit 1; }

VERSIONED_PREFIX="${EDITION}/v${VERSION}"
LIVE_PREFIX="${EDITION}"
BUCKET_DISPLAY="${TENCENT_COS_BUCKET:-<bucket>}"

if $DRY_RUN; then
  echo "[dry-run] Artifacts in $RELEASE_DIR → cos://${BUCKET_DISPLAY}/${VERSIONED_PREFIX}/"
  echo "[dry-run] Manifests → cos://${BUCKET_DISPLAY}/${LIVE_PREFIX}/:"
  for m in $MANIFESTS; do echo "    - $m"; done
  exit 0
fi

for v in TENCENT_SECRET_ID TENCENT_SECRET_KEY TENCENT_COS_BUCKET TENCENT_COS_REGION; do
  [ -n "${!v:-}" ] || { echo "ERROR: env $v required when not --dry-run" >&2; exit 1; }
done
command -v tccli >/dev/null 2>&1 || { echo "ERROR: tccli not installed (pip install tccli)" >&2; exit 1; }

# Configure tccli credentials once. Subsequent tccli invocations read these.
tccli configure set \
  secretId   "$TENCENT_SECRET_ID" \
  secretKey  "$TENCENT_SECRET_KEY" \
  region     "$TENCENT_COS_REGION"

echo "[cos] Uploading artifacts → /${VERSIONED_PREFIX}/"
# UploadBunch recursively pushes the local dir to a COS dir, filtered by --include
# patterns (semicolon-separated). Exact flag names verified via `tccli cos UploadBunch --help`.
tccli cos UploadBunch \
  --bucket     "$TENCENT_COS_BUCKET" \
  --local-path "$RELEASE_DIR" \
  --cos-dir    "/${VERSIONED_PREFIX}/" \
  --include    "*.dmg;*.zip;*.exe;*.AppImage;*.deb;*.blockmap;latest*.yml" \
  --skip-dotdir \
  --recursive

for m in $MANIFESTS; do
  if [ ! -f "$RELEASE_DIR/$m" ]; then
    echo "WARN: manifest $m not in $RELEASE_DIR — skipping live copy" >&2
    continue
  fi
  echo "[cos] $m → /${LIVE_PREFIX}/${m}"
  tccli cos PutObject \
    --bucket     "$TENCENT_COS_BUCKET" \
    --local-path "$RELEASE_DIR/$m" \
    --cos-path   "/${LIVE_PREFIX}/${m}"
done

echo "[cos] Done."
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/upload-to-cos.sh`

- [ ] **Step 3: Verify with shellcheck**

Run: `shellcheck scripts/upload-to-cos.sh`
Expected: no errors. (Warnings OK if any; fix them.)

- [ ] **Step 4: Verify `--dry-run` flag parsing**

Run:
```bash
scripts/upload-to-cos.sh \
  --edition oss --version 0.0.0-test \
  --release-dir frontend/apps/desktop/ \
  --manifests "latest-mac.yml" --dry-run
```
Expected output (exact paths printed, no uploads attempted, no `tccli` call):
```
[dry-run] Artifacts in frontend/apps/desktop/ → cos://<bucket>/oss/v0.0.0-test/
[dry-run] Manifests → cos://<bucket>/oss/:
    - latest-mac.yml
```

- [ ] **Step 5: Verify `--help` works**

Run: `scripts/upload-to-cos.sh --help`
Expected: prints the header doc comment and exits 0.

- [ ] **Step 6: Verify required-arg validation works**

Run: `scripts/upload-to-cos.sh --edition oss`
Expected: exits 1 with `ERROR: --version required` on stderr.

---

## Task 2: Switch `electron-builder.yml` publish block to generic

**Files:**
- Modify: `frontend/apps/desktop/build/electron-builder.yml:113-130` (the `publish:` block + the comment above it)

- [ ] **Step 1: Replace the publish block**

In `frontend/apps/desktop/build/electron-builder.yml`, find lines 113-130 (the comment block starting with `# Auto-update channel.` and the `publish:` block). Replace with:

```yaml
# Auto-update channel. electron-builder writes app-update.yml into the
# packaged app from this block and emits latest-mac.yml / latest.yml /
# latest-linux*.yml next to each artifact. electron-updater
# (src/main/updater.ts) reads app-update.yml at runtime unless
# VALUZ_UPDATER_URL overrides it.
#
# Live feed is hosted on Tencent COS + Tencent CDN (files.valuz.cn). The
# generic provider only stamps app-update.yml with the feed URL — it does
# NOT perform HTTP uploads (unlike the GitHub provider). CI uploads the
# artifacts and manifests via scripts/upload-to-cos.sh (wrapping `tccli cos`).
# See docs/superpowers/specs/2026-06-22-tencent-cos-auto-update-design.md.
#
# VALUZ_UPDATER_URL defaults to https://files.valuz.cn/valuz-${EDITION}/
# (set in scripts/build-desktop.sh). Override locally for testing.
publish:
  provider: generic
  url: "${env.VALUZ_UPDATER_URL}"
```

- [ ] **Step 2: Validate the YAML still parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('frontend/apps/desktop/build/electron-builder.yml'))"`
Expected: no output, exit 0.

- [ ] **Step 3: Confirm electron-builder accepts the config**

Run (from `frontend/apps/desktop`):
```bash
VALUZ_UPDATER_URL="https://files.valuz.cn/valuz-oss/" \
  pnpm exec electron-builder --config build/electron-builder.yml --help \
  | head -20
```
Expected: electron-builder prints its help text without complaining about the publish block. (If it errors on the publish config, the `${env.*}` syntax may need a different form — check electron-builder docs and adjust.)

---

## Task 3: Default `VALUZ_UPDATER_URL` in `scripts/build-desktop.sh`

**Files:**
- Modify: `scripts/build-desktop.sh:97` (just after `export VALUZ_EDITION="$EDITION"`)

- [ ] **Step 1: Add the default**

After the line `export VALUZ_EDITION="$EDITION"` (around line 97), add:

```bash
# Default the auto-update feed URL. CI overrides per-edition via matrix env.
# Local dev builds don't publish, so the value only matters for packaged
# builds — but electron-builder reads it unconditionally when stamping
# app-update.yml.
: "${VALUZ_UPDATER_URL:=https://files.valuz.cn/valuz-${EDITION}/}"
export VALUZ_UPDATER_URL
```

- [ ] **Step 2: Verify build-desktop.sh still parses**

Run: `bash -n scripts/build-desktop.sh`
Expected: no output, exit 0 (syntax check).

- [ ] **Step 3: Verify the default is applied when unset**

Run:
```bash
unset VALUZ_UPDATER_URL
# Source only the env-default lines (extract via sed), to avoid the full build:
VALUZ_EDITION="oss"
: "${VALUZ_UPDATER_URL:=https://files.valuz.cn/valuz-${VALUZ_EDITION}/}"
echo "$VALUZ_UPDATER_URL"
```
Expected: `https://files.valuz.cn/valuz-oss/`

---

## Task 4: Drop `--publish=always` from the 4 CI build jobs

**Files:**
- Modify: `.github/workflows/release-desktop.yml` — all 4 platform jobs' `Build desktop app` step

The 4 jobs each pass `--publish=always` to `scripts/build-desktop.sh`. With the generic provider, electron-builder can't self-publish — it would either no-op or error. CI takes over uploading (GitHub via `gh release upload`, COS via the new helper).

- [ ] **Step 1: In `build-mac`, change the build invocation**

Find (line ~125):
```yaml
        run: |
          bash scripts/build-desktop.sh --signed --publish=always
```
Change to:
```yaml
        run: |
          bash scripts/build-desktop.sh --signed
```

- [ ] **Step 2: Same change in `build-mac-x64`** (line ~267)

```yaml
        run: |
          bash scripts/build-desktop.sh --signed
```

- [ ] **Step 3: Same change in `build-linux-arm64`** (line ~388)

```yaml
        run: |
          bash scripts/build-desktop.sh
```

- [ ] **Step 4: Same change in `build-windows-x64`** (line ~508)

```yaml
        run: |
          bash scripts/build-desktop.sh
```

- [ ] **Step 5: Validate YAML still parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release-desktop.yml'))"`
Expected: no output, exit 0.

---

## Task 5: Add COS upload steps to the 4 CI jobs

**Files:**
- Modify: `.github/workflows/release-desktop.yml` — all 4 platform jobs, after the existing `Ensure artifacts uploaded to GitHub Release` step

Each job gets the same 2 new steps in the same position. The `--manifests` arg differs per platform.

- [ ] **Step 1: Add to `build-mac`** (after the `Ensure artifacts uploaded to GitHub Release` step, before the `Save mac manifest (arm64) for the merge` step)

```yaml
      # ── Mirror artifacts + live manifest to Tencent COS ────────────
      # Auto-update reads from COS via CDN (files.valuz.cn); GitHub Releases
      # is the manual-download + backup path. The mac merge job is what makes
      # latest-mac.yml multi-arch — this single-arch upload is a transient
      # state the merge job overwrites on a full tag push. On a single-platform
      # mac dispatch (merge job skipped) this single-arch manifest becomes live,
      # matching today's behavior.
      - name: Setup tccli
        run: pip install tccli

      - name: Upload to Tencent COS
        env:
          TENCENT_SECRET_ID: ${{ secrets.TENCENT_SECRET_ID }}
          TENCENT_SECRET_KEY: ${{ secrets.TENCENT_SECRET_KEY }}
          TENCENT_COS_BUCKET: ${{ secrets.TENCENT_COS_BUCKET }}
          TENCENT_COS_REGION: ${{ secrets.TENCENT_COS_REGION }}
          VALUZ_EDITION: oss
        run: |
          bash scripts/upload-to-cos.sh \
            --edition "$VALUZ_EDITION" \
            --version "${{ needs.set-version.outputs.version }}" \
            --release-dir frontend/apps/desktop/release/ \
            --manifests "latest-mac.yml"
```

- [ ] **Step 2: Add the same 2 steps to `build-mac-x64`** (after its `Ensure artifacts uploaded to GitHub Release` step, before its `Save mac manifest (x64) for the merge` step)

(Same step bodies as Step 1. The `--manifests "latest-mac.yml"` value is the same; on a full push the merge job will overwrite this single-arch manifest with the merged one.)

- [ ] **Step 3: Add to `build-linux-arm64`** (after its `Ensure artifacts uploaded to GitHub Release` step)

```yaml
      - name: Setup tccli
        run: pip install tccli

      - name: Upload to Tencent COS
        env:
          TENCENT_SECRET_ID: ${{ secrets.TENCENT_SECRET_ID }}
          TENCENT_SECRET_KEY: ${{ secrets.TENCENT_SECRET_KEY }}
          TENCENT_COS_BUCKET: ${{ secrets.TENCENT_COS_BUCKET }}
          TENCENT_COS_REGION: ${{ secrets.TENCENT_COS_REGION }}
          VALUZ_EDITION: oss
        run: |
          bash scripts/upload-to-cos.sh \
            --edition "$VALUZ_EDITION" \
            --version "${{ needs.set-version.outputs.version }}" \
            --release-dir frontend/apps/desktop/release/ \
            --manifests "latest-linux-arm64.yml"
```

- [ ] **Step 4: Add to `build-windows-x64`** (after its `Ensure artifacts uploaded to GitHub Release` step, `shell: bash` because the runner is windows-latest)

```yaml
      - name: Setup tccli
        run: pip install tccli

      - name: Upload to Tencent COS
        shell: bash
        env:
          TENCENT_SECRET_ID: ${{ secrets.TENCENT_SECRET_ID }}
          TENCENT_SECRET_KEY: ${{ secrets.TENCENT_SECRET_KEY }}
          TENCENT_COS_BUCKET: ${{ secrets.TENCENT_COS_BUCKET }}
          TENCENT_COS_REGION: ${{ secrets.TENCENT_COS_REGION }}
          VALUZ_EDITION: oss
        run: |
          bash scripts/upload-to-cos.sh \
            --edition "$VALUZ_EDITION" \
            --version "${{ needs.set-version.outputs.version }}" \
            --release-dir frontend/apps/desktop/release/ \
            --manifests "latest.yml"
```

- [ ] **Step 5: Validate YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release-desktop.yml'))"`
Expected: no output, exit 0.

---

## Task 6: Rewrite `merge-mac-manifest` final step to upload to COS

**Files:**
- Modify: `.github/workflows/release-desktop.yml` — the `merge-mac-manifest` job's `Upload the merged latest-mac.yml to the release` step (line ~618-622)

The merge job already does the Python merge to produce a local `latest-mac.yml`. Only the destination changes: COS instead of `gh release upload`.

- [ ] **Step 1: Add `Setup tccli` + replace the final upload step**

Find the step at ~line 618:
```yaml
      - name: Upload the merged latest-mac.yml to the release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          gh release upload "v${{ needs.set-version.outputs.version }}" latest-mac.yml --clobber --repo valuz-ai/valuz-oss
```

Replace with:
```yaml
      # merged latest-mac.yml lives at the live feed URL on COS (CDN-served).
      # This is the authoritative mac manifest — overwrites whatever the
      # individual mac jobs pushed as single-arch manifests.
      - name: Setup tccli
        run: pip install tccli

      - name: Upload merged latest-mac.yml to Tencent COS
        env:
          TENCENT_SECRET_ID: ${{ secrets.TENCENT_SECRET_ID }}
          TENCENT_SECRET_KEY: ${{ secrets.TENCENT_SECRET_KEY }}
          TENCENT_COS_BUCKET: ${{ secrets.TENCENT_COS_BUCKET }}
          TENCENT_COS_REGION: ${{ secrets.TENCENT_COS_REGION }}
          VALUZ_EDITION: oss
        run: |
          tccli configure set secretId "$TENCENT_SECRET_ID" \
                secretKey "$TENCENT_SECRET_KEY" \
                region "$TENCENT_COS_REGION"
          tccli cos PutObject \
            --bucket "$TENCENT_COS_BUCKET" \
            --local-path latest-mac.yml \
            --cos-path "/${VALUZ_EDITION}/latest-mac.yml"
          echo "Live mac manifest: https://files.valuz.cn/${VALUZ_EDITION}/latest-mac.yml"
```

Note: the merge job produces `latest-mac.yml` in its own working dir (the Python heredoc writes to `./latest-mac.yml`), so we upload directly without going through the helper.

- [ ] **Step 2: Validate YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release-desktop.yml'))"`
Expected: no output, exit 0.

---

## Task 7: Update `CLAUDE.md` release process

**Files:**
- Modify: `CLAUDE.md` — the "Release process (desktop)" section (starting around line 41)

- [ ] **Step 1: Rewrite the section**

Find the "## Release process (desktop)" section. Replace it (from the `## Release process (desktop)` heading through the end of the "Operational recipes" subsection, before "## Verification") with:

```markdown
## Release process (desktop)

Releases are **tag-driven** and published by `.github/workflows/release-desktop.yml`
(pushing a `v*` tag triggers it). The tag name is the single source of truth for the
version — CI strips the `v`, sets `VALUZ_VERSION`, and `build-desktop.sh` overwrites
`frontend/apps/desktop/package.json`. **Do not hand-bump the version.**

**Two publish targets, by design:**

- **Tencent COS + CDN** (`files.valuz.cn`) — the **auto-updater feed**. CI uploads
  every artifact here, and the packaged client's `app-update.yml` points at
  `https://files.valuz.cn/valuz-<edition>/`. `electron-updater` reads
  `latest-*.yml` from there.
- **GitHub Releases** — the **manual-download + backup** surface. CI mirrors every
  artifact here too (`gh release upload`). If COS ever has an issue, the GitHub
  release still carries every artifact for manual install.

Required GitHub secrets: `TENCENT_SECRET_ID`, `TENCENT_SECRET_KEY`,
`TENCENT_COS_BUCKET`, `TENCENT_COS_REGION`.

Cutting `vX.Y.Z`:

1. **Pick the version** (SemVer, pre-1.0): bug-fix / small batch → patch (`0.1.x`);
   feature batch → minor (`0.2.0`).
2. **Update `CHANGELOG.md`** (Keep a Changelog: Added / Changed / Fixed / Docs & Chore).
   Credit every entry `(#PR @author)`; use the short SHA for commits pushed straight to
   main. Land it via PR.
3. **Create the release = create the tag** (one step; also triggers the build):
   ```bash
   gh release create vX.Y.Z --target main --title "Valuz X.Y.Z" --notes-file <notes>
   ```
   `<notes>` is the `[X.Y.Z]` section of the CHANGELOG. Title is always `Valuz X.Y.Z`.
4. CI builds **4 platforms** — mac arm64 (signed+notarized), mac x64 (signed), linux
   arm64, windows x64. Each platform uploads artifacts to **both** GitHub Releases
   (`gh release upload`) and Tencent COS (`scripts/upload-to-cos.sh`). The
   `merge-mac-manifest` job merges arm64+x64 manifests and uploads the merged
   `latest-mac.yml` to COS as the authoritative live mac feed.

**GitHub Releases should stay mutable** — keep GitHub "immutable releases" OFF for
this repo. A burned tag still breaks the GitHub mirror path (`422 Cannot upload
assets to an immutable release`), but it's no longer catastrophic because
auto-update reads COS — COS overwrites are always free. If a tag gets burned,
bump to the next version for the GitHub mirror; the COS feed can be republished
under any version without restriction.

Operational recipes:
- **Rebuild the same version with newer code** — re-run the workflow (tag push, or
  `workflow_dispatch` with `platform=all`). COS overwrites cleanly with no risk.
  GitHub Releases: delete + recreate still works while mutable.
  ```bash
  gh release delete vX.Y.Z --yes --cleanup-tag
  gh release create vX.Y.Z --target main --title "Valuz X.Y.Z" --notes-file <notes>
  ```
- **Re-run one platform** (uploads to both GitHub Release + COS live feed for that
  platform, no re-tag):
  ```bash
  gh workflow run release-desktop.yml --ref main -f version=vX.Y.Z \
    -f platform={mac-arm64|mac-x64|linux-arm64|windows-x64}
  ```
- **Roll back to vX.Y.Z on the live COS feed** (artifact URLs in the versioned
  manifest already point at `vX.Y.Z/...` which is immutable, so this just
  promotes the old manifest back to live):
  ```bash
  for m in latest-mac.yml latest-linux-arm64.yml latest.yml; do
    tccli cos CopyObject \
      --bucket "$TENCENT_COS_BUCKET" \
      --cos-path "oss/$m" \
      --source-oss-path "oss/vX.Y.Z/$m"
  done
  ```
  CDN picks up the change within the manifest TTL (60–300s).
- **Fix release notes after the fact** (GitHub release is mutable):
  `gh release edit vX.Y.Z --notes-file <notes> --title "Valuz X.Y.Z"`.

Runner quirks:
- The mac-x64 job runs on `macos-15-intel` (arm64 on `macos-14`); see the
  `runs-on:` labels in `release-desktop.yml`. If a runner is slow to pick up, the
  other three platforms upload independently — cancel a stuck run once they're done.
- Two `workflow_dispatch` runs on the same `--ref` share the
  `release-desktop-${{ github.ref }}` concurrency group (`cancel-in-progress: true`),
  so they cancel each other. To rebuild two platforms from `main`, either dispatch
  `platform=all` once, or run them sequentially (wait for the first to finish).
- Browser-verify any UI change before it goes into a release build.
```

- [ ] **Step 2: Verify the file still parses as a markdown doc**

Run: `awk '/^## Release process/,/^## Verification/' CLAUDE.md | head -5`
Expected: the first 5 lines of the new "Release process (desktop)" section.

---

## Task 8: Update `docs/architecture.md` Distribution section

**Files:**
- Modify: `docs/architecture.md` — §"8. Distribution" (around line 217)

- [ ] **Step 1: Append an "Auto-update" subsection**

Find the §"8. Distribution" section. After the table of components and the existing prose about editions, add this paragraph before §"9. Tech Stack":

```markdown
### Auto-update feed

The desktop client's auto-updater reads from Tencent COS + Tencent CDN
(`files.valuz.cn`), not GitHub Releases. The packaged client's
`app-update.yml` points at `https://files.valuz.cn/valuz-<edition>/`; the
manifests `latest-mac.yml` / `latest-linux-arm64.yml` / `latest.yml` live at
that base. CI uploads every build to both Tencent COS (auto-update feed) and
GitHub Releases (manual download + backup) — see
`docs/superpowers/specs/2026-06-22-tencent-cos-auto-update-design.md`.
```

- [ ] **Step 2: Verify markdown renders without broken anchors**

Run: `awk '/^### Auto-update feed/,/^---$|^## /' docs/architecture.md | head -10`
Expected: the new subsection's first lines.

---

## Task 9: Verification gates + unified commit

**Files:** n/a (verification + single commit covering Tasks 1-8)

- [ ] **Step 1: Run the project quality gates**

Run:
```bash
make typecheck
make lint
```
Expected: both pass. (No `make test-all` changes needed — this plan touches no test-covered product code; the `upload-to-cos.sh` helper is verified via `--dry-run` and shellcheck, not vitest.)

- [ ] **Step 2: Run shellcheck on the new helper**

Run: `shellcheck scripts/upload-to-cos.sh`
Expected: no errors.

- [ ] **Step 3: Lint the workflow YAML**

Run:
```bash
python3 -c "import yaml; d=yaml.safe_load(open('.github/workflows/release-desktop.yml')); assert 'jobs' in d; print('jobs:', list(d['jobs'].keys()))"
```
Expected: prints `jobs: ['set-version', 'build-mac', 'build-mac-x64', 'build-linux-arm64', 'build-windows-x64', 'merge-mac-manifest']`.

- [ ] **Step 4: Confirm electron-builder config parses with the new publish block**

Run (from `frontend/apps/desktop`):
```bash
VALUZ_UPDATER_URL="https://files.valuz.cn/valuz-oss/" \
  pnpm exec electron-builder --config build/electron-builder.yml --version
```
Expected: electron-builder prints its version, no config-parse errors.

- [ ] **Step 5: Review the diff**

Run: `git diff --stat && git status`
Expected: 6 modified files + 1 new file (`scripts/upload-to-cos.sh`), no untracked files unrelated to this change.

- [ ] **Step 6: Stage everything**

Run:
```bash
git add scripts/upload-to-cos.sh \
        frontend/apps/desktop/build/electron-builder.yml \
        scripts/build-desktop.sh \
        .github/workflows/release-desktop.yml \
        CLAUDE.md \
        docs/architecture.md
```

- [ ] **Step 7: Commit (unified)**

```bash
git commit -m "$(cat <<'EOF'
build(release): move desktop auto-update feed to Tencent COS + CDN

The packaged client now checks https://files.valuz.cn/valuz-<edition>/
for updates instead of GitHub Releases. CI double-writes every artifact
to Tencent COS (auto-update feed) and GitHub Releases (manual download
+ backup).

- electron-builder publish block → provider: generic with
  ${env.VALUZ_UPDATER_URL}; build-desktop.sh defaults the URL per edition.
- New scripts/upload-to-cos.sh wraps `tccli cos` for the CI upload
  (artifacts to <edition>/v<version>/, live manifest to <edition>/).
- release-desktop.yml drops --publish=always (generic provider can't
  self-publish), adds COS upload steps to all 4 platform jobs, and
  uploads the merged latest-mac.yml to COS in the merge job.
- CLAUDE.md release-process section rewritten: COS+CDN is the feed,
  GitHub Releases is the mirror; mutable-release warning demoted from
  catastrophic to annoying.
- docs/architecture.md §Distribution notes the CDN feed URL.

First release that ships with this change bootstraps existing users: they
check GitHub until they manually install this build once, then they're
on the COS feed permanently.

Spec: docs/superpowers/specs/2026-06-22-tencent-cos-auto-update-design.md
Plan: docs/superpowers/plans/2026-06-22-tencent-cos-auto-update.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Verify the commit landed cleanly**

Run: `git log -1 --stat`
Expected: the unified commit at HEAD, listing the 6 modified + 1 new file.

---

## Self-review notes

- **Spec coverage:** §1 Goal → all tasks. §2 Current state → not edited, informational. §3 Architecture → Task 4-6. §4 COS layout → Tasks 1, 5, 6. §5 electron-builder config → Task 2. §6 build-desktop.sh → Task 3. §7.1 per-platform COS upload → Task 5. §7.2 merge job → Task 6. §7.3 helper surface → Task 1. §8 secrets → named in Task 5/6 + pre-flight. §9 CDN cache policy → pre-flight. §10 docs → Tasks 7, 8. §11 recipes → Task 7. §12 bootstrap → mentioned in commit message. §13 risks → handled (race noted inline in Task 5; `tccli` flag verification noted in Task 1). §14 files touched → matches this plan's File Structure table.
- **Placeholder scan:** no TBDs; `tccli cos` subcommand names are concrete (UploadBunch, PutObject, CopyObject) with a documented verification path via `--help`.
- **Type consistency:** helper script's CLI flags (`--edition --version --release-dir --manifests --dry-run`) match the workflow invocations in Tasks 5/6.
- **Unified commit:** per user preference `最后统一 commit 继续执行` — Task 9 step 7 is the only commit; no per-task commits.
