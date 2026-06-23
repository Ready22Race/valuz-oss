# Desktop Auto-Update → Tencent COS + CDN

**Status:** Approved (2026-06-22)
**Author:** hanjixin
**Scope:** Desktop release pipeline only — no client behavior change beyond the auto-update feed URL.

---

## 1. Goal

Move the desktop auto-update feed off GitHub Releases onto a self-hosted
Tencent Cloud Object Storage (COS) bucket fronted by a Tencent CDN custom
domain. CI continues to publish artifacts to GitHub Releases (manual download
+ backup) — only the **auto-updater** reads COS. Releases keep their tag-driven
flow (`v*` tag → `release-desktop.yml`).

Non-goals:

- No changes to in-app update UI, the update window, or the download/install
  loop — `electron-updater` is the same library with a different feed URL.
- No changes to code-signing / notarization.
- No changes to the tag-driven release cadence or SemVer scheme.
- Enterprise / finance editions are not blocked, but only the OSS edition is
  wired up initially; the same pattern applies per edition.

## 2. Current State

- `frontend/apps/desktop/build/electron-builder.yml` has
  `publish: { provider: github, owner: valuz-ai, repo: valuz-oss, releaseType: release }`.
- `.github/workflows/release-desktop.yml` builds 4 platforms, calls
  `scripts/build-desktop.sh --signed --publish=always` so electron-builder
  uploads each platform's artifacts to the matching GitHub release, then
  re-uploads via a `gh release upload --clobber` fallback.
- A `merge-mac-manifest` job merges the per-arch `latest-mac.yml` from the
  two mac runners and re-uploads the merged manifest.
- `frontend/apps/desktop/src/main/updater.ts` already supports a runtime
  override via `VALUZ_UPDATER_URL` → `autoUpdater.setFeedURL({ provider: 'generic', url })`.
- `CLAUDE.md` §"Release process (desktop)" warns about GitHub "immutable
  releases" burning tags — a constraint that disappears once the live feed
  moves to COS (overwrite is always free).

## 3. Target Architecture

```
                         ┌──► GitHub Releases  (manual download + backup)
CI build (4 platforms) ──┤
                         └──► Tencent COS  ──►  CDN (files.valuz.cn)  ──►  electron-updater
```

- CI **double-writes** every artifact: GitHub Releases (existing path) and COS.
- The packaged client's `app-update.yml` points at `https://files.valuz.cn/valuz-<edition>/`.
- `electron-updater` fetches `latest-mac.yml` / `latest-linux-arm64.yml` /
  `latest.yml` from that base and downloads the per-version artifact the
  manifest references (same CDN).
- GitHub Releases remains the canonical place a human visits to download a
  `.dmg` / `.exe` / `.AppImage` outside the app.

## 4. COS Bucket Layout

Per-edition + per-version subdirectory. The versioned subdir preserves every
release for rollback; the edition root hosts the live manifests that
`electron-updater` reads.

```
<bucket>/valuz-oss/
  v0.1.5/
    valuz-oss-v0.1.5-darwin-arm64.dmg           (+ .blockmap)
    valuz-oss-v0.1.5-darwin-arm64.zip
    valuz-oss-v0.1.5-darwin-x64.dmg             (+ .blockmap)
    valuz-oss-v0.1.5-darwin-x64.zip
    valuz-oss-v0.1.5-linux-arm64.AppImage
    valuz-oss-v0.1.5-linux-arm64.deb
    valuz-oss-v0.1.5-windows-x64.exe            (+ .blockmap)
    latest-mac.yml                              # per-version, archival
    latest-linux-arm64.yml
    latest.yml
  latest-mac.yml                                # LIVE feed URL electron-updater reads
  latest-linux-arm64.yml
  latest.yml
```

Rules:

- Edition prefix is the `VALUZ_EDITION` env var (`oss` | `enterprise` | `finance`).
- Every release uploads to both `${EDITION}/v${VERSION}/...` (immutable) and
  overwrites the live manifests at `${EDITION}/latest-*.yml`.
- Old versions are retained indefinitely (or by a future lifecycle rule).
- Mac arm64 + x64 artifacts live side-by-side in the same versioned subdir; the
  **merged** `latest-mac.yml` at `${EDITION}/latest-mac.yml` is what
  electron-updater actually reads on macOS.

## 5. electron-builder Config

`frontend/apps/desktop/build/electron-builder.yml` — the `publish:` block
changes from GitHub to a generic provider. The generic provider writes the
manifest locally and stamps `app-update.yml` with the feed URL; it does **not**
perform HTTP uploads, so `--publish=always` becomes a no-op and we drop it.

```yaml
publish:
  provider: generic
  url: "${env.VALUZ_UPDATER_URL}"    # https://files.valuz.cn/valuz-oss/
```

`VALUZ_UPDATER_URL` is set by `build-desktop.sh` (next section) with a per-edition
default, overridable for local testing.

## 6. `scripts/build-desktop.sh`

- Drop `--publish=always` semantics. The flag still parses for compatibility,
  but when the publish block is `generic`, electron-builder only writes manifests
  locally — actual uploads move to the CI workflow.
- Set `VALUZ_UPDATER_URL` from edition if unset:

  ```bash
  : "${VALUZ_UPDATER_URL:=https://files.valuz.cn/valuz-${EDITION}/}"
  export VALUZ_UPDATER_URL
  ```

- No other changes to build phases.

## 7. CI Workflow — `.github/workflows/release-desktop.yml`

### 7.1 Per-platform jobs (build-mac, build-mac-x64, build-linux-arm64, build-windows-x64)

For each job:

1. Keep the existing build step (drop `--publish=always` from the
   `scripts/build-desktop.sh` invocation; `--signed` stays where applicable).
2. Keep the existing `Verify latest-*.yml generated` step (it already fails loud
   on a missing/empty manifest).
3. Keep the existing `Ensure artifacts uploaded to GitHub Release` step — this
   becomes the primary GH upload path now that electron-builder no longer
   self-publishes.
4. **Add** a `Setup tccli` step (one-liner `pip install tccli`).
5. **Add** an `Upload to Tencent COS` step that calls a new helper
   `scripts/upload-to-cos.sh` with the local release dir, edition, version, and
   platform manifest filename list. The helper:
   - Configures `tccli` from secrets (`TENCENT_SECRET_ID`, `TENCENT_SECRET_KEY`,
     `TENCENT_COS_REGION`).
   - Uploads all artifacts to `cos://${TENCENT_COS_BUCKET}/${EDITION}/v${VER}/`.
   - Overwrites the live manifest(s) at `${EDITION}/latest-*.yml` so a
     single-platform `workflow_dispatch` (e.g. `platform=linux-arm64`) cuts over
     the live feed for that platform too. The full-platform tag push also runs
     the merge-mac-manifest job (below) which is authoritative for macOS.

### 7.2 `merge-mac-manifest` job

The merge logic (download per-arch manifests via `actions/download-artifact`,
union the `files:` list in Python) stays. The final step changes from
`gh release upload latest-mac.yml` to:

```bash
scripts/upload-to-cos.sh \
  --file latest-mac.yml \
  --cos-path "/${EDITION}/latest-mac.yml" \
  --content-type "text/yaml"
```

(One destination, atomic. The COS overwrite + CDN short-TTL replaces the old
"clobber GitHub release" dance.)

### 7.3 New helper: `scripts/upload-to-cos.sh`

A thin wrapper over `tccli cos` so each job's YAML stays readable. Rough surface:

```bash
scripts/upload-to-cos.sh \
  --edition oss \
  --version 0.1.5 \
  --release-dir frontend/apps/desktop/release/ \
  --manifests "latest-mac.yml"            # platform-specific manifest filenames
```

Responsibilities:

- Read `TENCENT_SECRET_ID`, `TENCENT_SECRET_KEY`, `TENCENT_COS_BUCKET`,
  `TENCENT_COS_REGION` from the environment.
- `tccli configure set` with those credentials.
- Upload everything in `--release-dir` (filtered to distributable extensions
  plus `latest*.yml`) to `cos://<bucket>/<edition>/v<version>/`.
- Copy each manifest named in `--manifests` to `cos://<bucket>/<edition>/<name>`
  (the live feed URL).
- Log every key it writes.

## 8. Required GitHub Secrets

| Secret | Value |
|---|---|
| `TENCENT_SECRET_ID` | COS sub-account SecretId, scoped to the release bucket only |
| `TENCENT_SECRET_KEY` | matching SecretKey |
| `TENCENT_COS_BUCKET` | e.g. `valuz-releases-1300000000` |
| `TENCENT_COS_REGION` | e.g. `ap-shanghai` |

`VALUZ_UPDATER_URL` is **not** a secret — it's derived from `VALUZ_EDITION` in
`build-desktop.sh` (production default: `https://files.valuz.cn/valuz-${EDITION}/`).

## 9. CDN Cache Policy (Tencent CDN console — manual one-time config)

- `latest-*.yml` (root of each edition prefix) → TTL **60–300s** so a new
  release is visible within minutes.
- Versioned artifacts (`*.dmg`, `*.zip`, `*.exe`, `*.AppImage`, `*.deb`,
  `*.blockmap`) → TTL **≥ 1 day** (immutable per version).
- Don't cache 4xx / 5xx.
- Serve range requests (electron-updater uses them for `.blockmap` differential
  downloads).

These are configured in the Tencent CDN console against the `files.valuz.cn`
domain, not in this repo.

## 10. Docs Updates

### 10.1 `CLAUDE.md` §"Release process (desktop)"

- Rewrite the "immutable releases" warning: with COS as the live feed, the
  burned-tag problem no longer blocks auto-update. GitHub Releases still
  benefits from staying mutable, but a burned tag is no longer catastrophic
  because CI can publish the same version to COS without touching GitHub.
- Add a "COS upload" subsection under step 4 (CI builds 4 platforms) noting
  the double-write and listing the required secrets.
- Update operational recipes: **rebuild same version** and **rollback** now
  describe COS key overwrites (one `tccli cos cp` per manifest) rather than
  GitHub release delete + recreate.

### 10.2 `docs/architecture.md` §"Distribution"

Add a short note: the live auto-update feed lives at
`https://files.valuz.cn/valuz-<edition>/latest-*.yml`, backed by Tencent COS,
served via Tencent CDN. GitHub Releases remains for manual download.

## 11. Operational Recipes

- **Rebuild same version with newer code** — re-run the workflow (tag push, or
  `workflow_dispatch` with `platform=all`). COS overwrites cleanly; no
  burned-tag risk. The GitHub Release side still benefits from
  `gh release delete --cleanup-tag` + recreate as before, but it's no longer
  load-bearing for auto-update.
- **Roll back to vX.Y.Z** — overwrite the live manifests from the archived
  versioned copy:

  ```bash
  for m in latest-mac.yml latest-linux-arm64.yml latest.yml; do
    tccli cos copy \
      --bucket "$TENCENT_COS_BUCKET" \
      --cos-path "${EDITION}/${m}" \
      --src "${EDITION}/v${VER}/${m}"
  done
  ```

  CDN picks up the change within the manifest TTL.
- **Re-run one platform** — `gh workflow run release-desktop.yml -f version=vX.Y.Z -f platform=<...>`.
  Each platform job updates its own live manifest, so a single-platform
  dispatch rolls forward that platform only.

## 12. First-Release Bootstrapping

The first release that ships with this design produces a client whose
`app-update.yml` points at `https://files.valuz.cn/valuz-oss/`. Existing users
on prior versions still check GitHub until they manually install this release
once — both feeds coexist, so nothing breaks during the cutover.

## 13. Risks & Edge Cases

- **`provider: generic` + `--publish=always`**: electron-builder's generic
  provider does not implement publishing (no HTTP upload). We must drop
  `--publish=always` from CI invocations of `build-desktop.sh`. The workflow
  continues to use `gh release upload` for GitHub and the new helper for COS.
- **Mac x64 runner scarce** (`macos-15-intel`): the merge job still waits for
  both arches. If x64 doesn't pick up, the live mac manifest stays at the prior
  version — same failure mode as today, on COS instead of GitHub.
- **Mac manifest race during a full push**: the two mac jobs each write a
  single-arch `latest-mac.yml` to the live URL; whichever finishes last wins
  until the `merge-mac-manifest` job runs and overwrites with the merged
  manifest. The window is bounded by job-start skew + merge job runtime, and
  self-heals — acceptable. (Same transient as today's GitHub Releases flow;
  the comments at `release-desktop.yml:565-572` describe the same single-arch
  exposure for single-platform mac dispatches.) If we later want to eliminate
  it: gate the mac jobs on writing only to `v<version>/`, and let the merge
  job be the sole writer of the live mac manifest — at the cost of a
  single-platform mac dispatch no longer cutting over the live feed.
- **`tccli cos` subcommand surface**: this spec uses `tccli cos upload-bunch`,
  `tccli cos cp`, and `tccli cos copy`. Exact subcommand names/flags get
  verified during implementation planning against the installed `tccli`
  version. The intent (upload artifacts to versioned prefix, atomically write
  live manifests) is what's load-bearing here.
- **CDN `Content-Type` for `.yml`**: COS auto-detects; typically served as
  `application/octet-stream` or `text/yaml`. electron-updater reads bytes and
  parses YAML — either is fine.
- **CDN propagation delay**: a new release is visible to clients within the
  manifest TTL (60–300s). Artifacts themselves are immutable per version, so
  they're cache-friendly.
- **Bucket public-read**: the bucket (or at least the edition prefixes) must be
  anonymous-readable for electron-updater to fetch manifests + artifacts.
  Configured in the COS console, not in this repo.

## 14. Files Touched

| File | Change |
|---|---|
| `frontend/apps/desktop/build/electron-builder.yml` | `publish:` block → `provider: generic` + `${env.VALUZ_UPDATER_URL}` |
| `scripts/build-desktop.sh` | drop `--publish=always` semantics; default `VALUZ_UPDATER_URL` from edition |
| `.github/workflows/release-desktop.yml` | add COS upload steps to 4 jobs; rewrite merge-mac-manifest final step |
| `scripts/upload-to-cos.sh` | **new** — `tccli cos` wrapper used by the workflow |
| `CLAUDE.md` | rewrite "Release process (desktop)" section (mutable-releases warning, COS upload subsection, recipes) |
| `docs/architecture.md` | Distribution section — note CDN/COS feed URL |
