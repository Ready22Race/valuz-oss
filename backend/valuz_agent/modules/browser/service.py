"""Managed-browser lifecycle service (wraps the ``chrome-devtools`` CLI).

The single owner of "how to invoke the CLI and with what config". Consumed by:
- the ``browser_start`` / ``browser_stop`` MCP tools (model-triggered, lazy), and
- (M1) the Settings ``/v1/browser/*`` routes (status / open / stop).

Design (see docs/design/browser-feature.md):
- Operations (navigate/click/snapshot/…) are NOT here — the agent runs them via
  shell using the ``cli_prefix`` returned by ``start``. This module owns only
  daemon **management** so profile path / flags / mode stay host-owned.
- ``start`` is idempotent: a single per-user daemon is shared across sessions.
- ``managed`` mode launches a visible Chrome on the isolated persistent profile
  (``FsRegistry.browser_profile_dir``); ``attach`` connects to a user-launched
  Chrome at ``browser_attach_url``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
from pathlib import Path

from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import FsRegistry
from valuz_agent.modules.browser.errors import BrowserNodeMissing, BrowserStartFailed
from valuz_agent.modules.browser.schemas import BrowserStartResult, BrowserStatus, EnvReport

logger = logging.getLogger(__name__)

_fs = FsRegistry()

# `chrome-devtools status` prints e.g. "... daemon is running. pid=4242 socket=..."
_RUNNING_MARKER = "daemon is running"
_PID_RE = re.compile(r"pid=(\d+)")


_WRAPPER_NAME = "chrome-devtools"


def _engine_argv() -> list[str]:
    """The *real* invocation of the ``chrome-devtools`` CLI (what actually runs).

    The packaged desktop app vendors Node + the chrome-devtools-mcp JS tree and
    sets ``VALUZ_NODE_PATH`` + ``VALUZ_CDT_ENTRY`` (the Electron sidecar; see
    docs/design/browser-feature.md §8) — we then invoke ``node <entry>`` by
    absolute path, bypassing the GUI app's stripped PATH. Without both vars
    (dev/headless with Node on PATH) we fall back to ``npx`` with the pinned
    version (``settings.chrome_devtools_version``).

    Used directly by the host's own management calls (status/start/stop) and
    baked into the ``chrome-devtools`` wrapper exposed to the agent.
    """
    node = os.environ.get("VALUZ_NODE_PATH")
    entry = os.environ.get("VALUZ_CDT_ENTRY")
    if node and entry:
        return [node, entry]
    return [
        "npx",
        "-y",
        "-p",
        f"chrome-devtools-mcp@{settings.chrome_devtools_version}",
        "chrome-devtools",
    ]


def _is_windows() -> bool:
    return os.name == "nt"


def _bin_dir() -> Path:
    return _fs.browser_bin_dir()


def _wrapper_path(bin_dir: Path) -> Path:
    return bin_dir / (f"{_WRAPPER_NAME}.cmd" if _is_windows() else _WRAPPER_NAME)


def _wrapper_body(argv: list[str]) -> str:
    """A tiny wrapper that forwards to the real engine argv, so the agent runs a
    clean ``chrome-devtools <tool>`` instead of a raw ``node <abs>`` / ``npx``."""
    if _is_windows():
        quoted = " ".join(f'"{a}"' for a in argv)
        return f"@echo off\r\n{quoted} %*\r\n"
    quoted = " ".join(shlex.quote(a) for a in argv)
    return f'#!/bin/sh\nexec {quoted} "$@"\n'


def _prepend_path(directory: str) -> None:
    """Idempotently put ``directory`` first on this process's PATH."""
    sep = os.pathsep
    parts = [p for p in os.environ.get("PATH", "").split(sep) if p and p != directory]
    os.environ["PATH"] = sep.join([directory, *parts])


def _is_cli_on_path() -> bool:
    """True when the friendly wrapper is installed AND its dir is on this
    process's PATH — i.e. boot installed it before the agent subprocess spawned,
    so the agent shell can actually resolve ``chrome-devtools``."""
    bin_dir = _bin_dir()
    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        return False
    return _wrapper_path(bin_dir).is_file()


def ensure_cli_on_path() -> str | None:
    """Install a friendly ``chrome-devtools`` wrapper and put it on PATH.

    Writes a wrapper (baking the current engine argv) into the host bin dir and
    prepends that dir to this process's PATH, so every runtime subprocess
    spawned *afterwards* inherits it. **Must run at boot**, before any session
    spawns its agent subprocess (env is inherited at spawn time, not live).

    Idempotent. Returns the wrapper command name on success, or ``None`` when
    the engine is unavailable / setup failed (callers fall back to the raw
    engine prefix). Never raises — a convenience wrapper must not break boot.
    """
    if not node_available():
        return None
    try:
        bin_dir = _fs.browser_bin_dir()
        wrapper = _wrapper_path(bin_dir)
        body = _wrapper_body(_engine_argv())
        if not wrapper.is_file() or wrapper.read_text(encoding="utf-8") != body:
            wrapper.write_text(body, encoding="utf-8")
            if not _is_windows():
                wrapper.chmod(0o755)
        _prepend_path(str(bin_dir))
        return _WRAPPER_NAME
    except Exception:  # noqa: BLE001 — never break boot/session over a convenience wrapper
        logger.warning("failed to install chrome-devtools wrapper on PATH", exc_info=True)
        return None


def cli_prefix() -> str:
    """The command prefix the agent/skill uses for browser commands.

    The friendly ``chrome-devtools`` when the wrapper is installed on PATH (boot
    did this); otherwise the raw engine invocation as a fallback. This is a pure
    read — installation happens once at boot via ``ensure_cli_on_path``."""
    if _is_cli_on_path():
        return _WRAPPER_NAME
    return " ".join(_engine_argv())


def node_available() -> bool:
    """True when the CLI can run — vendored Node + entry imply a bundled runtime;
    otherwise Node must be on PATH (``npx`` needs it)."""
    if os.environ.get("VALUZ_NODE_PATH") and os.environ.get("VALUZ_CDT_ENTRY"):
        return True
    return shutil.which("node") is not None


async def _run_cli(*args: str, timeout: float = 60.0) -> tuple[int, str, str]:
    """Run ``chrome-devtools <args>``; return ``(returncode, stdout, stderr)``."""
    argv = [*_engine_argv(), *args]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        raise BrowserStartFailed(message="Timed out running the chrome-devtools CLI.") from None
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _parse_status(text: str) -> tuple[bool, int | None]:
    running = _RUNNING_MARKER in text
    m = _PID_RE.search(text)
    return running, (int(m.group(1)) if m else None)


async def detect_env() -> EnvReport:
    return EnvReport(node_ok=node_available())


_NODE_MISSING_HINT = (
    "Node.js (>= 20) was not found on PATH. Install it from https://nodejs.org, "
    "then reopen the browser."
)


async def status() -> BrowserStatus:
    node_ok = node_available()
    running, pid = False, None
    hints: list[str] = []
    if node_ok:
        _rc, out, err = await _run_cli("status", timeout=30.0)
        running, pid = _parse_status(out + "\n" + err)
    else:
        hints.append(_NODE_MISSING_HINT)
    return BrowserStatus(
        daemon_running=running,
        mode=settings.browser_mode,
        node_ok=node_ok,
        cli_prefix=cli_prefix(),
        pid=pid,
        hints=hints,
    )


async def start(user_id: str) -> BrowserStartResult:
    """Ensure the managed-browser daemon is up (idempotent). Returns the
    ``cli_prefix`` the caller should use for subsequent browser commands."""
    if not user_id:
        raise ValueError("user_id is required to start the managed browser")
    if not node_available():
        raise BrowserNodeMissing()

    current = await status()
    if current.daemon_running:
        return BrowserStartResult(
            status="already_running", mode=current.mode, cli_prefix=cli_prefix()
        )

    argv = ["start", "--headless=false"]
    if settings.browser_mode == "attach":
        argv.append(f"--browserUrl={settings.browser_attach_url}")
    else:
        argv.append(f"--userDataDir={_fs.browser_profile_dir(user_id)}")
    # P3 (deferred) appends safety flags here: --blockedUrlPattern /
    # --redactNetworkHeaders / --no-category-network (see p3-safety §5/§8).

    _rc, out, err = await _run_cli(*argv, timeout=90.0)

    # `start` prints notices to stderr and may exit 0 before the daemon is
    # fully up; verify via `status` rather than trusting the return code.
    after = await status()
    if not after.daemon_running:
        detail = (err or out or "chrome-devtools start did not bring up a daemon").strip()
        raise BrowserStartFailed(message=detail[:500])
    logger.info("managed browser started (mode=%s)", after.mode)
    return BrowserStartResult(status="started", mode=after.mode, cli_prefix=cli_prefix())


async def stop() -> None:
    """Stop the managed-browser daemon (best-effort; no-op if Node is absent)."""
    if node_available():
        await _run_cli("stop", timeout=30.0)
