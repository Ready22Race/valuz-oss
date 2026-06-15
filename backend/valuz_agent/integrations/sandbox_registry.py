"""Sandbox driver registry — the OSS seam for pluggable kernel sandboxes.

OSS ships one built-in driver (``seatbelt`` — the local macOS sandbox). A
commercial/overlay edition adds cloud drivers (``e2b`` / ``vefaas``) WITHOUT
touching OSS: it ships a package that declares a ``valuz.sandbox_drivers``
entry point (or calls ``register`` at composition), and this module discovers
it. ``main.py`` and ``sandbox_runtime`` resolve the active driver by name
(``VALUZ_SANDBOX_DRIVER``) through ``get`` — neither knows any concrete
driver, so the whole sandbox surface is overlay-pluggable with zero OSS edits.
"""

from __future__ import annotations

import logging

from valuz_agent.ports.sandbox_provider import SandboxDriver

_log = logging.getLogger("valuz_agent.sandbox")

_drivers: dict[str, SandboxDriver] = {}
_loaded = False


def register(driver: SandboxDriver) -> None:
    """Register a driver under its ``name`` (last registration wins)."""
    _drivers[driver.name] = driver


def get(name: str | None) -> SandboxDriver | None:
    """The driver for ``name`` (e.g. ``VALUZ_SANDBOX_DRIVER``), or ``None`` when
    unset/unknown — the caller then runs the kernel in-process (the default)."""
    if not name:
        return None
    _ensure_loaded()
    return _drivers.get(name)


def available() -> list[str]:
    """Registered driver names (built-ins + discovered overlays)."""
    _ensure_loaded()
    return sorted(_drivers)


def _ensure_loaded() -> None:
    """Register built-ins + discover overlay entry points, once and lazily so
    importing this module has no side effects."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    _register_builtins()
    _discover_entry_points()


def _register_builtins() -> None:
    # OSS built-in: the local macOS Seatbelt sandbox. Imported lazily so a
    # non-macOS host (where the module still imports fine, but the driver's
    # preflight fails) doesn't pay anything until a sandbox is requested.
    try:
        from valuz_agent.integrations.sandbox_seatbelt import SeatbeltDriver

        register(SeatbeltDriver())
    except Exception:  # noqa: BLE001 — never let a builtin import break dispatch
        _log.warning("failed to register the built-in seatbelt driver", exc_info=True)


def _discover_entry_points() -> None:
    """Discover overlay drivers via the ``valuz.sandbox_drivers`` entry-point
    group — a pip-installed overlay plugs in with zero OSS edits. Each entry
    point loads to a zero-arg callable returning a ``SandboxDriver``."""
    from importlib.metadata import entry_points

    try:
        eps = entry_points(group="valuz.sandbox_drivers")
    except Exception:  # noqa: BLE001 — importlib.metadata quirks across versions
        return
    for ep in eps:
        try:
            register(ep.load()())
        except Exception:  # noqa: BLE001 — one bad plugin must not break others
            _log.warning("sandbox driver entry point %r failed to load", ep.name, exc_info=True)
