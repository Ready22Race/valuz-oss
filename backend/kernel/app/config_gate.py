"""Config-gated startup (``KERNEL_CONFIG_WAIT``) for snapshot-based sandboxes.

Micro-VM snapshot platforms (e.g. Tencent AGS auto-snapshot) cannot inject
per-instance environment at create time: an instance resumes with the
template's frozen env, and the host can only deliver per-user configuration
(``KERNEL_STORE`` / ``VALUZ_DATA_API_*`` / ``KERNEL_AUTH_TOKEN``) AFTER the
instance is running. Restarting the kernel to pick that up costs a full
interpreter restart (~1.5-2s measured, dominated by imports). The gate removes
the restart:

* the snapshot TEMPLATE boots with ``KERNEL_CONFIG_WAIT=1`` and no config file
  → the process imports everything and blocks in lifespan; the snapshot
  freezes a fully-imported, waiting kernel;
* at resume the host writes ``KERNEL_CONFIG_FILE`` (KEY=VALUE lines; ``export``
  prefixes and shell quoting are tolerated, so the same file a shell run
  script ``source``s works verbatim); the gate applies it to ``os.environ``
  and startup completes in milliseconds — no process restart.

Scope: values consumed at IMPORT time are not gate-deliverable and must live
in the template env — ``HOST`` / ``PORT`` / ``CORS_ORIGINS`` /
``KERNEL_SANDBOX_CONTROL`` (and ``DATABASE_URL`` when migrations run before
the server). Everything :class:`app.config.AppConfig` reads IS deliverable:
it is a frozen dataclass whose fields are ``default_factory`` lambdas, so the
lifespan's post-gate ``AppConfig()`` rebuild picks up the applied env.

Security: the control plane should write the file ``0600`` and ATOMICALLY
(write to a temp path, then rename) so the gate never reads a partial file.
The pre-gate kernel serves nothing — uvicorn only binds after lifespan
startup completes — so no route (and no auth decision) is reachable before
the per-instance ``KERNEL_AUTH_TOKEN`` is in place.

Default OFF: without ``KERNEL_CONFIG_WAIT=1`` the only cost is one env-var
check in lifespan; the boot path is otherwise byte-for-byte unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE = "/run/valuz/env"

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def gate_enabled() -> bool:
    """True when this process must wait for late config (``KERNEL_CONFIG_WAIT=1``)."""
    return os.getenv("KERNEL_CONFIG_WAIT") == "1"


def config_file_path() -> str:
    """The file the gate waits for (``KERNEL_CONFIG_FILE``, default /run/valuz/env)."""
    return os.getenv("KERNEL_CONFIG_FILE") or DEFAULT_CONFIG_FILE


def parse_env_lines(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines into a dict.

    Tolerates blank lines, ``#`` comments, an ``export `` prefix and
    shell-quoted values (``KEY='a b'`` → ``a b``) so a file written for a
    shell ``source`` parses identically here. Lines with malformed keys are
    skipped with a warning — never applied, never fatal.
    """
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _KEY_RE.match(key):
            logger.warning("config gate: skipping malformed line %r", raw[:80])
            continue
        value = value.strip()
        if value:
            try:
                parts = shlex.split(value)
                value = " ".join(parts)
            except ValueError:
                logger.warning("config gate: unbalanced quoting for %s; using raw value", key)
        env[key] = value
    return env


async def wait_for_config(
    path: str | None = None,
    *,
    poll_interval_s: float = 0.1,
    timeout_s: float | None = None,
) -> dict[str, str]:
    """Block until the config file exists, apply it to ``os.environ``, return it.

    ``timeout_s`` ``None`` (default) waits forever — the platform reaps stuck
    instances, and a gated kernel without config is useless anyway; set
    ``KERNEL_CONFIG_WAIT_TIMEOUT_S`` to fail fast instead (raises
    :class:`TimeoutError`).
    """
    path = path or config_file_path()
    if timeout_s is None:
        raw_timeout = os.getenv("KERNEL_CONFIG_WAIT_TIMEOUT_S")
        timeout_s = float(raw_timeout) if raw_timeout else None
    logger.info("config gate: waiting for %s", path)
    waited = 0.0
    while not os.path.exists(path):
        await asyncio.sleep(poll_interval_s)
        waited += poll_interval_s
        if timeout_s is not None and waited >= timeout_s:
            raise TimeoutError(f"config gate: {path} did not appear within {timeout_s}s")
    with open(path, encoding="utf-8") as fh:
        env = parse_env_lines(fh.read())
    os.environ.update(env)
    logger.info("config gate: applied %d vars from %s", len(env), path)
    return env
