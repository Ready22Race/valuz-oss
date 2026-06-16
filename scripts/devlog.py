#!/usr/bin/env python3
"""tee-with-rotation for the dev launcher.

``scripts/dev.sh`` pipes each service's stdout/stderr through this so the dev
logs under ``.ai/dev/`` are size-bounded (the plain ``tee`` it replaced grew a
single file without limit — a long ``make dev`` session reached 7 MB+). Lines
are still echoed to the foreground so Ctrl+C surfaces failures fast.

Usage:  <cmd> 2>&1 | python3 scripts/devlog.py <path> [maxBytes] [backupCount]
Defaults: 20 MiB per file, 3 backups (≈80 MiB cap per stream).
"""

from __future__ import annotations

import logging.handlers
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("devlog.py: missing log path\n")
        return 2
    path = sys.argv[1]
    max_bytes = int(sys.argv[2]) if len(sys.argv) > 2 else 20 * 1024 * 1024
    backups = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    # Rotate at startup so each run begins a fresh segment (per-run separation
    # on top of the size cap), but only when the current file has content.
    if Path(path).exists() and Path(path).stat().st_size > 0:
        handler.doRollover()

    out = sys.stdout
    # Track bytes ourselves and roll over manually — avoids feeding the handler
    # a synthetic LogRecord just to call ``shouldRollover``.
    written = handler.stream.tell()
    for line in iter(sys.stdin.readline, ""):
        out.write(line)  # foreground echo (verbatim)
        out.flush()
        handler.stream.write(line)
        handler.stream.flush()
        written += len(line.encode("utf-8", "replace"))
        if max_bytes > 0 and written >= max_bytes:
            handler.doRollover()
            written = 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BrokenPipeError, KeyboardInterrupt):
        sys.exit(0)
