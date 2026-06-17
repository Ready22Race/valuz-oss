from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "valuz-agent"
    data_dir: Path = Path.home() / ".valuz" / "app"
    db_filename: str = "valuz.db"
    # The kernel's own SQLite file — sessions / messages / events, its
    # langgraph checkpoint tables, and the kernel ``alembic_version``. Kept
    # in a SEPARATE file from the host ``valuz.db`` (sibling in ``data_dir``)
    # so it can be handed to a sandboxed/remote kernel that owns it
    # exclusively, and so dev (in-process) and dev-sandbox share one history.
    # See ``kernel_db_url`` for the resolution order.
    kernel_db_filename: str = "kernel.db"
    debug: bool = False

    # Explicit DATABASE_URL — when set, overrides the default SQLite path.
    # Accepts postgresql://... for multi-user deployments.
    database_url: str | None = None

    # Explicit override for the kernel database URL (e.g. a Postgres DSN, or
    # a custom SQLite path). When unset, the kernel still gets its OWN file —
    # ``data_dir/kernel_db_filename`` — for the local SQLite default; it only
    # shares the host database when ``database_url`` itself is set (a server
    # deployment where host + kernel deliberately co-locate). The host always
    # reaches kernel state through the ``KernelClient`` seam, never by querying
    # kernel tables on its own engine. Override with ``VALUZ_KERNEL_DATABASE_URL``.
    kernel_database_url: str | None = None

    # Kernel transport mode — which ``KernelClient`` implementation the
    # host binds at import. ``inprocess`` (default) drives the kernel's
    # route functions in this process; ``http`` addresses a kernel
    # running as a separate process at ``kernel_url`` (bare subprocess,
    # sandbox, or remote), authenticated by ``kernel_token``. Override
    # with VALUZ_KERNEL_MODE / VALUZ_KERNEL_URL / VALUZ_KERNEL_TOKEN.
    #
    # ENV CONTRACT (two sides, one secret): the standalone kernel
    # *server* reads ``KERNEL_AUTH_TOKEN`` from its own process env and
    # refuses to start without it (unless KERNEL_ALLOW_UNAUTHENTICATED=1);
    # the *host* sends ``VALUZ_KERNEL_TOKEN`` as the bearer. Whoever
    # provisions the kernel process must set both to the same secret —
    # see tests/adapters/test_http_kernel_client_subprocess.py for the
    # canonical wiring.
    kernel_mode: str = "inprocess"
    kernel_url: str = "http://127.0.0.1:8400"
    kernel_token: str | None = None

    # ── AGS / e2b cloud sandbox driver (VALUZ_SANDBOX_DRIVER=ags) ──────
    # Provisions the kernel image (see docker/kernel.Dockerfile, published to
    # ghcr.io) inside a Tencent AGS sandbox over the e2b-compatible SDK. All
    # optional — the driver's preflight reports what's missing and the host
    # falls back to in-process when unset. The API key may also come from the
    # SDK's own ``E2B_API_KEY`` env; ``ags_domain`` points the SDK at AGS
    # instead of e2b.dev. ``ags_kernel_template`` is the template/image the
    # sandbox runs (the kernel image, registered as an AGS template).
    ags_api_key: str | None = None
    ags_domain: str | None = None
    ags_kernel_template: str | None = None
    ags_kernel_port: int = 8000
    # AGS rejects the e2b ``create(envs=)`` per-sandbox env sync (500
    # "post-create env sync failed"), so the kernel's bearer must be a STATIC
    # env on the sandbox tool. Set this to that same token: the driver then
    # skips create-time env injection and uses this token to auth to the
    # kernel. Unset → dynamic mode (random token via create envs) for e2b
    # backends that honour it.
    ags_kernel_token: str | None = None
    # Path the AGS sandbox tool/template mounts the COS bucket at (the mount is
    # configured on the sandbox tool in the console, NOT by this driver). The
    # host stages a project to COS under a prefix and the kernel session cwd
    # becomes ``{mount_path}/{prefix}``. Keep in sync with the console mount.
    ags_mount_path: str = "/workspace"
    # Safety caps on per-project stage-in (loud log + stop, never silent
    # truncation): max files and max total bytes uploaded to COS.
    ags_stage_max_files: int = 5000
    ags_stage_max_bytes: int = 200_000_000
    # Sandbox lifetime hint (seconds). AGS 常驻 sandboxes are effectively
    # no-timeout; this is the create-time timeout the e2b SDK requires. Large
    # by default so a long-running kernel isn't reaped mid-session. (Stock e2b
    # caps this; AGS's true no-timeout is a backend-side convention.)
    ags_sandbox_timeout_s: int = 86_400
    # e2b ``secure``: when True the exposed sandbox URL is gated by an e2b
    # traffic-access token (403 without it). We default FALSE because the
    # kernel enforces its OWN bearer (``KERNEL_AUTH_TOKEN``) on every non-health
    # route, so the host can reach it directly; flip True only if you also
    # thread the e2b traffic token (not wired here).
    ags_secure: bool = False

    # ── COS (object store backing the cloud sandbox workspace, ⑤) ─────
    # Tencent COS is S3-compatible; the host writes the project here under a
    # per-project prefix and AGS mounts the bucket so the kernel sees it as
    # ``/workspace``. Secrets belong in ``.env`` (git-ignored) / the secret
    # store, never in code — production should prefer a CAM role / STS over a
    # long-lived SecretId/SecretKey. ``cos_endpoint`` defaults to the regional
    # COS S3 endpoint when unset.
    cos_bucket: str | None = None
    cos_region: str = "ap-beijing"
    cos_secret_id: str | None = None
    cos_secret_key: str | None = None
    cos_endpoint: str | None = None

    @property
    def is_http_kernel(self) -> bool:
        """True when the kernel runs as a SEPARATE process (subprocess /
        sandbox / remote) and the host drives it over HTTP. Boot must then
        skip the in-process kernel bootstrap — migrations, store/orchestrator
        singletons, kernel router mounting, and orphan scans — because the
        standalone kernel owns all of that (see
        ``docs/design/kernel-sandbox-deployment.md`` §B.6 / B2–B5)."""
        return self.kernel_mode == "http"

    @property
    def kernel_callback_base_url(self) -> str:
        """Base URL the kernel uses to call back into the host (④ face).

        The host-served MCP servers (docs / automations / connectors /
        harness) are injected into sessions against this base. In ``http``
        mode an explicit ``host_external_url`` wins (the remote kernel can't
        reach loopback); otherwise — and always in-process — it is the
        host's own ``backend_base_url``."""
        if self.is_http_kernel and self.host_external_url:
            return self.host_external_url
        return self.backend_base_url

    @property
    def kernel_callback_is_loopback(self) -> bool:
        """True when the resolved callback base points at loopback — a
        footgun for a remote kernel (it would dial the sandbox, not the
        host). Boot warns on this in ``http`` mode."""
        base = self.kernel_callback_base_url
        return "127.0.0.1" in base or "localhost" in base or "[::1]" in base

    # ── Backend self-URL ─────────────────────────────────────────────
    # Where the host's own FastAPI is reachable from inside the same
    # process / container. Used to inject the in-process docs MCP server
    # URL into the kernel's ``session.mcp_servers`` so the agent's MCP
    # client (running in the kernel runtime) can call back into the host
    # for ``doc_search`` / ``list_doc_scope``. Override with
    # ``VALUZ_BACKEND_BASE_URL`` (e.g. ``http://127.0.0.1:18080``) when
    # the launcher pins a custom port.
    backend_base_url: str = "http://127.0.0.1:8000"

    # ── Host callback URL for a REMOTE kernel (④ tool-callback face) ──
    # The four host-served MCP servers (docs / automations / connectors /
    # harness) are injected into every session as HTTP URLs the kernel's
    # MCP client dials back into. With an in-process or same-host
    # (Seatbelt) kernel, ``backend_base_url`` (loopback) is reachable and
    # correct. With the kernel in a SEPARATE host the agent runs on — a
    # cloud sandbox (e.g. Tencent AGS) — loopback points at the sandbox
    # itself, not the host, and every callback tool (doc_search, memory,
    # task dispatch…) would fail. Set this to an address the remote kernel
    # can actually reach the host on (LAN / public / reverse-proxy URL).
    # Only consulted in ``http`` kernel mode; ``kernel_callback_base_url``
    # falls back to ``backend_base_url`` when unset. A NAT'd desktop host
    # has no such address — that case needs the tunnel/queue transport
    # (``docs/design/kernel-sandbox-deployment.md`` §3.6, deferred), not
    # this setting. Override with ``VALUZ_HOST_EXTERNAL_URL``.
    host_external_url: str | None = None

    # Custom URL scheme the desktop shell registers (Electron
    # ``setAsDefaultProtocolClient`` — see
    # frontend/apps/desktop/src/main/deep-link-utils.ts ``DEEP_LINK_PROTOCOL``).
    # The connector OAuth callback hands its result back to the running app via a
    # ``<scheme>://connector-oauth?...`` deep link. Keep in sync with the
    # frontend constant; override with ``VALUZ_DEEP_LINK_PROTOCOL`` for an
    # edition that ships under a different scheme.
    deep_link_protocol: str = "valuz-oss"

    # Shared secret the docs MCP server checks against the
    # ``X-Valuz-Internal`` header. Generated per process; effectively
    # localhost-only since the URL never leaves the box, but it's a cheap
    # extra defence against accidental cross-origin leakage.
    internal_mcp_token_override: str | None = None

    # Hard cap on attachments per session — counts local uploads and
    # KB-sourced references together. Both the multipart upload route
    # and the KB-attach route reject requests that would push the
    # session past this; the desktop UI greys out the attachment menu
    # entries once the count is reached. Override with
    # ``VALUZ_MAX_SESSION_ATTACHMENTS``.
    max_session_attachments: int = 20

    # Whether CLI-subscription model channels (Claude Pro·Max via ``claude
    # /login``, Codex via ``codex /login``) are offered. These authenticate
    # out-of-band against a **local** CLI keychain, so they only make sense
    # where that keychain exists — the desktop app and local/LAN headless runs.
    # A shared multi-user server (no per-user CLI keychain) sets this False so
    # the subscription templates are not surfaced in the providers list.
    # Override with ``VALUZ_SUBSCRIPTION_LOGIN_ENABLED``.
    subscription_login_enabled: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.db_path}"

    @property
    def db_url_async(self) -> str:
        if self.database_url:
            return self._to_async_url(self.database_url)
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def kernel_db_path(self) -> Path:
        return self.data_dir / self.kernel_db_filename

    @property
    def kernel_db_url(self) -> str:
        """Sync-driver URL for the kernel's database.

        Resolution order:
        1. ``kernel_database_url`` — explicit override (Postgres / custom path).
        2. ``database_url`` — an explicit host DB (e.g. a shared Postgres
           server) co-locates the kernel there, preserving the single-store
           layout server deployments rely on.
        3. Otherwise (the local SQLite default) the kernel gets its OWN
           ``kernel.db`` file, separate from the host ``valuz.db``.
        """
        if self.kernel_database_url:
            return self.kernel_database_url
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.kernel_db_path}"

    @property
    def kernel_db_url_async(self) -> str:
        if self.kernel_database_url:
            return self._to_async_url(self.kernel_database_url)
        if self.database_url:
            return self._to_async_url(self.database_url)
        return f"sqlite+aiosqlite:///{self.kernel_db_path}"

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")

    @staticmethod
    def _to_async_url(url: str) -> str:
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("sqlite://"):
            return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return url

    @property
    def docs_dir(self) -> Path:
        return self.data_dir / "docs"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    # ── Installation identity ────────────────────────────────────────
    # Where the locally-generated owner id (int32) is persisted. Lives
    # OUTSIDE the business tables so a DB clean-up rebuild never loses it
    # (see ``infra.local_identity.resolve_local_user_id``). Assigned once
    # on first install from a device fingerprint and stable thereafter.
    installation_filename: str = "installation.json"

    @property
    def installation_file(self) -> Path:
        return self.data_dir / self.installation_filename

    # ── Logging paths ────────────────────────────────────────────────
    # ``infra.logging.configure_logging`` writes structured JSON lines
    # to ``log_file`` via a RotatingFileHandler so the desktop ``服务``
    # panel can display + offer "open in editor" without depending on
    # whichever shell launched the process. ``log_dir`` is created on
    # first write — we don't ``mkdir`` here so the property stays pure.
    log_filename: str = "backend.log"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def log_file(self) -> Path:
        return self.log_dir / self.log_filename

    # Per-session scratch dir where skill-creator writes draft skills.
    # Each session gets a subdirectory named after its session_id; inside,
    # the agent creates one directory per skill (slug-named) containing
    # SKILL.md plus any bundled scripts/references/assets. Empty default
    # means "data_dir/skill-creator/staging"; set via VALUZ_SKILL_STAGING_DIR.
    skill_staging_dir_override: Path | None = None

    @property
    def skill_staging_dir(self) -> Path:
        return self.skill_staging_dir_override or (self.data_dir / "skill-creator" / "staging")

    @property
    def internal_mcp_token(self) -> str:
        """Per-process token for the in-process docs MCP server.

        Lazily generated so tests can monkey-patch
        ``internal_mcp_token_override`` deterministically. The token is
        kept in memory only — never persisted, never logged in full.
        """
        global _RUNTIME_TOKEN
        if self.internal_mcp_token_override:
            return self.internal_mcp_token_override
        if _RUNTIME_TOKEN is None:
            import secrets

            _RUNTIME_TOKEN = secrets.token_urlsafe(24)
        return _RUNTIME_TOKEN

    # ── User-facing project root ───────────────────────────────────
    # Base directory for user-visible projects (not hidden).
    # Defaults to ~/Valuz; override with VALUZ_USER_PROJECT_ROOT.
    user_project_root: Path = Path.home() / "Valuz"

    model_config = {"env_prefix": "VALUZ_"}


_RUNTIME_TOKEN: str | None = None
settings = Settings()
