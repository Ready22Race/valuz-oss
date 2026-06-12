"""Application configuration — loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _get_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    return [o.strip() for o in raw.split(",") if o.strip()]


@dataclass(frozen=True)
class AppConfig:
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agent_harness.db")
    )
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    cors_origins: list[str] = field(default_factory=_get_cors_origins)
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    # Bearer token required on every request when the kernel runs as a
    # standalone process (``KERNEL_AUTH_TOKEN``). Empty/None = open —
    # acceptable only for the in-process mount, where the host's own
    # auth fronts these routes. Applies to HTTP and the WS run channel.
    auth_token: str | None = field(default_factory=lambda: os.getenv("KERNEL_AUTH_TOKEN") or None)
