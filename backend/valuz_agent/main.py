"""Module entry point for ``python -m valuz_agent``.

Boots the backend on the conventional dev port (8000) so the desktop shell's
default API base hits it without configuration. Handy for one-shot launches
and the dev-startup script.

Run:
    uv run python -m valuz_agent
    uv run python -m valuz_agent --port 18080 --reload
    uv run python -m valuz_agent --help

For programmatic / CLI multiplexing use ``valuz_agent.cli`` (Typer-based; the
``serve`` subcommand has the same effect as this module).
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from valuz_agent.infra.config import settings


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m valuz_agent",
        description="Start the Valuz Agent backend (FastAPI + V5 kernel mounted).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1; use 0.0.0.0 for LAN)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port (default: 8000 — matches frontend's default VITE_API_BASE_URL)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload on source changes (dev only)",
    )
    return parser.parse_args(argv)


def _provision_sandboxed_kernel(args: argparse.Namespace) -> None:
    """Provision the kernel sandbox for ``VALUZ_SANDBOX_DRIVER`` and point the
    host at it (http mode), via the driver registry.

    Driver-agnostic: the active driver (``seatbelt`` built-in; ``e2b`` /
    ``vefaas`` from an overlay) owns preflight, provisioning, and re-attach —
    this function only resolves it from the registry and wires the result.
    Re-entrant under uvicorn ``--reload``: a reloader child sees the env vars
    the parent set and re-attaches instead of re-provisioning. Mutates the
    ``settings`` singleton AND the env so both the current process and any
    re-import path select http mode against the provisioned endpoint.
    """
    import asyncio
    import atexit
    import logging
    import os

    from valuz_agent.adapters import kernel_client
    from valuz_agent.integrations import sandbox_registry, sandbox_runtime
    from valuz_agent.ports.sandbox_provider import SandboxBootContext, SandboxEndpoint

    log = logging.getLogger("valuz_agent.sandbox")

    driver_name = os.environ.get("VALUZ_SANDBOX_DRIVER")
    driver = sandbox_registry.get(driver_name)
    if driver is None:
        # Unset or unknown driver → run the kernel in-process (the default).
        if driver_name:
            log.warning(
                "unknown VALUZ_SANDBOX_DRIVER=%r (available: %s) — running in-process",
                driver_name,
                ", ".join(sandbox_registry.available()) or "none",
            )
        return

    ctx = SandboxBootContext(
        host=args.host,
        port=args.port,
        host_callback_url=os.environ.get("VALUZ_BACKEND_BASE_URL", ""),
        passthrough_env={
            k: v
            for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
            if (v := os.environ.get(k)) is not None
        },
    )

    def _wire(
        result_provider: object, base_url: str, token: str, static_roots: tuple[str, ...]
    ) -> None:
        settings.kernel_mode = "http"
        settings.kernel_url = base_url
        settings.kernel_token = token
        os.environ["VALUZ_KERNEL_MODE"] = "http"
        os.environ["VALUZ_KERNEL_URL"] = base_url
        os.environ["VALUZ_KERNEL_TOKEN"] = token
        kernel_client.rebind_client()
        sandbox_runtime.activate(result_provider, "host-kernel", static_roots)  # type: ignore[arg-type]

    # Already provisioned by a parent process (reload child) — re-attach.
    if os.environ.get("VALUZ_KERNEL_URL"):
        endpoint = SandboxEndpoint(
            sandbox_id="host-kernel",
            base_url=os.environ["VALUZ_KERNEL_URL"],
            token=os.environ.get("VALUZ_KERNEL_TOKEN", ""),
        )
        result = driver.attach(ctx, endpoint)
        _wire(result.provider, endpoint.base_url, endpoint.token, result.static_roots)
        return

    # Preflight BEFORE doing any work. The user asked for a sandbox explicitly
    # (VALUZ_SANDBOX_DRIVER=...) — if the host can't provide one we FAIL LOUD
    # rather than silently falling back to the in-process kernel, which would
    # leave them believing the agent is confined when it isn't (a security
    # surprise). Set VALUZ_SANDBOX_FALLBACK=inprocess for a warned degrade.
    problems = driver.preflight()
    if problems:
        msg = f"{driver_name} sandbox unavailable: " + "; ".join(problems)
        if os.environ.get("VALUZ_SANDBOX_FALLBACK") == "inprocess":
            log.warning("%s — falling back to in-process kernel (UNSANDBOXED)", msg)
            return
        raise SystemExit(
            f"{msg}\n"
            f"Refusing to start unsandboxed after an explicit "
            f"VALUZ_SANDBOX_DRIVER={driver_name}. Fix the environment, unset the "
            "driver to run in-process, or set VALUZ_SANDBOX_FALLBACK=inprocess "
            "to degrade with a warning."
        )

    result = asyncio.run(driver.provision_for_boot(ctx))
    log.warning("kernel running in %s sandbox at %s", driver_name, result.endpoint.base_url)
    _wire(result.provider, result.endpoint.base_url, result.endpoint.token, result.static_roots)
    atexit.register(lambda: asyncio.run(result.provider.destroy("host-kernel")))


def main(argv: list[str] | None = None) -> int:
    import multiprocessing
    import os

    # The document parser runs in a ``ProcessPoolExecutor`` (see
    # ``infra/parse_pool``). Under a PyInstaller-frozen ``valuz-server`` the
    # ``spawn`` start method re-launches this executable as a worker; without
    # ``freeze_support`` the child would re-run the server (and the pool would
    # never start). A no-op when not frozen, so it's safe to always call —
    # and it must run before any pool is created.
    multiprocessing.freeze_support()

    from valuz_agent.modules.system.service import (
        record_boot_started,
        record_listen_port,
    )

    args = _parse_args(argv)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # NB: structured JSON logging is configured **inside** the FastAPI
    # startup hook (see ``api/app.py``), NOT here. Uvicorn calls
    # ``logging.config.dictConfig`` during its own boot which wipes
    # existing handlers from the root logger (CPython's
    # ``_clearExistingHandlers``), so any handler installed before
    # ``uvicorn.run`` is gone by the time the app is alive.
    # Tell the docs MCP injector where the host is reachable from inside
    # the same process. The kernel's MCP client connects via this URL.
    # Honour an explicitly-set VALUZ_BACKEND_BASE_URL (deployment override);
    # otherwise compose from the bind address. ``0.0.0.0`` is replaced with
    # ``127.0.0.1`` because the kernel client must hit a routable host.
    if "VALUZ_BACKEND_BASE_URL" not in os.environ:
        host_for_self = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
        os.environ["VALUZ_BACKEND_BASE_URL"] = f"http://{host_for_self}:{args.port}"

    # Optional: run the kernel inside a local sandbox (the minimal form of
    # ``docs/design/kernel-sandbox-deployment.md`` §B). When
    # ``VALUZ_SANDBOX_DRIVER=seatbelt`` is set, provision a sandbox-confined
    # kernel and switch the host to http mode against it BEFORE the app is
    # constructed (the transport + router-mount decisions read settings at
    # import time). Default (unset) is byte-identical in-process mode.
    if os.environ.get("VALUZ_SANDBOX_DRIVER"):
        _provision_sandboxed_kernel(args)

    # Capture startup metadata for the desktop ``服务`` panel before
    # uvicorn forks/spawns workers. ``record_listen_port`` is module-
    # level state read by ``GET /v1/system/status`` since uvicorn's
    # actual socket isn't exposed on the FastAPI app object.
    record_boot_started()
    record_listen_port(args.port)

    uvicorn.run(
        "valuz_agent.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
