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
import shutil

from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import FsRegistry
from valuz_agent.modules.browser.errors import BrowserNodeMissing, BrowserStartFailed
from valuz_agent.modules.browser.schemas import BrowserStartResult, BrowserStatus, EnvReport

logger = logging.getLogger(__name__)

_fs = FsRegistry()

# `chrome-devtools status` prints e.g. "... daemon is running. pid=4242 socket=..."
_RUNNING_MARKER = "daemon is running"
_PID_RE = re.compile(r"pid=(\d+)")


def _cli_argv() -> list[str]:
    """Argv prefix to invoke the ``chrome-devtools`` CLI.

    GA vendors the bin and sets ``VALUZ_CDT_PATH``; MVP uses ``npx`` with the
    pinned version (``settings.chrome_devtools_version``).
    """
    vendored = os.environ.get("VALUZ_CDT_PATH")
    if vendored:
        return [vendored]
    return [
        "npx",
        "-y",
        "-p",
        f"chrome-devtools-mcp@{settings.chrome_devtools_version}",
        "chrome-devtools",
    ]


def cli_prefix() -> str:
    """The command prefix the agent/skill must use for browser commands."""
    return " ".join(_cli_argv())


def node_available() -> bool:
    """True when the CLI can run — a vendored bin implies a bundled runtime;
    otherwise Node must be on PATH (``npx`` needs it)."""
    if os.environ.get("VALUZ_CDT_PATH"):
        return True
    return shutil.which("node") is not None


async def _run_cli(*args: str, timeout: float = 60.0) -> tuple[int, str, str]:
    """Run ``chrome-devtools <args>``; return ``(returncode, stdout, stderr)``."""
    argv = [*_cli_argv(), *args]
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


async def start() -> BrowserStartResult:
    """Ensure the managed-browser daemon is up (idempotent). Returns the
    ``cli_prefix`` the caller should use for subsequent browser commands."""
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
        argv.append(f"--userDataDir={_fs.browser_profile_dir()}")
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
