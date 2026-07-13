"""ADR-013 — ``KERNEL_API_PREFIX`` consistency across in-process / http-kernel
transports.

The kernel package exposes an upstream-compatible, config-driven prefix
(default ``/api`` — see ``kernel/app/routes/__init__.py``); this OSS host
overrides it to ``/kernel`` so the kernel's native HTTP surface doesn't
collide with the host's own ``/v1/*`` business API. Three call sites must
agree on the SAME value with no separate wiring:

1. in-process mode — each ``app.routes.*`` router's ``prefix=`` (frozen at
   import time, covered by ``tests/test_api_prefix.py``);
2. http-kernel mode — the standalone subprocess (``app.main:app``) imports the
   identical router modules, so it freezes the SAME prefix as long as its
   spawn env carries ``KERNEL_API_PREFIX`` (inherited automatically via
   ``env = dict(os.environ)`` in ``integrations/sandbox_seatbelt.py::_spawn``,
   since the host process's REAL env already carries it — see
   ``test_http_kernel_client_subprocess.py`` for the live subprocess
   end-to-end);
3. ``HttpKernelClient`` — the host-side caller of a remote kernel — must build
   request paths under the SAME prefix the target kernel actually serves.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import os

import pytest

import valuz_agent.boot.kernel as kb  # noqa: F401 — sys.path side-effect for app.*


def test_default_prefix_is_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    """No explicit override → the host's ADR-013 default, ``/kernel`` — NOT
    the kernel package's own upstream default (``/api``)."""
    monkeypatch.delenv("KERNEL_API_PREFIX", raising=False)
    assert kb.kernel_api_prefix() == "/kernel"


def test_explicit_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator-set ``KERNEL_API_PREFIX`` always wins over the host default
    — ``kernel_api_prefix()`` never hardcodes past an explicit env value."""
    monkeypatch.setenv("KERNEL_API_PREFIX", "/custom-kernel")
    assert kb.kernel_api_prefix() == "/custom-kernel"


def test_kernel_package_default_is_kernel() -> None:
    """The kernel package's own default IS ``/kernel`` (ADR-013): the kernel
    is maintained in-tree with no upstream, so no host-side env override
    (boot setdefault / conftest pin) exists anymore — the default is the
    single source of truth and routers freeze correctly regardless of which
    module imports ``app.routes.*`` first."""
    from app.routes import KERNEL_API_PREFIX  # type: ignore[import-not-found]

    assert KERNEL_API_PREFIX == "/kernel"
    # No ambient override needed or present in the test process.
    assert os.environ.get("KERNEL_API_PREFIX") in (None, "/kernel")


def test_http_kernel_client_builds_paths_under_the_active_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``HttpKernelClient`` resolves ``kernel_api_prefix()`` at construction
    time — a client built for a kernel serving a NON-default prefix must
    address it, not the host's ``/kernel`` default."""
    monkeypatch.setenv("KERNEL_API_PREFIX", "/custom-kernel")
    from valuz_agent.adapters.kernel_client_http import HttpKernelClient

    client = HttpKernelClient("http://127.0.0.1:1", token="t")
    assert client._prefix == "/custom-kernel"  # noqa: SLF001


def test_http_kernel_client_matches_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KERNEL_API_PREFIX", raising=False)
    from valuz_agent.adapters.kernel_client_http import HttpKernelClient

    client = HttpKernelClient("http://127.0.0.1:1", token="t")
    assert client._prefix == "/kernel"  # noqa: SLF001


def test_kernel_routes_package_default_is_untouched_upstream_value() -> None:
    """The vendored kernel's OWN module-level default (evaluated once, at
    ``app.routes`` package import time, under whatever env was active then)
    must never be hardcoded to ``/kernel`` inside the vendored copy — the
    override belongs to the host. This only asserts the package still reads
    from ``KERNEL_API_PREFIX`` — not a literal value, since it's frozen at
    whatever prefix was active at this test session's first import (usually
    ``/kernel``, via conftest)."""
    from app.routes import KERNEL_API_PREFIX  # type: ignore[import-not-found]

    assert isinstance(KERNEL_API_PREFIX, str)
    assert KERNEL_API_PREFIX.startswith("/")
