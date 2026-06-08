# ripgrep — vendored binary

Source: https://github.com/BurntSushi/ripgrep/releases/tag/15.1.0
Archive: ripgrep-15.1.0-x86_64-pc-windows-msvc.zip
License: MIT (see https://github.com/BurntSushi/ripgrep/blob/master/LICENSE-MIT)

Used by backend/valuz_agent/providers/docs_embedded.py via the
``VALUZ_RG_PATH`` env injected from the Electron sidecar.

## Refresh

```
bash scripts/download-rg.sh x86_64-pc-windows-msvc
# then copy the result into backend/vendor/rg/windows-amd64/rg.exe
# and regenerate SHA256SUMS:
( cd backend/vendor/rg/windows-amd64 && shasum -a 256 rg.exe > SHA256SUMS )
```
