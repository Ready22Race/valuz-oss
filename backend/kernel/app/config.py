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

    # Durable write-through backend (model A). The local ``SQLAlchemyStore`` on
    # ``database_url`` is ALWAYS present (local-first); ``kernel_store`` selects
    # the OPTIONAL durable target every write is also mirrored to:
    #   ``local``  (default) — none; local-only, zero change.
    #   ``pg``     — an in-process ``SQLAlchemyStore`` on ``durable_database_url``
    #                (the OSS "configure a Postgres" path; same process, no HTTP).
    #   ``remote`` — a ``RemoteStore`` over a trusted data API (HTTP), authed by
    #                ``data_api_token`` (short-lived JWT). The sandbox holds ONLY
    #                that token — never a DB DSN/driver/PG credential.
    # Set via ``KERNEL_STORE``; see ``docs`` plan saas-kernel.
    kernel_store: str = field(default_factory=lambda: os.getenv("KERNEL_STORE", "local"))
    # DSN of the in-process durable Postgres (``kernel_store=pg`` only). Distinct
    # from ``database_url`` (the local store): writes go to both.
    durable_database_url: str | None = field(
        default_factory=lambda: os.getenv("VALUZ_DURABLE_DATABASE_URL") or None
    )
    # Base URL of the remote data API (e.g. PostgREST) — remote mode only.
    data_api_url: str | None = field(
        default_factory=lambda: os.getenv("VALUZ_DATA_API_URL") or None
    )
    # Bearer token (short-lived JWT) the sandbox presents to the data API.
    # The sandbox holds ONLY this token — never a DB credential or the
    # signing secret.
    data_api_token: str | None = field(
        default_factory=lambda: os.getenv("VALUZ_DATA_API_TOKEN") or None
    )
    # Which registered RemoteStore backend to bind in remote mode. ``http`` =
    # the T1 own thin data service (default). ``postgrest`` is a future option.
    data_api_kind: str = field(default_factory=lambda: os.getenv("VALUZ_DATA_API_KIND", "http"))
