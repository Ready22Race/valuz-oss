"""AGS / e2b-compatible cloud sandbox driver — run the kernel in the cloud.

Provisions the kernel image (``docker/kernel.Dockerfile``, published to
ghcr.io) inside a **Tencent AGS** sandbox over the e2b-compatible Python SDK,
and hands the host an ``HttpKernelClient``-ready endpoint. This is the C-line
cloud form of the ① supply face (see
``docs/design/kernel-sandbox-deployment.md`` §3.2 / §3.6); the local form is
``integrations/sandbox_seatbelt``.

Registered as ``ags`` in ``sandbox_registry``. Opt-in via
``VALUZ_SANDBOX_DRIVER=ags`` plus the ``VALUZ_AGS_*`` settings; its
``preflight`` reports anything missing so an unconfigured host falls back to
in-process cleanly. The ``e2b`` SDK is an OPTIONAL dependency (extra ``ags``)
and is imported lazily — OSS installs without it pay nothing.

Scope (P2): provisioning + lifecycle. File staging into the sandbox
(``bind_workspace``) is the ⑤ materials face and lands in P3 — until then a
remote kernel runs in its own empty ``/workspace`` (see ``bind_workspace``).
The ④ tool-callback to a NAT'd desktop host is a known gap (P4); a host with a
reachable ``VALUZ_HOST_EXTERNAL_URL`` works today.

Setup (one-time, operator):
  1. Register the kernel image as an AGS/e2b **template** (templates are
     referenced by id/name at create time). From the published image, the e2b
     flow needs an explicit start command even though the image has an
     ENTRYPOINT, e.g.::

         E2B_API_KEY=e2b_… E2B_DOMAIN=<ags-domain> \
           e2b template build -n valuz-kernel \
             -c "/usr/bin/tini -- /app/kernel-entrypoint.sh"

     (whether AGS pulls ``ghcr.io`` at build time is an AGS-side question.)
  2. Configure the host: ``VALUZ_SANDBOX_DRIVER=ags``,
     ``VALUZ_AGS_API_KEY`` (or ``E2B_API_KEY``), ``VALUZ_AGS_DOMAIN`` (the AGS
     endpoint), ``VALUZ_AGS_KERNEL_TEMPLATE`` (the template from step 1), and a
     reachable ``VALUZ_HOST_EXTERNAL_URL`` for ④.

The e2b SDK API targeted here is **v2.x** (``timeout`` in seconds,
``envs`` kwarg, ``secure`` traffic gate, ``get_host``/``kill``/``connect``).
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Literal

import httpx

from valuz_agent.infra.config import settings
from valuz_agent.ports.object_store import ObjectStore
from valuz_agent.ports.sandbox_provider import (
    MountGrant,
    SandboxBootContext,
    SandboxBootResult,
    SandboxEndpoint,
    SandboxProvisionError,
    SandboxSpec,
)

logger = logging.getLogger("valuz_agent.sandbox")

# Sentinel: the COS object store is resolved lazily, and ``None`` is a real
# value (COS unconfigured), so we need a distinct "not yet resolved" marker.
_UNSET = object()


def _api_key() -> str | None:
    """The AGS/e2b API key — explicit setting, else the SDK's own env var."""
    return settings.ags_api_key or os.getenv("E2B_API_KEY")


def ags_preflight() -> list[str]:
    """Reasons this host can't run the AGS driver (empty = OK)."""
    problems: list[str] = []
    try:
        import e2b  # noqa: F401
    except Exception:  # noqa: BLE001 — ImportError or a broken transitive dep
        problems.append("e2b SDK not installed (pip install 'valuz-agent[ags]' or e2b)")
    if not _api_key():
        problems.append("AGS API key missing (set VALUZ_AGS_API_KEY or E2B_API_KEY)")
    if not settings.ags_kernel_template:
        problems.append("kernel template missing (set VALUZ_AGS_KERNEL_TEMPLATE)")
    return problems


# ---------------------------------------------------------------------------
# e2b SDK adapter — the ONLY place the vendor SDK is touched. Swapping SDK
# version / vendor is editing this class; the provider/driver below stay put.
# ---------------------------------------------------------------------------


class _AgsBackend:
    """Thin async wrapper over the ``e2b`` SDK, scoped to what provisioning
    needs: create-from-template, resolve the exposed port's public URL, and
    kill. Kept deliberately tiny so it tracks one SDK surface."""

    def __init__(self, handle: object, sandbox_id: str) -> None:
        self._handle = handle
        self.sandbox_id = sandbox_id

    @classmethod
    async def create(cls, *, envs: dict[str, str] | None = None) -> _AgsBackend:
        from e2b import AsyncSandbox

        # Explicit named args (not a **dict splat) so mypy validates them
        # against the e2b signature when the SDK is installed. secure=False:
        # the exposed URL is reachable directly and the kernel's own
        # KERNEL_AUTH_TOKEN gate is the authority. timeout is SECONDS in e2b v2.
        # envs=None when empty — AGS 500s on the post-create env sync, so the
        # static-token path passes no envs (rely on the tool's env).
        sbx = await AsyncSandbox.create(
            settings.ags_kernel_template,
            envs=envs or None,
            timeout=settings.ags_sandbox_timeout_s,
            secure=settings.ags_secure,
            api_key=_api_key(),
            domain=settings.ags_domain,
        )
        return cls(sbx, sbx.sandbox_id)

    @classmethod
    async def connect(cls, sandbox_id: str) -> _AgsBackend:
        from e2b import AsyncSandbox

        sbx = await AsyncSandbox.connect(
            sandbox_id, api_key=_api_key(), domain=settings.ags_domain
        )
        return cls(sbx, sandbox_id)

    def base_url(self) -> str:
        """``https://`` URL the host reaches the kernel's port on."""
        host = self._handle.get_host(settings.ags_kernel_port)  # type: ignore[attr-defined]
        return f"https://{host}"

    async def kill(self) -> None:
        await self._handle.kill()  # type: ignore[attr-defined]


class AgsSandboxProvider:
    """``SandboxProvider`` backed by an AGS / e2b-compatible sandbox running
    the kernel image."""

    def __init__(self) -> None:
        self._backends: dict[str, _AgsBackend] = {}
        self._endpoints: dict[str, SandboxEndpoint] = {}
        self._store: object = _UNSET  # resolved lazily to an ObjectStore | None
        self._grants: dict[str, str] = {}  # grant_id -> COS prefix

    @classmethod
    def from_existing(cls, sandbox_id: str, base_url: str, token: str) -> AgsSandboxProvider:
        """A provider that only knows the endpoint (reload child / lazy
        activation) — enough to drive the kernel; ``destroy`` reconnects on
        demand. Mirrors ``SeatbeltSandboxProvider.from_existing``."""
        self = cls()
        self._endpoints[sandbox_id] = SandboxEndpoint(
            sandbox_id=sandbox_id, base_url=base_url, token=token
        )
        return self

    async def provision(self, spec: SandboxSpec) -> SandboxEndpoint:
        problems = ags_preflight()
        if problems:
            raise SandboxProvisionError(
                "AgsSandboxProvider preflight failed: " + "; ".join(problems)
            )
        # Token strategy. AGS rejects the e2b ``create(envs=)`` env sync, so
        # when ``ags_kernel_token`` is set we use that STATIC token (which must
        # match the KERNEL_AUTH_TOKEN env on the sandbox tool) and pass NO envs.
        # Otherwise (dynamic mode, for e2b backends that honour it) we mint a
        # random token and inject it + ⑥ L1 credentials via create envs.
        envs: dict[str, str] | None
        if settings.ags_kernel_token:
            token = settings.ags_kernel_token
            envs = None
        else:
            token = secrets.token_urlsafe(24)
            # The cloud image self-migrates (entrypoint `alembic upgrade head`)
            # and defaults its SQLite under /app/data. KERNEL_SANDBOX_CONTROL
            # stays UNSET (macOS-only).
            envs = {"KERNEL_AUTH_TOKEN": token, **spec.env}
            if spec.host_callback_url:
                envs["CODEX_TOOLKIT_BASE_URL"] = spec.host_callback_url  # ④ callback

        try:
            backend = await _AgsBackend.create(envs=envs)
        except SandboxProvisionError:
            raise
        except Exception as exc:  # noqa: BLE001 — any SDK/transport failure
            raise SandboxProvisionError(f"AGS sandbox create failed: {exc}") from exc

        base_url = backend.base_url()
        try:
            await self._await_health(base_url, token)
        except Exception as exc:  # noqa: BLE001
            await _safe_kill(backend)
            raise SandboxProvisionError(
                f"AGS kernel did not become healthy at {base_url}: {exc}"
            ) from exc

        self._backends[spec.sandbox_id] = backend
        endpoint = SandboxEndpoint(sandbox_id=spec.sandbox_id, base_url=base_url, token=token)
        self._endpoints[spec.sandbox_id] = endpoint
        logger.info("AGS sandbox %s up: kernel at %s", backend.sandbox_id, base_url)
        return endpoint

    @staticmethod
    async def _await_health(base_url: str, token: str, *, deadline_s: float = 120.0) -> None:
        """Poll ``/health`` until the kernel answers (it must boot + migrate
        first). ``/health`` is unauthenticated; the token is unused here but
        kept for symmetry with an authed-probe future."""
        import asyncio

        loop_deadline = deadline_s
        async with httpx.AsyncClient(timeout=5.0) as c:
            waited = 0.0
            while waited < loop_deadline:
                try:
                    r = await c.get(f"{base_url}/health")
                    if r.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(2.0)
                waited += 2.0
        raise TimeoutError(f"no /health 200 within {deadline_s:.0f}s")

    async def health(self, sandbox_id: str) -> bool:
        ep = self._endpoints.get(sandbox_id)
        if ep is None:
            return False
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{ep.base_url}/health", timeout=3.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def destroy(self, sandbox_id: str) -> None:
        backend = self._backends.pop(sandbox_id, None)
        ep = self._endpoints.pop(sandbox_id, None)
        if backend is not None:
            await _safe_kill(backend)
            return
        # from_existing provider (no owned handle): reconnect by the e2b id
        # parsed from the endpoint host, then kill. Best-effort.
        if ep is not None:
            e2b_id = _sandbox_id_from_url(ep.base_url)
            if e2b_id:
                try:
                    await _safe_kill(await _AgsBackend.connect(e2b_id))
                except Exception:  # noqa: BLE001 — idempotent teardown
                    logger.warning(
                        "AGS destroy: reconnect+kill failed for %s", e2b_id, exc_info=True
                    )

    def _object_store(self) -> ObjectStore | None:
        from valuz_agent.integrations.object_store_s3 import cos_object_store

        if self._store is _UNSET:
            self._store = cos_object_store()
        return self._store  # type: ignore[return-value]

    async def bind_workspace(
        self, sandbox_id: str, host_path: str, mode: Literal["rw", "ro"] = "rw"
    ) -> MountGrant:
        """⑤ materials face — stage ``host_path`` into the cloud sandbox via COS.

        Isolation contract: the AGS sandbox tool mounts **the current user's COS
        prefix** (存储路径 = ``/<user_id>``) at ``settings.ags_mount_path``, so a
        sandbox sees only its own user's subtree and the user_id is the mount
        root — it does NOT appear in the in-sandbox path. We therefore upload to
        COS key ``<user_id>/<rel>`` but tell the kernel ``{mount_path}/<rel>``
        (user_id stripped). The kernel writes results straight back to COS via
        the mount (the mount IS the sync). If COS isn't configured we fall back
        to an empty in-sandbox dir and warn.
        """
        import hashlib

        from valuz_agent.infra.auth_context import get_current_user_id

        uid = get_current_user_id() or "local"
        real = os.path.realpath(host_path)
        digest = hashlib.sha256(real.encode()).hexdigest()[:12]
        rel = f"workspaces/{digest}"  # path WITHIN the user's mounted prefix
        cos_key = f"{uid}/{rel}"  # where it lives in COS
        kernel_cwd = f"{settings.ags_mount_path.rstrip('/')}/{rel}"  # mount root == <uid>/

        store = self._object_store()
        if store is None:
            logger.warning(
                "AGS bind_workspace: COS not configured — %s is NOT staged; the "
                "kernel runs in empty %s. Set VALUZ_COS_* to enable staging.",
                host_path,
                kernel_cwd,
            )
            return MountGrant(
                grant_id=f"ags-nostore:{digest}", kernel_cwd=kernel_cwd, host_path=real, mode=mode
            )

        from valuz_agent.integrations.object_store_s3 import stage_directory

        n, total = await stage_directory(
            store,
            cos_key,
            real,
            max_files=settings.ags_stage_max_files,
            max_bytes=settings.ags_stage_max_bytes,
        )
        logger.info(
            "AGS bind_workspace: staged %d files (%d bytes) of %s → COS %s (kernel cwd %s)",
            n,
            total,
            real,
            cos_key,
            kernel_cwd,
        )
        self._grants[f"cos:{cos_key}"] = cos_key
        return MountGrant(
            grant_id=f"cos:{cos_key}", kernel_cwd=kernel_cwd, host_path=real, mode=mode
        )

    async def unbind_workspace(self, sandbox_id: str, grant_id: str) -> None:
        # Deliberately does NOT delete the COS prefix: results the kernel wrote
        # there may not be read back yet (the host reads COS on demand). Staged
        # data is reclaimed by a separate lifecycle/TTL policy, not here.
        return None


async def _safe_kill(backend: _AgsBackend) -> None:
    try:
        await backend.kill()
    except Exception:  # noqa: BLE001 — destroy is idempotent
        logger.warning("AGS kill failed for %s", backend.sandbox_id, exc_info=True)


def _sandbox_id_from_url(base_url: str) -> str | None:
    """Recover the e2b sandbox id from an exposed-port host of the shape
    ``https://<port>-<sandboxid>.<domain>``. Returns None if it doesn't match."""
    try:
        host = base_url.split("://", 1)[1].split("/", 1)[0]
        first_label = host.split(".", 1)[0]  # "<port>-<sandboxid>"
        parts = first_label.split("-", 1)
        return parts[1] if len(parts) == 2 and parts[1] else None
    except (IndexError, ValueError):
        return None


class AgsSandboxDriver:
    """The registrable ``ags`` driver — boot wiring around ``AgsSandboxProvider``."""

    name = "ags"

    def preflight(self) -> list[str]:
        return ags_preflight()

    async def provision_for_boot(self, ctx: SandboxBootContext) -> SandboxBootResult:
        spec = SandboxSpec(
            sandbox_id="host-kernel",
            # Cloud kernel uses its in-image SQLite default; the host path is
            # meaningless in the sandbox. Kept informational.
            kernel_db_path="/app/data/kernel.db",
            env=ctx.passthrough_env,  # ⑥ L1 credential injection
            host_callback_url=ctx.host_callback_url,  # ④ (reachable host only)
        )
        provider = AgsSandboxProvider()
        endpoint = await provider.provision(spec)
        # static_roots empty: NO host path is reachable in the cloud sandbox
        # without staging, so every session cwd must go through bind_workspace
        # (P3). Until P3, bind_workspace returns an empty in-sandbox root.
        return SandboxBootResult(endpoint=endpoint, provider=provider, static_roots=())

    def attach(self, ctx: SandboxBootContext, endpoint: SandboxEndpoint) -> SandboxBootResult:
        provider = AgsSandboxProvider.from_existing(
            "host-kernel", endpoint.base_url, endpoint.token
        )
        return SandboxBootResult(endpoint=endpoint, provider=provider, static_roots=())
