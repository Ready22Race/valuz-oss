"""Optional override for runtime availability (design §3.3).

By default ``GET /v1/runtimes`` asks the kernel which runtimes it can launch
(``KernelClient.runtime_availability``) — correct for the bundled desktop where
the kernel is in-process. A deployment that **guarantees** its execution image's
runtime set (e.g. a cloud sandbox built and shipped by the platform) can bind
this port to declare availability directly, avoiding a per-user sandbox probe at
picker time — the runtimes are known because the image is controlled.

OSS binds no override (``None`` semantics via an unset ``ext`` attr); the kernel
probe is used. An overlay binds it for its controlled deployment.
"""

from __future__ import annotations

from typing import Protocol


class RuntimeAvailabilityPort(Protocol):
    def available_runtimes(self) -> set[str] | None:
        """Runtime ids guaranteed launchable in this deployment's execution
        environment, or ``None`` to defer to the kernel probe."""
        ...
